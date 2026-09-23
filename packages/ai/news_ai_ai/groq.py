"""Groq SDK adapter with per-key credential-state failover."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from time import monotonic
from typing import Any

import groq
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AITaskType,
    ProviderCapabilities,
    ProviderLocality,
    TokenUsage,
)
from .credentials import CredentialPoolConfig, MemoryCredentialPool, ResolvedCredential
from .model_config import ProviderModelConfig
from .pooled import CredentialPool, CredentialPooledProvider
from .provider import (
    AIContextTooLargeError,
    AIInvalidResponseError,
    AIProviderAuthenticationError,
    AIProviderError,
    AIProviderPolicyError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)
from .sdk_clients import close_sdk_client


def _strict_json_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_strict_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {
        key: _strict_json_schema(item)
        for key, item in value.items()
        if key not in {"default", "pattern"}
    }
    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["required"] = list(properties)
        normalized["additionalProperties"] = False
    return normalized


def _legacy_model() -> ProviderModelConfig:
    return ProviderModelConfig(
        model_id="openai/gpt-oss-120b",
        task_types=frozenset(AITaskType),
        max_context_tokens=131072,
        default_max_completion_tokens=8192,
        honors_reasoning_effort=True,
    )


class GroqProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: str = Field(default="groq", pattern=r"^[a-z][a-z0-9_-]*$")
    base_url: AnyHttpUrl = Field(default="https://api.groq.com/openai/v1", validate_default=True)
    credential_pool_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")
    models: tuple[ProviderModelConfig, ...] = ()
    request_timeout_seconds: float = Field(default=120.0, gt=0.0, le=600.0)
    model: str | None = Field(default=None, min_length=1, max_length=255, exclude=True)
    task_types: frozenset[AITaskType] = Field(default_factory=frozenset, exclude=True)
    max_context_tokens: int | None = Field(default=None, ge=1, exclude=True)
    default_max_completion_tokens: int | None = Field(default=None, ge=1, exclude=True)

    @model_validator(mode="after")
    def validate_configuration(self) -> GroqProviderConfig:
        if self.base_url.scheme != "https":
            raise ValueError("Groq base_url must use HTTPS")
        if self.base_url.username is not None or self.base_url.password is not None:
            raise ValueError("Groq base_url must not contain credentials")
        if self.base_url.query is not None or self.base_url.fragment is not None:
            raise ValueError("Groq base_url must not contain query or fragment components")
        ids = [item.model_id for item in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("Groq model IDs must be unique")
        return self

    @property
    def configured_models(self) -> tuple[ProviderModelConfig, ...]:
        if self.models:
            return self.models
        legacy = _legacy_model()
        return (
            legacy.model_copy(
                update={
                    "model_id": self.model or legacy.model_id,
                    "task_types": self.task_types or legacy.task_types,
                    "max_context_tokens": self.max_context_tokens or legacy.max_context_tokens,
                    "default_max_completion_tokens": self.default_max_completion_tokens
                    or legacy.default_max_completion_tokens,
                }
            ),
        )


class GroqProvider(CredentialPooledProvider):
    def __init__(
        self,
        config: GroqProviderConfig,
        *,
        credential_pool: CredentialPool | None = None,
        api_key: str | None = None,
        api_keys: Iterable[str] | None = None,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        if credential_pool is not None and (api_key is not None or api_keys is not None):
            raise ValueError("configure Groq credentials through one constructor input")
        if credential_pool is None:
            supplied = (api_key,) if api_keys is None else tuple(api_keys)
            values = tuple(
                dict.fromkeys(item.strip() for item in supplied if item and item.strip())
            )
            if not values:
                raise ValueError("at least one configured Groq credential is required")
            pool_config = CredentialPoolConfig(
                pool_id=config.credential_pool_id or "groq-memory",
                provider="groq",
                env_prefix="GROQ_API_KEY",
            )
            credentials = tuple(
                ResolvedCredential(
                    pool_id=pool_config.pool_id,
                    provider_type="groq",
                    slot=index,
                    fingerprint=f"memory-{index}",
                    secret=value,
                )
                for index, value in enumerate(values, start=1)
            )
            credential_pool = MemoryCredentialPool(pool_config, credentials)
        self.config = config
        self.credential_pool = credential_pool
        self._models = {item.model_id: item for item in config.configured_models}
        self._client_factory = client_factory or self._make_client
        task_types = frozenset(task for item in self._models.values() for task in item.task_types)
        formats = frozenset(
            value for item in self._models.values() for value in item.response_formats
        )
        self._capabilities = ProviderCapabilities(
            provider_id=config.provider_id,
            locality=ProviderLocality.CLOUD,
            task_types=task_types,
            response_formats=formats,
            models=frozenset(self._models),
            model_task_types={key: value.task_types for key, value in self._models.items()},
            model_response_formats={
                key: value.response_formats for key, value in self._models.items()
            },
            supports_vision=any(item.supports_vision for item in self._models.values()),
            supports_tools=any(item.supports_tools for item in self._models.values()),
            honors_reasoning_effort=any(
                item.honors_reasoning_effort for item in self._models.values()
            ),
            max_context_tokens=max(
                (item.max_context_tokens or 0 for item in self._models.values()), default=0
            )
            or None,
        )

    @property
    def provider_id(self) -> str:
        return self.config.provider_id

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def close(self) -> None:
        return None

    def _make_client(self, api_key: str) -> groq.AsyncGroq:
        return groq.AsyncGroq(
            api_key=api_key,
            base_url=str(self.config.base_url).rstrip("/"),
            timeout=self.config.request_timeout_seconds,
            max_retries=0,
        )

    async def execute(self, request: AIRequest) -> AIResponse:
        return await self._execute_from_pool(request)

    async def _execute_with_credential(self, request: AIRequest, api_key: str) -> AIResponse:
        model = request.model or next(iter(self._models))
        model_config = self._models.get(model)
        if model_config is None:
            raise AIProviderError("Groq model is not configured")
        started = monotonic()
        client = self._client_factory(api_key)
        try:
            completion = await client.chat.completions.create(
                **self._request_payload(request, model, model_config)
            )
        except groq.AuthenticationError as exc:
            raise AIProviderAuthenticationError("Groq credential was rejected") from exc
        except groq.PermissionDeniedError as exc:
            raise AIProviderPolicyError("Groq rejected the request policy") from exc
        except groq.RateLimitError as exc:
            raise AIProviderRateLimitError(
                "Groq request was rate limited", retry_after_seconds=self._retry_after(exc)
            ) from exc
        except groq.APITimeoutError as exc:
            raise AIProviderTimeoutError("Groq request timed out") from exc
        except groq.APIConnectionError as exc:
            raise AIProviderUnavailableError("Groq endpoint is unavailable") from exc
        except groq.APIStatusError as exc:
            if exc.status_code == 413:
                raise AIContextTooLargeError("Groq request exceeded the context size") from exc
            if exc.status_code >= 500:
                raise AIProviderUnavailableError("Groq service is unavailable") from exc
            raise AIProviderError("Groq rejected the request") from exc
        except groq.APIError as exc:
            raise AIProviderError("Groq request failed") from exc
        finally:
            await close_sdk_client(client)
        return self._normalize_response(
            request, completion, max(0, round((monotonic() - started) * 1000))
        )

    def _request_payload(
        self, request: AIRequest, model: str, model_config: ProviderModelConfig
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": self._serialize_input(request.input)},
            ],
            "temperature": request.temperature,
            "stream": False,
            "include_reasoning": False,
            "max_completion_tokens": request.max_tokens
            or model_config.default_max_completion_tokens,
        }
        if request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort.value
        if request.response_format is AIResponseFormat.STRUCTURED:
            payload["response_format"] = (
                {"type": "json_object"}
                if request.response_schema is None
                else {
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.response_schema.name,
                        "strict": request.response_schema.strict,
                        "schema": _strict_json_schema(request.response_schema.json_schema),
                    },
                }
            )
        return payload

    @staticmethod
    def _serialize_input(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise AIProviderError("AI request input is not JSON serializable") from exc

    def _normalize_response(
        self, request: AIRequest, completion: Any, latency_ms: int
    ) -> AIResponse:
        try:
            choice = completion.choices[0]
            content = choice.message.content
            if not isinstance(content, str):
                raise ValueError
            structured = None
            text = content
            if request.response_format is AIResponseFormat.STRUCTURED:
                structured = json.loads(content)
                if not isinstance(structured, dict):
                    raise ValueError
                text = None
            usage = getattr(completion, "usage", None)
            return AIResponse(
                text=text,
                structured=structured,
                provider=self.provider_id,
                model=completion.model,
                usage=TokenUsage(
                    input_tokens=getattr(usage, "prompt_tokens", None),
                    output_tokens=getattr(usage, "completion_tokens", None),
                ),
                latency_ms=latency_ms,
                finish_reason=choice.finish_reason,
                provider_request_id=completion.id,
                metadata={"system_fingerprint": completion.system_fingerprint}
                if getattr(completion, "system_fingerprint", None)
                else {},
            )
        except (AttributeError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            raise AIInvalidResponseError("Groq response failed normalization") from None

    def _retry_after(self, error: groq.RateLimitError) -> float | None:
        value = error.response.headers.get("retry-after") if error.response is not None else None
        try:
            parsed = float(value) if value is not None else None
        except ValueError:
            return None
        if parsed is None or parsed < 0:
            return None
        return min(parsed, self.credential_pool.config.max_retry_after_seconds)
