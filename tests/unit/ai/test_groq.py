from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from news_ai_ai import (
    REASONING_ROUTING_POLICY_VERSION,
    AIContextTooLargeError,
    AIInvalidResponseError,
    AIProviderError,
    AIProviderPolicyError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    AIReasoningEffort,
    AIReasoningReason,
    AIRequest,
    AIResponseFormat,
    AITaskType,
    GroqProvider,
    GroqProviderConfig,
    ProviderLocality,
)
from pydantic import ValidationError

ACTIVE_TASKS = frozenset(
    {
        AITaskType.CLAIM_EXTRACTION,
        AITaskType.EVIDENCE_ASSESSMENT,
        AITaskType.CONTENT_GENERATION,
        AITaskType.QUALITY_CHECKING,
    }
)


def _config() -> GroqProviderConfig:
    return GroqProviderConfig(task_types=ACTIVE_TASKS)


def _request(
    *,
    structured: bool = True,
    max_tokens: int | None = None,
    reasoning_effort: AIReasoningEffort = AIReasoningEffort.MEDIUM,
    reasoning_reasons: tuple[AIReasoningReason, ...] = (),
) -> AIRequest:
    return AIRequest(
        task_type=AITaskType.CLAIM_EXTRACTION,
        system_prompt="Extract atomic claims.",
        input={"headline": "Example"},
        response_format=(AIResponseFormat.STRUCTURED if structured else AIResponseFormat.TEXT),
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        reasoning_policy_version=REASONING_ROUTING_POLICY_VERSION,
        reasoning_reasons=reasoning_reasons,
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _completion(content: str, **updates: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": "groq-request-1",
        "model": "openai/gpt-oss-120b",
        "system_fingerprint": "fp-1",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning": "must never be retained",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        "reasoning": "also must never be retained",
    }
    body.update(updates)
    return body


def test_config_and_provider_declare_closed_cloud_capabilities_without_secret() -> None:
    config = _config()
    provider = GroqProvider(config, api_key="test-secret", client=httpx.AsyncClient())
    assert provider.provider_id == "groq"
    assert provider.capabilities.locality is ProviderLocality.CLOUD
    assert provider.capabilities.models == frozenset({"openai/gpt-oss-120b"})
    assert provider.capabilities.task_types == ACTIVE_TASKS
    assert provider.capabilities.response_formats == frozenset(AIResponseFormat)
    assert provider.capabilities.max_context_tokens == 131072
    assert not provider.capabilities.supports_vision
    assert not provider.capabilities.supports_tools
    assert "test-secret" not in config.model_dump_json()
    assert config.api_key_env == "GROQ_API_KEY"
    asyncio.run(provider.close())


def test_config_rejects_insecure_url_and_constructor_requires_key() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        GroqProviderConfig(base_url="http://api.groq.com/openai/v1", task_types=ACTIVE_TASKS)
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        GroqProvider(_config(), api_key="  ")


def test_structured_request_maps_payload_and_normalizes_response() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
            assert request.headers["Authorization"] == "Bearer test-secret"
            payload = json.loads(request.content)
            assert payload == {
                "model": "openai/gpt-oss-120b",
                "messages": [
                    {"role": "system", "content": "Extract atomic claims."},
                    {"role": "user", "content": '{"headline":"Example"}'},
                ],
                "temperature": 0.2,
                "stream": False,
                "include_reasoning": False,
                "reasoning_effort": "medium",
                "max_completion_tokens": 8192,
                "response_format": {"type": "json_object"},
            }
            return httpx.Response(200, json=_completion('{"claims":[]}'))

        async with _client(handler) as client:
            response = await GroqProvider(_config(), api_key="test-secret", client=client).execute(
                _request()
            )
        assert response.structured == {"claims": []}
        assert response.text is None
        assert response.provider == "groq"
        assert response.model == "openai/gpt-oss-120b"
        assert response.usage.input_tokens == 12
        assert response.usage.output_tokens == 4
        assert response.provider_request_id == "groq-request-1"
        assert response.finish_reason == "stop"
        assert response.metadata == {"system_fingerprint": "fp-1"}
        assert "reasoning" not in response.model_dump_json()

    asyncio.run(run())


def test_text_request_uses_explicit_high_and_max_tokens_without_json_format() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            assert payload["reasoning_effort"] == "high"
            assert payload["max_completion_tokens"] == 123
            assert "response_format" not in payload
            return httpx.Response(200, json=_completion("One claim."))

        request = _request(
            structured=False,
            max_tokens=123,
            reasoning_effort=AIReasoningEffort.HIGH,
            reasoning_reasons=(AIReasoningReason.HIGH_RISK,),
        )
        async with _client(handler) as client:
            response = await GroqProvider(_config(), api_key="test-secret", client=client).execute(
                request
            )
        assert response.text == "One claim."
        assert response.structured is None

    asyncio.run(run())


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"not-json", "invalid JSON"),
        (json.dumps({"choices": []}).encode(), "no completion choice"),
        (json.dumps({"choices": [{"message": {}}]}).encode(), "no message content"),
        (json.dumps(_completion("[]")).encode(), "must be a JSON object"),
        (json.dumps(_completion("not-json")).encode(), "malformed structured output"),
        (json.dumps(_completion("{}", model=None)).encode(), "no model identity"),
        (
            json.dumps(_completion("{}", id="x" * 256)).encode(),
            "failed normalization",
        ),
    ],
)
def test_invalid_responses_are_normalized_without_raw_body(body: bytes, message: str) -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body)

        async with _client(handler) as client:
            await GroqProvider(_config(), api_key="test-secret", client=client).execute(_request())

    with pytest.raises(AIInvalidResponseError, match=message) as caught:
        asyncio.run(run())
    assert "test-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (429, AIProviderRateLimitError),
        (401, AIProviderPolicyError),
        (403, AIProviderPolicyError),
        (413, AIContextTooLargeError),
        (500, AIProviderUnavailableError),
        (503, AIProviderUnavailableError),
        (400, AIProviderError),
        (422, AIProviderError),
    ],
)
def test_http_failures_map_to_normalized_types(status: int, error_type: type[Exception]) -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": {"message": "secret-provider-body"}})

        async with _client(handler) as client:
            await GroqProvider(_config(), api_key="test-secret", client=client).execute(_request())

    with pytest.raises(error_type) as caught:
        asyncio.run(run())
    assert "secret-provider-body" not in str(caught.value)
    if status in {400, 422}:
        assert type(caught.value) is AIProviderError


