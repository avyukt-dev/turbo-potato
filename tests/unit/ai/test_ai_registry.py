from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from news_ai_ai import (
    AICapabilityError,
    AIInvalidResponseError,
    AIProviderNotRegisteredError,
    AIProviderRegistry,
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AITaskType,
    ProviderCapabilities,
    ProviderLocality,
)


@dataclass
class FakeProvider:
    provider_id: str
    capabilities: ProviderCapabilities
    response: AIResponse

    async def execute(self, request: AIRequest) -> AIResponse:
        return self.response


def _provider(
    provider_id: str = "local-llama",
    *,
    response_provider: str | None = None,
    response_model: str = "model-a",
    structured: bool = True,
) -> FakeProvider:
    capabilities = ProviderCapabilities(
        provider_id=provider_id,
        locality=ProviderLocality.LOCAL,
        task_types=frozenset({AITaskType.CLAIM_EXTRACTION}),
        response_formats=frozenset({AIResponseFormat.TEXT, AIResponseFormat.STRUCTURED}),
        models=frozenset({"model-a"}),
    )
    response = AIResponse(
        text=None if structured else "claims",
        structured={"claims": []} if structured else None,
        provider=response_provider or provider_id,
        model=response_model,
        latency_ms=12,
    )
    return FakeProvider(provider_id=provider_id, capabilities=capabilities, response=response)


def _structured_request(**updates: object) -> AIRequest:
    request = AIRequest(
        task_type=AITaskType.CLAIM_EXTRACTION,
        system_prompt="Extract claims.",
        input={"story": "example"},
        model="model-a",
        response_format=AIResponseFormat.STRUCTURED,
    )
    return request.model_copy(update=updates)


def test_registry_rejects_duplicate_and_mismatched_provider_identity() -> None:
    provider = _provider()
    registry = AIProviderRegistry([provider])

    with pytest.raises(ValueError, match="already registered"):
        registry.register(provider)

    mismatched = FakeProvider(
        provider_id="local-llama",
        capabilities=provider.capabilities.model_copy(update={"provider_id": "cloud-a"}),
        response=provider.response,
    )
    with pytest.raises(ValueError, match="must match"):
        AIProviderRegistry([mismatched])


def test_registry_reports_capabilities_in_stable_order_without_routing() -> None:
    second = _provider("z-provider")
    first = _provider("a-provider")
    registry = AIProviderRegistry([second, first])

    assert [item.provider_id for item in registry.capabilities()] == ["a-provider", "z-provider"]
    assert registry.compatible_provider_ids(_structured_request(model=None)) == (
        "a-provider",
        "z-provider",
    )


def test_registry_requires_registered_and_compatible_provider() -> None:
    registry = AIProviderRegistry([_provider()])

    with pytest.raises(AIProviderNotRegisteredError):
        registry.get("missing")

    disallowed = _structured_request(allowed_providers=("cloud-a",))
    with pytest.raises(AICapabilityError, match="not allowed"):
        registry.require_compatible("local-llama", disallowed)


def test_registry_execute_validates_provider_and_model_identity() -> None:
    wrong_provider = AIProviderRegistry([_provider(response_provider="cloud-a")])
    with pytest.raises(AIInvalidResponseError, match="provider response identity"):
        asyncio.run(wrong_provider.execute("local-llama", _structured_request()))

    wrong_model = AIProviderRegistry([_provider(response_model="model-b")])
    with pytest.raises(AIInvalidResponseError, match="response model"):
        asyncio.run(wrong_model.execute("local-llama", _structured_request()))


def test_registry_execute_enforces_requested_output_shape() -> None:
    registry = AIProviderRegistry([_provider(structured=False)])

    with pytest.raises(AIInvalidResponseError, match="no structured output"):
        asyncio.run(registry.execute("local-llama", _structured_request()))


def test_registry_execute_returns_valid_normalized_response() -> None:
    registry = AIProviderRegistry([_provider()])

    response = asyncio.run(registry.execute("local-llama", _structured_request()))

    assert response.provider == "local-llama"
    assert response.model == "model-a"
    assert response.structured == {"claims": []}
