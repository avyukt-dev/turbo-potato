"""Google Gen AI SDK adapter for provider-neutral text tasks."""

from __future__ import annotations

import json
from collections.abc import Callable
from time import monotonic
from typing import Any

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    AIInvalidResponseError,
    AIProviderAuthenticationError,
    AIProviderError,
    AIProviderPolicyError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)


class GeminiProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: str = Field(default="gemini", pattern=r"^[a-z][a-z0-9_-]*$")
    credential_pool_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    models: tuple[ProviderModelConfig, ...]
    request_timeout_seconds: float = Field(default=120.0, gt=0, le=600)

    @model_validator(mode="after")
    def require_models(self) -> GeminiProviderConfig:
        ids = [item.model_id for item in self.models]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Gemini models must be non-empty and unique")
        return self


class GeminiProvider(CredentialPooledProvider):
    def __init__(
        self,
        config: GeminiProviderConfig,
        credential_pool: CredentialPool,
        *,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.config = config
        self.credential_pool = credential_pool
        self._models = {item.model_id: item for item in config.models}
        self._client_factory = client_factory or (
            lambda key: genai.Client(
                api_key=key,
                http_options=types.HttpOptions(timeout=int(config.request_timeout_seconds * 1000)),
            )
        )
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
            honors_reasoning_effort=False,
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

    async def close(self) -> None:
        return None

    async def execute(self, request: AIRequest) -> AIResponse:
        return await self._execute_from_pool(request)

    async def _execute_with_credential(self, request: AIRequest, api_key: str) -> AIResponse:
        model = request.model or next(iter(self._models))
        model_config = self._models.get(model)
        if model_config is None:
            raise AIProviderError("Gemini model is not configured")
        config_args: dict[str, Any] = {
            "system_instruction": request.system_prompt,
            "temperature": request.temperature,
            "max_output_tokens": request.max_tokens or model_config.default_max_completion_tokens,
        }
        if request.response_format is AIResponseFormat.STRUCTURED:
            config_args["response_mime_type"] = "application/json"
            if request.response_schema is not None:
                config_args["response_json_schema"] = request.response_schema.json_schema
        started = monotonic()
        try:
            response = await self._client_factory(api_key).aio.models.generate_content(
                model=model,
                contents=self._serialize(request.input),
                config=types.GenerateContentConfig(**config_args),
            )
        except errors.APIError as exc:
            if exc.code == 401:
                raise AIProviderAuthenticationError("Gemini credential was rejected") from exc
            if exc.code == 403:
                raise AIProviderPolicyError("Gemini rejected the request policy") from exc
            if exc.code == 429:
                raise AIProviderRateLimitError("Gemini request was rate limited") from exc
            if exc.code in {408, 504}:
                raise AIProviderTimeoutError("Gemini request timed out") from exc
            if exc.code >= 500:
                raise AIProviderUnavailableError("Gemini service is unavailable") from exc
            raise AIProviderError("Gemini rejected the request") from exc
        except TimeoutError as exc:
            raise AIProviderTimeoutError("Gemini request timed out") from exc
        except OSError as exc:
            raise AIProviderUnavailableError("Gemini endpoint is unavailable") from exc
        text = response.text
        if not isinstance(text, str) or not text:
            raise AIInvalidResponseError("Gemini response failed normalization")
        try:
            structured = (
                json.loads(text) if request.response_format is AIResponseFormat.STRUCTURED else None
            )
        except json.JSONDecodeError:
            raise AIInvalidResponseError("Gemini response failed normalization") from None
        if structured is not None and not isinstance(structured, dict):
            raise AIInvalidResponseError("Gemini structured response must be an object")
        usage = getattr(response, "usage_metadata", None)
        return AIResponse(
            text=None if structured is not None else text,
            structured=structured,
            provider=self.provider_id,
            model=model,
            usage=TokenUsage(
                input_tokens=getattr(usage, "prompt_token_count", None),
                output_tokens=getattr(usage, "candidates_token_count", None),
            ),
            latency_ms=max(0, round((monotonic() - started) * 1000)),
            finish_reason=None,
            provider_request_id=getattr(response, "response_id", None),
        )

    @staticmethod
    def _serialize(value: Any) -> str:
        return (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
