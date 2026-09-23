"""OpenAI-compatible SDK adapter for a local llama.cpp server."""

from __future__ import annotations

import json
from time import monotonic
from typing import Any

import httpx
import openai
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
from .model_config import ProviderModelConfig
from .provider import (
    AIContextTooLargeError,
    AIInvalidResponseError,
    AIProviderError,
    AIProviderPolicyError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)


class LlamaCppProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: str = Field(default="local-llama", pattern=r"^[a-z][a-z0-9_-]*$")
    base_url: AnyHttpUrl
    models: tuple[ProviderModelConfig, ...] = ()
    request_timeout_seconds: float = Field(default=120.0, gt=0.0, le=600.0)
    health_timeout_seconds: float = Field(default=2.0, gt=0.0, le=30.0)
    credential_pool_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")
    model: str | None = Field(default=None, min_length=1, max_length=255, exclude=True)
    task_types: frozenset[AITaskType] = Field(default_factory=frozenset, exclude=True)
    max_context_tokens: int | None = Field(default=None, ge=1, exclude=True)
    api_key_env: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_configuration(self) -> LlamaCppProviderConfig:
        if self.base_url.query is not None or self.base_url.fragment is not None:
            raise ValueError("llama.cpp base_url must not contain query or fragment")
        ids = [item.model_id for item in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("llama.cpp model IDs must be unique")
        if self.model is not None and not self.models and not self.task_types:
            raise ValueError("llama.cpp requires at least one task")
        return self

    @property
    def configured_models(self) -> tuple[ProviderModelConfig, ...]:
        if self.models:
            return self.models
        return (
            ProviderModelConfig(
                model_id=self.model or "local-news-ai",
                task_types=self.task_types or frozenset(AITaskType),
                max_context_tokens=self.max_context_tokens,
                default_max_completion_tokens=4096,
            ),
        )


class LlamaCppProvider:
    def __init__(
        self,
        config: LlamaCppProviderConfig,
        *,
        api_key: str | None = None,
        client: openai.AsyncOpenAI | None = None,
        health_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._base_url = str(config.base_url).rstrip("/")
        self._client = client or openai.AsyncOpenAI(
            api_key=api_key or "local-no-auth",
            base_url=f"{self._base_url}/v1",
            timeout=config.request_timeout_seconds,
            max_retries=0,
        )
        self._owns_client = client is None
        self._health_client = health_client
        self._owns_health_client = health_client is None
        self._models = {item.model_id: item for item in config.configured_models}
        self._capabilities = ProviderCapabilities(
            provider_id=config.provider_id,
            locality=ProviderLocality.LOCAL,
            task_types=frozenset(
                task for item in self._models.values() for task in item.task_types
            ),
            response_formats=frozenset(
                fmt for item in self._models.values() for fmt in item.response_formats
            ),
            models=frozenset(self._models),
            model_task_types={key: value.task_types for key, value in self._models.items()},
            model_response_formats={
                key: value.response_formats for key, value in self._models.items()
            },
            supports_vision=any(item.supports_vision for item in self._models.values()),
            supports_tools=any(item.supports_tools for item in self._models.values()),
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
        if self._owns_client:
            await self._client.close()
        if self._owns_health_client and self._health_client is not None:
            await self._health_client.aclose()

    async def healthcheck(self) -> bool:
        if self._health_client is None:
            self._health_client = httpx.AsyncClient()
        try:
            response = await self._health_client.get(
                f"{self._base_url}/health", timeout=self.config.health_timeout_seconds
            )
            return response.status_code == 200 and response.json().get("status") == "ok"
        except (httpx.RequestError, ValueError, AttributeError):
            return False

    async def execute(self, request: AIRequest) -> AIResponse:
        model = request.model or next(iter(self._models))
        model_config = self._models.get(model)
        if model_config is None:
            raise AIProviderError("llama.cpp model is not configured")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": self._serialize(request.input)},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens or model_config.default_max_completion_tokens,
        }
        if request.response_format is AIResponseFormat.STRUCTURED:
            payload["response_format"] = {"type": "json_object"}
        started = monotonic()
        try:
            completion = await self._client.chat.completions.create(**payload)
        except openai.PermissionDeniedError as exc:
            raise AIProviderPolicyError("llama.cpp rejected the request policy") from exc
        except openai.RateLimitError as exc:
            raise AIProviderRateLimitError("llama.cpp request was rate limited") from exc
        except openai.APITimeoutError as exc:
            raise AIProviderTimeoutError("llama.cpp request timed out") from exc
        except openai.APIConnectionError as exc:
            raise AIProviderUnavailableError("llama.cpp endpoint is unavailable") from exc
        except openai.APIStatusError as exc:
            if exc.status_code == 413:
                raise AIContextTooLargeError("llama.cpp request exceeded context") from exc
            if exc.status_code >= 500:
                raise AIProviderUnavailableError("llama.cpp service is unavailable") from exc
            raise AIProviderError("llama.cpp rejected the request") from exc
        choice = completion.choices[0] if completion.choices else None
        content = choice.message.content if choice else None
        if not isinstance(content, str):
            raise AIInvalidResponseError("llama.cpp response failed normalization")
        try:
            structured = (
                json.loads(content)
                if request.response_format is AIResponseFormat.STRUCTURED
                else None
            )
        except json.JSONDecodeError:
            raise AIInvalidResponseError("llama.cpp response failed normalization") from None
        if structured is not None and not isinstance(structured, dict):
            raise AIInvalidResponseError("llama.cpp structured response must be an object")
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
