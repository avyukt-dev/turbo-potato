from __future__ import annotations

import asyncio
from types import SimpleNamespace

from news_ai_ai import (
    AIRequest,
    AIResponseFormat,
    AITaskType,
    CredentialPoolConfig,
    GeminiProvider,
    GeminiProviderConfig,
    OpenAIProvider,
    OpenAIProviderConfig,
    ProviderModelConfig,
)
from news_ai_ai.credentials import MemoryCredentialPool, resolve_credential_pool


def _pool(provider: str):
    config = CredentialPoolConfig(
        pool_id=f"{provider}-test", provider=provider, env_prefix=f"{provider.upper()}_API_KEY"
    )
    resolved = resolve_credential_pool(
        config, environ={f"{provider.upper()}_API_KEY_1": "secret-value"}
    )
    return MemoryCredentialPool(config, resolved)


def _request() -> AIRequest:
    return AIRequest(
        task_type=AITaskType.CLAIM_EXTRACTION,
        system_prompt="Return JSON.",
        input={"story": "example"},
        response_format=AIResponseFormat.STRUCTURED,
        max_tokens=64,
    )


def _model() -> ProviderModelConfig:
    return ProviderModelConfig(
        model_id="model-1", task_types=frozenset({AITaskType.CLAIM_EXTRACTION})
    )


def test_openai_sdk_adapter_normalizes_provider_contract() -> None:
    calls: list[dict] = []

    class Completions:
        async def create(self, **payload):
            calls.append(payload)
            return SimpleNamespace(
                id="openai-request",
                model="model-1",
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"claims":[]}'),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    provider = OpenAIProvider(
        OpenAIProviderConfig(credential_pool_id="openai-test", models=(_model(),)),
        _pool("openai"),
        client_factory=lambda _: client,
    )
    response = asyncio.run(provider.execute(_request()))
    assert response.structured == {"claims": []}
    assert response.provider_request_id == "openai-request"
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_gemini_sdk_adapter_normalizes_provider_contract() -> None:
    calls: list[dict] = []

    class Models:
        async def generate_content(self, **payload):
            calls.append(payload)
            return SimpleNamespace(
                text='{"claims":[]}',
                response_id="gemini-request",
                usage_metadata=SimpleNamespace(prompt_token_count=3, candidates_token_count=2),
            )

    client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
    provider = GeminiProvider(
        GeminiProviderConfig(credential_pool_id="gemini-test", models=(_model(),)),
        _pool("gemini"),
        client_factory=lambda _: client,
    )
    response = asyncio.run(provider.execute(_request()))
    assert response.structured == {"claims": []}
    assert response.provider_request_id == "gemini-request"
    assert calls[0]["model"] == "model-1"
    assert calls[0]["config"].response_mime_type == "application/json"
