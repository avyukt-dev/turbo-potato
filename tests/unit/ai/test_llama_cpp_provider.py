from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest
from news_ai_ai import (
    AIContextTooLargeError,
    AIInvalidResponseError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    AIReasoningEffort,
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


def _completion(content: str | None = "One claim.", *, choices: bool = True):
    return SimpleNamespace(
        id="cmpl-1",
        model="tiny-model",
        choices=(
            [SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")]
            if choices
            else []
        ),
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
    )


class _Completions:
    def __init__(self, outcome, calls: list[dict]) -> None:
        self.outcome = outcome
        self.calls = calls

    async def create(self, **payload):
        self.calls.append(payload)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _Client:
    def __init__(self, outcome, calls: list[dict] | None = None) -> None:
        self.calls = calls if calls is not None else []
        self.chat = SimpleNamespace(completions=_Completions(outcome, self.calls))


def _status_error(status: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "http://127.0.0.1:8080/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return openai.APIStatusError("safe", response=response, body={"error": "safe"})


def test_config_requires_declared_tasks_and_safe_service_url() -> None:
    with pytest.raises(ValidationError, match="at least one task"):
        LlamaCppProviderConfig(
            base_url="http://127.0.0.1:8080", model="model", task_types=frozenset()
        )
    with pytest.raises(ValidationError, match="query or fragment"):
        LlamaCppProviderConfig(
            base_url="http://127.0.0.1:8080?token=secret",
            model="model",
            task_types=frozenset({AITaskType.CLASSIFICATION}),
        )


def test_provider_declares_local_capabilities_without_service_manager_logic() -> None:
    provider = LlamaCppProvider(_config(), client=_Client(_completion()))
    assert provider.provider_id == "local-llama"
    assert provider.capabilities.locality is ProviderLocality.LOCAL
    assert provider.capabilities.models == frozenset({"tiny-model"})
    assert provider.capabilities.max_context_tokens == 4096


def test_healthcheck_uses_separate_bounded_http_probe() -> None:
    async def run(status: int, payload: object) -> bool:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/health"
            return httpx.Response(status, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as health:
            return await LlamaCppProvider(
                _config(), client=_Client(_completion()), health_client=health
            ).healthcheck()

    assert asyncio.run(run(200, {"status": "ok"}))
    assert not asyncio.run(run(503, {"error": "loading"}))
    assert not asyncio.run(run(200, {"status": "loading"}))


def test_sdk_request_maps_to_chat_completion_and_normalizes_response() -> None:
    calls: list[dict] = []
    request = _request().model_copy(update={"reasoning_effort": AIReasoningEffort.HIGH})
    response = asyncio.run(
        LlamaCppProvider(_config(), client=_Client(_completion(), calls)).execute(request)
    )
    assert calls == [
        {
            "model": "tiny-model",
            "messages": [
                {"role": "system", "content": "Extract atomic claims."},
                {"role": "user", "content": '{"headline":"Example"}'},
            ],
            "temperature": 0.2,
            "max_tokens": 128,
        }
    ]
    assert response.text == "One claim."
    assert response.usage.input_tokens == 10
    assert response.provider_request_id == "cmpl-1"


def test_structured_request_rejects_invalid_sdk_response() -> None:
    valid = LlamaCppProvider(_config(), client=_Client(_completion('{"claims":[]}')))
    assert asyncio.run(valid.execute(_request(structured=True))).structured == {"claims": []}
    for content in ("not json", "[]"):
        provider = LlamaCppProvider(_config(), client=_Client(_completion(content)))
        with pytest.raises(AIInvalidResponseError):
            asyncio.run(provider.execute(_request(structured=True)))
    for completion in (_completion(choices=False), _completion(None)):
        with pytest.raises(AIInvalidResponseError):
            asyncio.run(LlamaCppProvider(_config(), client=_Client(completion)).execute(_request()))


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            openai.RateLimitError(
                "safe",
                response=httpx.Response(429, request=httpx.Request("POST", "http://local")),
                body=None,
            ),
            AIProviderRateLimitError,
        ),
        (_status_error(413), AIContextTooLargeError),
        (_status_error(503), AIProviderUnavailableError),
        (
            openai.APITimeoutError(request=httpx.Request("POST", "http://local")),
            AIProviderTimeoutError,
        ),
    ],
)
def test_sdk_errors_are_normalized(error: Exception, expected: type[Exception]) -> None:
    with pytest.raises(expected):
        asyncio.run(LlamaCppProvider(_config(), client=_Client(error)).execute(_request()))
