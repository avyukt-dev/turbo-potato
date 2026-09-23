from __future__ import annotations

import asyncio
import traceback
from types import SimpleNamespace

import groq
import httpx
import pytest
from news_ai_ai import (
    REASONING_ROUTING_POLICY_VERSION,
    AIProviderCredentialPoolExhaustedError,
    AIProviderRateLimitError,
    AIReasoningEffort,
    AIRequest,
    AIResponseFormat,
    AITaskType,
    GroqProvider,
    GroqProviderConfig,
    ProviderLocality,
)

ACTIVE_TASKS = frozenset(AITaskType)


def _config() -> GroqProviderConfig:
    return GroqProviderConfig(task_types=ACTIVE_TASKS)


def test_sdk_base_url_is_an_origin_and_rejects_versioned_api_paths() -> None:
    assert str(_config().base_url).rstrip("/") == "https://api.groq.com"
    with pytest.raises(ValueError, match="SDK origin"):
        GroqProviderConfig(
            base_url="https://api.groq.com/openai/v1",
            task_types=ACTIVE_TASKS,
        )


def _request() -> AIRequest:
    return AIRequest(
        task_type=AITaskType.CLAIM_EXTRACTION,
        system_prompt="Extract atomic claims.",
        input={"headline": "Example"},
        response_format=AIResponseFormat.STRUCTURED,
        reasoning_effort=AIReasoningEffort.MEDIUM,
        reasoning_policy_version=REASONING_ROUTING_POLICY_VERSION,
    )


def _completion(content: str = '{"claims":[]}') -> SimpleNamespace:
    return SimpleNamespace(
        id="request-1",
        model="openai/gpt-oss-120b",
        system_fingerprint="fingerprint",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
    )


class _Completions:
    def __init__(self, calls: list[dict], outcome):
        self.calls = calls
        self.outcome = outcome

    async def create(self, **payload):
        self.calls.append(payload)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _Client:
    def __init__(self, calls: list[dict], outcome):
        self.chat = SimpleNamespace(completions=_Completions(calls, outcome))


def test_sdk_adapter_maps_structured_request_and_response() -> None:
    calls: list[dict] = []
    provider = GroqProvider(
        _config(),
        api_key="secret",
        client_factory=lambda _: _Client(calls, _completion()),
    )
    response = asyncio.run(provider.execute(_request()))
    assert response.structured == {"claims": []}
    assert response.provider == "groq"
    assert response.model == "openai/gpt-oss-120b"
    assert response.usage.input_tokens == 12
    assert calls == [
        {
            "model": "openai/gpt-oss-120b",
            "messages": [
                {"role": "system", "content": "Extract atomic claims."},
                {"role": "user", "content": '{"headline":"Example"}'},
            ],
            "temperature": 0.2,
            "stream": False,
            "include_reasoning": False,
            "max_completion_tokens": 8192,
            "reasoning_effort": "medium",
            "response_format": {"type": "json_object"},
        }
    ]
    assert provider.capabilities.locality is ProviderLocality.CLOUD


def _status_error(kind, status: int):
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(status, request=request, headers={"retry-after": "12"})
    return kind("safe", response=response, body={"error": {"message": "sentinel-secret"}})


def test_rate_limit_cools_only_exact_key_and_uses_next_key() -> None:
    calls: list[str] = []

    def factory(key: str):
        calls.append(key)
        outcome = _status_error(groq.RateLimitError, 429) if key == "first" else _completion()
        return _Client([], outcome)

    provider = GroqProvider(_config(), api_keys=("first", "second"), client_factory=factory)
    assert asyncio.run(provider.execute(_request())).structured == {"claims": []}
    assert calls == ["first", "second"]
    calls.clear()
    assert asyncio.run(provider.execute(_request())).structured == {"claims": []}
    assert calls == ["second"]


def test_auth_failure_disables_exact_key_and_uses_next_key() -> None:
    calls: list[str] = []

    def factory(key: str):
        calls.append(key)
        outcome = (
            _status_error(groq.AuthenticationError, 401) if key == "invalid" else _completion()
        )
        return _Client([], outcome)

    provider = GroqProvider(_config(), api_keys=("invalid", "valid"), client_factory=factory)
    assert asyncio.run(provider.execute(_request())).structured == {"claims": []}
    assert calls == ["invalid", "valid"]
    calls.clear()
    asyncio.run(provider.execute(_request()))
    assert calls == ["valid"]


def test_single_rate_limited_or_auth_failed_key_returns_normalized_error() -> None:
    rate = GroqProvider(
        _config(),
        api_key="secret",
        client_factory=lambda _: _Client([], _status_error(groq.RateLimitError, 429)),
    )
    with pytest.raises(AIProviderRateLimitError) as caught:
        asyncio.run(rate.execute(_request()))
    assert caught.value.retry_after_seconds == 12
    assert "sentinel-secret" not in str(caught.value)

    auth = GroqProvider(
        _config(),
        api_key="secret",
        client_factory=lambda _: _Client([], _status_error(groq.AuthenticationError, 401)),
    )
    with pytest.raises(AIProviderCredentialPoolExhaustedError) as caught:
        asyncio.run(auth.execute(_request()))
    assert "secret" not in str(caught.value)


def test_normalized_failure_traceback_does_not_retain_raw_credential() -> None:
    sentinel = "SUPER_SECRET_GROQ_TRACEBACK_SENTINEL"
    provider = GroqProvider(
        _config(),
        api_key=sentinel,
        client_factory=lambda _: _Client([], _status_error(groq.AuthenticationError, 401)),
    )

    with pytest.raises(AIProviderCredentialPoolExhaustedError) as caught:
        asyncio.run(provider.execute(_request()))

    rendered = "".join(traceback.format_exception(caught.value))
    assert sentinel not in rendered
    for frame, _ in traceback.walk_tb(caught.value.__traceback__):
        if frame.f_code.co_filename != __file__:
            assert sentinel not in repr(frame.f_locals)


def test_multiple_models_are_exposed_and_unconfigured_model_fails() -> None:
    config = GroqProviderConfig.model_validate(
        {
            "task_types": [],
            "models": [
                {
                    "model_id": "model-a",
                    "task_types": ["CLAIM_EXTRACTION"],
                },
                {
                    "model_id": "model-b",
                    "task_types": ["CONTENT_GENERATION"],
                },
            ],
        }
    )
    provider = GroqProvider(
        config, api_key="secret", client_factory=lambda _: _Client([], _completion())
    )
    assert provider.capabilities.models == frozenset({"model-a", "model-b"})
