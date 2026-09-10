"""Local llama.cpp HTTP provider adapter."""

from __future__ import annotations

import json
from time import monotonic
from typing import Any

import httpx
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import (
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AITaskType,
    ProviderCapabilities,
    ProviderLocality,
    TokenUsage,
)
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
    """Configuration for one local llama.cpp server endpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(default="local-llama", pattern=r"^[a-z][a-z0-9_-]*$")
    base_url: AnyHttpUrl
    model: str = Field(min_length=1, max_length=255)
    task_types: frozenset[AITaskType]
    request_timeout_seconds: float = Field(default=120.0, gt=0.0, le=600.0)
    health_timeout_seconds: float = Field(default=2.0, gt=0.0, le=30.0)
    max_context_tokens: int | None = Field(default=None, ge=1)
    api_key_env: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Z_][A-Z0-9_]*$",
    )

    @field_validator("model")
    @classmethod
    def strip_model(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("llama.cpp model must not be blank")
        return stripped

    @model_validator(mode="after")
    def require_tasks(self) -> LlamaCppProviderConfig:
        if not self.task_types:
            raise ValueError("llama.cpp provider must declare at least one task")
        if self.base_url.query is not None or self.base_url.fragment is not None:
            raise ValueError("llama.cpp base_url must not contain query or fragment components")
        return self


class LlamaCppProvider:
    """Provider-neutral adapter for a configured local llama.cpp HTTP server."""

    def __init__(
        self,
        config: LlamaCppProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
    ) -> None:
        self.config = config
        self._base_url = str(config.base_url).rstrip("/")
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._api_key = api_key
        self._capabilities = ProviderCapabilities(
            provider_id=config.provider_id,
            locality=ProviderLocality.LOCAL,
            task_types=config.task_types,
            response_formats=frozenset({AIResponseFormat.TEXT, AIResponseFormat.STRUCTURED}),
            models=frozenset({config.model}),
            supports_vision=False,
            supports_tools=False,
            max_context_tokens=config.max_context_tokens,
        )

    @property
    def provider_id(self) -> str:
        return self.config.provider_id

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def healthcheck(self) -> bool:
        """Return whether the configured llama.cpp endpoint reports ready."""

        try:
            response = await self._client.get(
                f"{self._base_url}/health",
                headers=self._headers(),
                timeout=self.config.health_timeout_seconds,
            )
        except httpx.RequestError:
            return False
        if response.status_code != 200:
            return False
        try:
            payload = response.json()
        except ValueError:
            return False
        return isinstance(payload, dict) and payload.get("status") == "ok"

    async def execute(self, request: AIRequest) -> AIResponse:
        """Execute a non-streaming OpenAI-compatible chat completion."""

        selected_model = request.model or self.config.model
        payload = self._request_payload(request, selected_model)
        started_at = monotonic()
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=request.timeout_seconds or self.config.request_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError("llama.cpp request timed out") from exc
        except httpx.RequestError as exc:
            raise AIProviderUnavailableError("llama.cpp endpoint is unavailable") from exc

        latency_ms = max(0, round((monotonic() - started_at) * 1000))
        self._raise_for_status(response)
        body = self._decode_response(response)
        return self._normalize_response(request, selected_model, body, latency_ms)

    def _headers(self) -> dict[str, str]:
        if self._api_key is None:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}

    @staticmethod
    def _serialize_input(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise AIProviderError("AI request input is not JSON serializable") from exc

    def _request_payload(self, request: AIRequest, model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": self._serialize_input(request.input)},
            ],
            "temperature": request.temperature,
            "stream": False,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.response_format is AIResponseFormat.STRUCTURED:
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 429:
            raise AIProviderRateLimitError("llama.cpp request was rate limited")
        if status in {401, 403}:
            raise AIProviderPolicyError("llama.cpp request was not authorized")
        if status == 413:
            raise AIContextTooLargeError("llama.cpp request exceeded the accepted context size")
        if status >= 500:
            raise AIProviderUnavailableError(f"llama.cpp server returned HTTP {status}")
        raise AIProviderError(f"llama.cpp rejected request with HTTP {status}")

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise AIInvalidResponseError("llama.cpp returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise AIInvalidResponseError("llama.cpp response must be a JSON object")
        return body

    def _normalize_response(
        self,
        request: AIRequest,
        selected_model: str,
        body: dict[str, Any],
        latency_ms: int,
    ) -> AIResponse:
        content, finish_reason = self._extract_choice(body)
        structured: dict[str, Any] | None = None
        text: str | None = content
        if request.response_format is AIResponseFormat.STRUCTURED:
            structured = self._parse_structured(content)
            text = None

        usage = body.get("usage")
        usage_mapping = usage if isinstance(usage, dict) else {}
        response_model = body.get("model")
        if not isinstance(response_model, str) or not response_model.strip():
            response_model = selected_model
        request_id = body.get("id")
        provider_request_id = request_id if isinstance(request_id, str) else None
        fingerprint = body.get("system_fingerprint")
        metadata = {"system_fingerprint": fingerprint} if isinstance(fingerprint, str) else {}

        return AIResponse(
            text=text,
            structured=structured,
            provider=self.provider_id,
            model=response_model,
            usage=TokenUsage(
                input_tokens=self._optional_non_negative_int(usage_mapping.get("prompt_tokens")),
                output_tokens=self._optional_non_negative_int(
                    usage_mapping.get("completion_tokens")
                ),
            ),
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            provider_request_id=provider_request_id,
            metadata=metadata,
        )

    @staticmethod
    def _extract_choice(body: dict[str, Any]) -> tuple[str, str | None]:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise AIInvalidResponseError("llama.cpp response contains no completion choice")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise AIInvalidResponseError("llama.cpp response contains no message content")
        content = message["content"]
        finish = choice.get("finish_reason")
        finish_reason = finish if isinstance(finish, str) else None
        return content, finish_reason

    @staticmethod
    def _parse_structured(content: str) -> dict[str, Any]:
        try:
            structured = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AIInvalidResponseError("llama.cpp returned malformed structured output") from exc
        if not isinstance(structured, dict):
            raise AIInvalidResponseError("llama.cpp structured output must be a JSON object")
        return structured

    @staticmethod
    def _optional_non_negative_int(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value