@pytest.mark.parametrize("signal_field", ["code", "type"])
def test_413_rate_limit_signal_maps_to_rate_limit_without_provider_body(
    signal_field: str,
) -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            error = {
                "message": "secret-provider-body requested 9266 tokens against an 8000 TPM limit",
                signal_field: "rate_limit_exceeded",
            }
            return httpx.Response(413, json={"error": error})

        async with _client(handler) as client:
            await GroqProvider(_config(), api_key="test-secret", client=client).execute(_request())

    with pytest.raises(AIProviderRateLimitError, match="Groq request was rate limited") as caught:
        asyncio.run(run())

    error = str(caught.value)
    assert type(caught.value) is AIProviderRateLimitError
    assert "secret-provider-body" not in error
    assert "9266" not in error
    assert "8000" not in error
    assert "test-secret" not in error


def test_413_non_rate_limit_code_remains_context_too_large_without_provider_body() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                413,
                json={
                    "error": {
                        "message": "secret-provider-body",
                        "type": "invalid_request_error",
                        "code": "request_too_large",
                    }
                },
            )

        async with _client(handler) as client:
            await GroqProvider(_config(), api_key="test-secret", client=client).execute(_request())

    with pytest.raises(
        AIContextTooLargeError,
        match="Groq request exceeded the accepted context size",
    ) as caught:
        asyncio.run(run())

    error = str(caught.value)
    assert type(caught.value) is AIContextTooLargeError
    assert "secret-provider-body" not in error
    assert "test-secret" not in error


def test_413_error_code_takes_precedence_over_conflicting_rate_limit_type() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                413,
                json={
                    "error": {
                        "message": "secret-provider-body",
                        "type": "rate_limit_exceeded",
                        "code": "request_too_large",
                    }
                },
            )

        async with _client(handler) as client:
            await GroqProvider(_config(), api_key="test-secret", client=client).execute(_request())

    with pytest.raises(AIContextTooLargeError) as caught:
        asyncio.run(run())

    error = str(caught.value)
    assert type(caught.value) is AIContextTooLargeError
    assert "secret-provider-body" not in error
    assert "test-secret" not in error


def test_json_validation_failure_maps_to_invalid_response_without_provider_body() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "secret-provider-message",
                        "type": "invalid_request_error",
                        "code": "json_validate_failed",
                        "failed_generation": "secret-generated-content",
                    }
                },
            )

        async with _client(handler) as client:
            await GroqProvider(
                _config(),
                api_key="test-secret",
                client=client,
            ).execute(_request())

    with pytest.raises(
        AIInvalidResponseError,
        match="Groq failed to produce valid structured output",
    ) as caught:
        asyncio.run(run())

    error = str(caught.value)

    assert type(caught.value) is AIInvalidResponseError
    assert "secret-provider-message" not in error
    assert "secret-generated-content" not in error
    assert "test-secret" not in error


def test_transport_timeout_and_network_error_are_normalized() -> None:
    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret-timeout", request=request)

    async def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-network", request=request)

    async def run(handler) -> None:
        async with _client(handler) as client:
            await GroqProvider(_config(), api_key="test-secret", client=client).execute(_request())

    with pytest.raises(AIProviderTimeoutError, match="Groq request timed out"):
        asyncio.run(run(timeout))
    with pytest.raises(AIProviderUnavailableError, match="Groq endpoint is unavailable"):
        asyncio.run(run(unavailable))
