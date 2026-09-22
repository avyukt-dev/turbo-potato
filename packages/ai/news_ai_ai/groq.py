"""Groq chat-completions adapter for the configured GPT-OSS model."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable
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


def _strict_json_schema(value: Any) -> Any:
    """Normalize generated schemas to Groq's strict structured-output contract."""

    if isinstance(value, list):
        return [_strict_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {
        key: _strict_json_schema(item)
        for key, item in value.items()
        # Constrained decoding supports a JSON Schema subset. Application
        # Pydantic validation remains authoritative for regex refinements.
        if key not in {"default", "pattern"}
    }
    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["required"] = list(properties)
        normalized["additionalProperties"] = False
    return normalized


class GroqProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(default="groq", pattern=r"^[a-z][a-z0-9_-]*$")
    base_url: AnyHttpUrl = Field(default="https://api.groq.com/openai/v1", validate_default=True)
    model: str = Field(default="openai/gpt-oss-120b", min_length=1, max_length=255)
    task_types: frozenset[AITaskType]
    request_timeout_seconds: float = Field(default=120.0, gt=0.0, le=600.0)
    max_context_tokens: int = Field(default=131072, ge=1)
    default_max_completion_tokens: int = Field(default=8192, ge=1)
    max_retry_after_seconds: float = Field(default=60.0, ge=0.0, le=300.0)
    credential_cooldown_seconds: float = Field(default=60.0, gt=0.0, le=3600.0)
    api_key_env: Literal["GROQ_API_KEY"] = "GROQ_API_KEY"
    api_key_envs: tuple[str, ...] = Field(default=(), max_length=15)

    @field_validator("model")
    @classmethod
    def strip_model(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Groq model must not be blank")
        return stripped

    @field_validator("api_key_envs")
    @classmethod
    def require_safe_unique_credential_references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Groq credential environment references must be unique")
        for name in value:
            if re.fullmatch(r"GROQ_API_KEY_[1-9][0-9]*", name) is None:
                raise ValueError(
                    "additional Groq credential references must use GROQ_API_KEY_<number>"
                )
        return value

    @property
    def credential_env_names(self) -> tuple[str, ...]:
        return (self.api_key_env, *self.api_key_envs)

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
    """Groq adapter with bounded credential failover before router fallback."""

    def __init__(
        self,
        config: GroqProviderConfig,
        *,
        api_key: str | None = None,
        api_keys: Iterable[str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if api_key is not None and api_keys is not None:
            raise ValueError("configure Groq credentials through one constructor input")
        supplied = (api_key,) if api_keys is None else tuple(api_keys)
        credentials = tuple(
            dict.fromkeys(
                value.strip() for value in supplied if value is not None and value.strip()
            )
        )
        if not credentials:
            raise ValueError("at least one configured GROQ_API_KEY credential is required")
        self.config = config
        self._api_keys = credentials
        self._credential_cooldowns = [0.0] * len(credentials)
        self._credential_lock = asyncio.Lock()
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
        attempted: set[int] = set()
        last_rate_limit: AIProviderRateLimitError | None = None

        while len(attempted) < len(self._api_keys):
            selected = await self._acquire_credential(attempted)
            if selected is None:
                break
            credential_index, api_key = selected
            attempted.add(credential_index)
            try:
                response = await self._execute_with_credential(request, selected_model, api_key)
            except AIProviderRateLimitError as exc:
                last_rate_limit = exc
                await self._cool_down_credential(credential_index, exc)
                continue
            return response

        if last_rate_limit is not None:
            raise last_rate_limit
        raise AIProviderRateLimitError(
            "All configured Groq credentials are cooling down",
            retry_after_seconds=await self._minimum_cooldown_remaining(),
        )

    async def _execute_with_credential(
        self,
        request: AIRequest,
        selected_model: str,
        api_key: str,
    ) -> AIResponse:
        started_at = monotonic()
        try:
            response = await self._http_client().post(
                f"{self._base_url}/chat/completions",
                json=self._request_payload(request, selected_model),
                headers={"Authorization": f"Bearer {api_key}"},
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

    async def _acquire_credential(self, attempted: set[int]) -> tuple[int, str] | None:
        async with self._credential_lock:
            now = monotonic()
            eligible = [
                index
                for index, cooldown_until in enumerate(self._credential_cooldowns)
                if index not in attempted and cooldown_until <= now
            ]
            if not eligible:
                return None
            index = eligible[0]
            return index, self._api_keys[index]

    async def _cool_down_credential(
        self,
        index: int,
        rate_limit: AIProviderRateLimitError,
    ) -> None:
        async with self._credential_lock:
            delay = (
                rate_limit.retry_after_seconds
                if rate_limit.retry_after_seconds is not None
                else self.config.credential_cooldown_seconds
            )
            self._credential_cooldowns[index] = max(
                self._credential_cooldowns[index], monotonic() + delay
            )

    async def _minimum_cooldown_remaining(self) -> float | None:
        async with self._credential_lock:
            remaining = [max(0.0, until - monotonic()) for until in self._credential_cooldowns]
        positive = [value for value in remaining if value > 0.0]
        return min(positive) if positive else None

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
            if request.response_schema is None:
                payload["response_format"] = {"type": "json_object"}
            else:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.response_schema.name,
                        "strict": request.response_schema.strict,
                        "schema": _strict_json_schema(request.response_schema.json_schema),
                    },
                }
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

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return

        if status == 429:
            raise AIProviderRateLimitError(
                "Groq request was rate limited",
                retry_after_seconds=self._retry_after_seconds(response),
            )
        if status in {401, 403}:
            raise AIProviderPolicyError("Groq request was not authorized")
        if status == 413:
            error_type, error_code = GroqProvider._error_fields(response)
            if error_code == "rate_limit_exceeded" or (
                error_code is None and error_type == "rate_limit_exceeded"
            ):
                raise AIProviderRateLimitError(
                    "Groq request was rate limited",
                    retry_after_seconds=self._retry_after_seconds(response),
                )
            raise AIContextTooLargeError("Groq request exceeded the accepted context size")
        if status >= 500:
            raise AIProviderUnavailableError(f"Groq server returned HTTP {status}")

        if status == 400:
            _, error_code = GroqProvider._error_fields(response)
            if error_code == "json_validate_failed":
                raise AIInvalidResponseError("Groq failed to produce valid structured output")

        raise AIProviderError(f"Groq rejected request with HTTP {status}")

    def _retry_after_seconds(self, response: httpx.Response) -> float | None:
        """Parse only bounded delta-seconds; never expose provider header text."""

        value = response.headers.get("retry-after")
        if value is None:
            return None
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*s?\s*", value)
        if match is None:
            return None
        return min(float(match.group(1)), self.config.max_retry_after_seconds)

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
