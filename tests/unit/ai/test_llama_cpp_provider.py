from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from news_ai_ai import (
    AIContextTooLargeError,
    AIInvalidResponseError,
    AIProviderPolicyError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    AIRequest,
    AIResponseFormat,
    AITaskType,
    LlamaCppProvider,
    LlamaCppProviderConfig,
    ProviderLocality,
)
from pydantic import ValidationError


def _config() -> LlamaCppProviderConfig:
    return LlamaCppProviderConfig(
        base_url="http://127.0.0.1:8080",
        model="tiny-model",
        task_types=frozenset({AITaskType.CLASSIFICATION, AITaskType.CLAIM_EXTRACTION}),
        max_context_tokens=4096,
    )


def _request(*, structured: bool = False) -> AIRequest:
    return AIRequest(
        task_type=AITaskType.CLAIM_EXTRACTION,
        system_prompt="Extract atomic claims.",
        input={"headline": "Example"},
        response_format=(AIResponseFormat.STRUCTURED if structured else AIResponseFormat.TEXT),
        max_tokens=128,
    )


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


def test_config_requires_declared_tasks_and_safe_service_url() -> None:
    with pytest.raises(ValidationError, match="at least one task"):
        LlamaCppProviderConfig(
            base_url="http://127.0.0.1:8080",
            model="model",
            task_types=frozenset(),
        )

    with pytest.raises(ValidationError, match="query or fragment"):
        LlamaCppProviderConfig(
            base_url="http://127.0.0.1:8080?token=secret",
            model="model",
            task_types=frozenset({AITaskType.CLASSIFICATION}),
        )


def test_provider_declares_local_capabilities_without_service_manager_logic() -> None:
    provider = LlamaCppProvider(_config(), client=httpx.AsyncClient())

    assert provider.provider_id == "local-llama"
    assert provider.capabilities.locality is ProviderLocality.LOCAL
    assert provider.capabilities.models == frozenset({"tiny-model"})
    assert provider.capabilities.max_context_tokens == 4096

    asyncio.run(provider.close())


def test_healthcheck_requires_http_200_and_ok_payload() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/health"
            return httpx.Response(200, json={"status": "ok"})

        async with _client(httpx.MockTransport(handler)) as client:
            provider = LlamaCppProvider(_config(), client=client)
            assert await provider.healthcheck()

    asyncio.run(run())


def test_healthcheck_returns_false_for_loading_or_invalid_response() -> None:
    async def run(status: int, payload: object) -> bool:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=payload)

        async with _client(httpx.MockTransport(handler)) as client:
            return await LlamaCppProvider(_config(), client=client).healthcheck()

    assert not asyncio.run(run(503, {"error": {"message": "Loading model"}}))
    assert not asyncio.run(run(200, {"status": "loading"}))


def test_text_request_maps_to_chat_completion_and_normalizes_response() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            payload = json.loads(request.content)
            assert payload["model"] == "tiny-model"
            assert payload["stream"] is False
            assert payload["max_tokens"] == 128
            assert "response_format" not in payload
            assert payload["messages"][0] == {
                "role": "system",
                "content": "Extract atomic claims.",
            }
            assert payload["messages"][1]["content"] == '{"headline":"Example"}'
            return httpx.Response(
                200,
                json={
                    "id": "cmpl-1",
                    "model": "tiny-model",
                    "system_fingerprint": "build-1",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "One claim."},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                },
            )

        async with _client(httpx.MockTransport(handler)) as client:
            response = await LlamaCppProvider(_config(), client=client).execute(_request())
            assert response.text == "One claim."
            assert response.structured is None
            assert response.provider == "local-llama"
            assert response.model == "tiny-model"
            assert response.usage.input_tokens == 10
            assert response.usage.output_tokens == 3
            assert response.provider_request_id == "cmpl-1"
            assert response.metadata == {"system_fingerprint": "build-1"}

    asyncio.run(run())


def test_structured_request_never_trusts_http_200_without_valid_json_object() -> None:
    async def run(content: str) -> dict[str, object]:
        async def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            assert payload["response_format"] == {"type": "json_object"}
            return httpx.Response(
                200,
                json={
                    "model": "tiny-model",
                    "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                },
            )

        async with _client(httpx.MockTransport(handler)) as client:
            response = await LlamaCppProvider(_config(), client=client).execute(
                _request(structured=True)
            )
            assert response.structured is not None
            return response.structured

    assert asyncio.run(run('{"claims":[]}')) == {"claims": []}

    with pytest.raises(AIInvalidResponseError, match="malformed structured output"):
        asyncio.run(run("I should probably return JSON."))

    with pytest.raises(AIInvalidResponseError, match="must be a JSON object"):
        asyncio.run(run("[]"))


def test_provider_maps_http_failures_to_normalized_error_types() -> None:
    async def execute_status(status: int) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": {"message": "failed"}})

        async with _client(httpx.MockTransport(handler)) as client:
            await LlamaCppProvider(_config(), client=client).execute(_request())

    cases = [
        (429, AIProviderRateLimitError),
        (401, AIProviderPolicyError),
        (413, AIContextTooLargeError),
        (503, AIProviderUnavailableError),
    ]
    for status, error_type in cases:
        with pytest.raises(error_type):
            asyncio.run(execute_status(status))


def test_provider_maps_transport_timeout_and_unavailable_errors() -> None:
    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async def unavailable_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async def run(handler: httpx.MockTransport) -> None:
        async with _client(handler) as client:
            await LlamaCppProvider(_config(), client=client).execute(_request())

    with pytest.raises(AIProviderTimeoutError):
        asyncio.run(run(httpx.MockTransport(timeout_handler)))
    with pytest.raises(AIProviderUnavailableError):
        asyncio.run(run(httpx.MockTransport(unavailable_handler)))


def test_provider_rejects_malformed_completion_envelope() -> None:
    async def run(payload: object) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        async with _client(httpx.MockTransport(handler)) as client:
            await LlamaCppProvider(_config(), client=client).execute(_request())

    with pytest.raises(AIInvalidResponseError, match="no completion choice"):
        asyncio.run(run({"choices": []}))
    with pytest.raises(AIInvalidResponseError, match="no message content"):
        asyncio.run(run({"choices": [{"message": {"content": None}}]}))
