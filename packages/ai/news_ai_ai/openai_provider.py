"""Official OpenAI SDK adapter for provider-neutral text tasks."""

from __future__ import annotations

import json
from collections.abc import Callable
from time import monotonic
from typing import Any

import openai
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    AIRequest,
    AIResponse,
    AIResponseFormat,
    ProviderCapabilities,
    ProviderLocality,
    TokenUsage,
)
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


class OpenAIProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: str = Field(default="openai", pattern=r"^[a-z][a-z0-9_-]*$")
    base_url: AnyHttpUrl = Field(default="https://api.openai.com/v1", validate_default=True)
    credential_pool_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    models: tuple[ProviderModelConfig, ...]
    request_timeout_seconds: float = Field(default=120.0, gt=0, le=600)

    @model_validator(mode="after")
    def validate_configuration(self) -> OpenAIProviderConfig:
        if self.base_url.scheme != "https" or self.base_url.username or self.base_url.password:
            raise ValueError("OpenAI base_url must be credential-free HTTPS")
        if self.base_url.query is not None or self.base_url.fragment is not None:
            raise ValueError("OpenAI base_url must not contain query or fragment")
        ids = [item.model_id for item in self.models]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("OpenAI models must be non-empty and unique")
        return self


class OpenAIProvider(CredentialPooledProvider):
    def __init__(
        self,
        config: OpenAIProviderConfig,
        credential_pool: CredentialPool,
        *,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.config = config
        self.credential_pool = credential_pool
        self._models = {item.model_id: item for item in config.models}
        self._client_factory = client_factory or self._make_client
        self._capabilities = ProviderCapabilities(
            provider_id=config.provider_id,
            locality=ProviderLocality.CLOUD,
            task_types=frozenset(task for item in config.models for task in item.task_types),
            response_formats=frozenset(
                fmt for item in config.models for fmt in item.response_formats
            ),
            models=frozenset(self._models),
            model_task_types={key: value.task_types for key, value in self._models.items()},
            model_response_formats={
                key: value.response_formats for key, value in self._models.items()
            },
            supports_vision=any(item.supports_vision for item in config.models),
            supports_tools=any(item.supports_tools for item in config.models),
            honors_reasoning_effort=any(item.honors_reasoning_effort for item in config.models),
            max_context_tokens=max(
                (item.max_context_tokens or 0 for item in config.models), default=0
            )
            or None,
        )

    @property
    def provider_id(self) -> str:
        return self.config.provider_id

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def _make_client(self, api_key: str) -> openai.AsyncOpenAI:
        return openai.AsyncOpenAI(
            api_key=api_key,
            base_url=str(self.config.base_url).rstrip("/"),
            timeout=self.config.request_timeout_seconds,
            max_retries=0,
        )

    async def close(self) -> None:
        return None

    async def execute(self, request: AIRequest) -> AIResponse:
        return await self._execute_from_pool(request)

    async def _execute_with_credential(self, request: AIRequest, api_key: str) -> AIResponse:
        model = request.model or next(iter(self._models))
        model_config = self._models.get(model)
        if model_config is None:
            raise AIProviderError("OpenAI model is not configured")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": self._serialize(request.input)},
            ],
            "temperature": request.temperature,
            "max_completion_tokens": request.max_tokens
            or model_config.default_max_completion_tokens,
        }
        if request.response_format is AIResponseFormat.STRUCTURED:
            payload["response_format"] = (
                {"type": "json_object"}
                if request.response_schema is None
                else {
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.response_schema.name,
                        "strict": request.response_schema.strict,
                        "schema": request.response_schema.json_schema,
                    },
                }
            )
        started = monotonic()
        try:
            completion = await self._client_factory(api_key).chat.completions.create(**payload)
        except openai.AuthenticationError as exc:
            raise AIProviderAuthenticationError("OpenAI credential was rejected") from exc
        except openai.PermissionDeniedError as exc:
            raise AIProviderPolicyError("OpenAI rejected the request policy") from exc
        except openai.RateLimitError as exc:
            raise AIProviderRateLimitError(
                "OpenAI request was rate limited", retry_after_seconds=self._retry_after(exc)
            ) from exc
        except openai.APITimeoutError as exc:
            raise AIProviderTimeoutError("OpenAI request timed out") from exc
        except openai.APIConnectionError as exc:
            raise AIProviderUnavailableError("OpenAI endpoint is unavailable") from exc
        except openai.APIStatusError as exc:
            if exc.status_code == 413:
                raise AIContextTooLargeError("OpenAI request exceeded the context size") from exc
            if exc.status_code >= 500:
                raise AIProviderUnavailableError("OpenAI service is unavailable") from exc
            raise AIProviderError("OpenAI rejected the request") from exc
        choice = completion.choices[0] if completion.choices else None
        content = choice.message.content if choice is not None else None
        if not isinstance(content, str):
            raise AIInvalidResponseError("OpenAI response failed normalization")
        try:
            structured = (
                json.loads(content)
                if request.response_format is AIResponseFormat.STRUCTURED
                else None
            )
        except json.JSONDecodeError:
            raise AIInvalidResponseError("OpenAI response failed normalization") from None
        if structured is not None and not isinstance(structured, dict):
            raise AIInvalidResponseError("OpenAI structured response must be an object")
        usage = completion.usage
        return AIResponse(
            text=None if structured is not None else content,
            structured=structured,
            provider=self.provider_id,
            model=completion.model,
            usage=TokenUsage(
                input_tokens=getattr(usage, "prompt_tokens", None),
                output_tokens=getattr(usage, "completion_tokens", None),
            ),
            latency_ms=max(0, round((monotonic() - started) * 1000)),
            finish_reason=choice.finish_reason,
            provider_request_id=completion.id,
        )

    @staticmethod
    def _serialize(value: Any) -> str:
        return (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )

    def _retry_after(self, error: openai.RateLimitError) -> float | None:
        value = error.response.headers.get("retry-after") if error.response is not None else None
        try:
            parsed = float(value) if value is not None else None
        except ValueError:
            return None
        return (
            min(parsed, self.credential_pool.config.max_retry_after_seconds)
            if parsed is not None and parsed >= 0
            else None
        )
