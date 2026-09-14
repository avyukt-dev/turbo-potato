"""Groq chat-completions adapter for the configured GPT-OSS model."""

from __future__ import annotations

import json
from time import monotonic
from typing import Any, Literal

import httpx
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

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


class GroqProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(default="groq", pattern=r"^[a-z][a-z0-9_-]*$")
    base_url: AnyHttpUrl = Field(default="https://api.groq.com/openai/v1", validate_default=True)
    model: str = Field(default="openai/gpt-oss-120b", min_length=1, max_length=255)
    task_types: frozenset[AITaskType]
    request_timeout_seconds: float = Field(default=120.0, gt=0.0, le=600.0)
    max_context_tokens: int = Field(default=131072, ge=1)
    default_max_completion_tokens: int = Field(default=8192, ge=1)
    api_key_env: Literal["GROQ_API_KEY"] = "GROQ_API_KEY"

    @field_validator("model")
    @classmethod
    def strip_model(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Groq model must not be blank")
        return stripped

    @model_validator(mode="after")
    def require_safe_configuration(self) -> GroqProviderConfig:
        if not self.task_types:
            raise ValueError("Groq provider must declare at least one task")
        if self.base_url.scheme != "https":
            raise ValueError("Groq base_url must use HTTPS")
        if self.base_url.username is not None or self.base_url.password is not None:
            raise ValueError("Groq base_url must not contain credentials")
        if self.base_url.query is not None or self.base_url.fragment is not None:
            raise ValueError("Groq base_url must not contain query or fragment components")
        return self


class GroqProvider:
    """One-request Groq adapter; fallback and retries remain router/worker concerns."""

    def __init__(
        self,
        config: GroqProviderConfig,
        *,
        api_key: str | None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if api_key is None or not api_key.strip():
            raise ValueError("GROQ_API_KEY is required for the configured Groq provider")
        self.config = config
        self._api_key = api_key.strip()
        self._base_url = str(config.base_url).rstrip("/")
        self._client = client
        self._owns_client = client is None
        self._capabilities = ProviderCapabilities(
            provider_id=config.provider_id,
            locality=ProviderLocality.CLOUD,
            task_types=config.task_types,
            response_formats=frozenset({AIResponseFormat.TEXT, AIResponseFormat.STRUCTURED}),
            models=frozenset({config.model}),
            supports_vision=False,
            supports_tools=False,
            honors_reasoning_effort=True,
            max_context_tokens=config.max_context_tokens,
        )

    @property
    def provider_id(self) -> str:
        return self.config.provider_id

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    async def execute(self, request: AIRequest) -> AIResponse:
        selected_model = request.model or self.config.model
        started_at = monotonic()
        try:
            response = await self._http_client().post(
                f"{self._base_url}/chat/completions",
                json=self._request_payload(request, selected_model),
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=request.timeout_seconds or self.config.request_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError("Groq request timed out") from exc
        except httpx.RequestError as exc:
            raise AIProviderUnavailableError("Groq endpoint is unavailable") from exc
        latency_ms = max(0, round((monotonic() - started_at) * 1000))
        self._raise_for_status(response)
        try:
            return self._normalize_response(request, self._decode_response(response), latency_ms)
        except ValidationError:
            raise AIInvalidResponseError("Groq response failed normalization") from None

    def _request_payload(self, request: AIRequest, model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": self._serialize_input(request.input)},
            ],
            "temperature": request.temperature,
            "stream": False,
            "include_reasoning": False,
            "max_completion_tokens": (
                request.max_tokens
                if request.max_tokens is not None
                else self.config.default_max_completion_tokens
            ),
        }
        if request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort.value
        if request.response_format is AIResponseFormat.STRUCTURED:
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _serialize_input(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise AIProviderError("AI request input is not JSON serializable") from exc

    @staticmethod
    def _error_fields(response: httpx.Response) -> tuple[str | None, str | None]:
        try:
            body = response.json()
        except ValueError:
            return None, None

        if not isinstance(body, dict):
            return None, None

        error = body.get("error")
        if not isinstance(error, dict):
            return None, None

        error_type = error.get("type")
        code = error.get("code")
        return (
            error_type if isinstance(error_type, str) else None,
            code if isinstance(code, str) else None,
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return

        if status == 429:
            raise AIProviderRateLimitError("Groq request was rate limited")
        if status in {401, 403}:
            raise AIProviderPolicyError("Groq request was not authorized")
        if status == 413:
            error_type, error_code = GroqProvider._error_fields(response)
            if error_code == "rate_limit_exceeded" or (
                error_code is None and error_type == "rate_limit_exceeded"
            ):
                raise AIProviderRateLimitError("Groq request was rate limited")
            raise AIContextTooLargeError("Groq request exceeded the accepted context size")
        if status >= 500:
            raise AIProviderUnavailableError(f"Groq server returned HTTP {status}")

        if status == 400:
            _, error_code = GroqProvider._error_fields(response)
            if error_code == "json_validate_failed":
                raise AIInvalidResponseError("Groq failed to produce valid structured output")

        raise AIProviderError(f"Groq rejected request with HTTP {status}")

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise AIInvalidResponseError("Groq returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise AIInvalidResponseError("Groq response must be a JSON object")
        return body

    def _normalize_response(
        self,
        request: AIRequest,
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
            raise AIInvalidResponseError("Groq response contains no model identity")
        request_id = body.get("id")
        fingerprint = body.get("system_fingerprint")
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
            provider_request_id=request_id if isinstance(request_id, str) else None,
            metadata=({"system_fingerprint": fingerprint} if isinstance(fingerprint, str) else {}),
        )

    @staticmethod
    def _extract_choice(body: dict[str, Any]) -> tuple[str, str | None]:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise AIInvalidResponseError("Groq response contains no completion choice")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise AIInvalidResponseError("Groq response contains no message content")
        finish = choice.get("finish_reason")
        return message["content"], finish if isinstance(finish, str) else None

    @staticmethod
    def _parse_structured(content: str) -> dict[str, Any]:
        try:
            structured = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AIInvalidResponseError("Groq returned malformed structured output") from exc
        if not isinstance(structured, dict):
            raise AIInvalidResponseError("Groq structured output must be a JSON object")
        return structured

    @staticmethod
    def _optional_non_negative_int(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value
