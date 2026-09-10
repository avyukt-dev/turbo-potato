from __future__ import annotations

from uuid import uuid4

import pytest
from news_ai_ai import (
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AITaskType,
    PromptReference,
    ProviderCapabilities,
    ProviderLocality,
    metadata_contains_secret_key,
)
from pydantic import ValidationError


def test_ai_request_uses_safe_independent_metadata_defaults() -> None:
    first = AIRequest(
        task_type=AITaskType.CLAIM_EXTRACTION,
        system_prompt="Extract atomic claims.",
        input={"story_id": str(uuid4())},
    )
    second = AIRequest(
        task_type=AITaskType.SUMMARIZATION,
        system_prompt="Summarize.",
        input={"text": "Example"},
    )

    first.metadata["trace"] = "one"

    assert second.metadata == {}
    assert first.temperature == 0.2
    assert first.response_format is AIResponseFormat.TEXT


def test_ai_request_rejects_blank_prompt_and_duplicate_restrictions() -> None:
    with pytest.raises(ValidationError, match="system_prompt must not be blank"):
        AIRequest(task_type=AITaskType.CLASSIFICATION, system_prompt="   ", input={})

    with pytest.raises(ValidationError, match="allowed_providers must be unique"):
        AIRequest(
            task_type=AITaskType.CLASSIFICATION,
            system_prompt="Classify.",
            input={},
            allowed_providers=("local-llama", "local-llama"),
        )


def test_prompt_reference_and_request_provenance_are_preserved() -> None:
    correlation_id = uuid4()
    request = AIRequest(
        task_type=AITaskType.ENTITY_EXTRACTION,
        system_prompt="Extract entities.",
        input={"text": "New Delhi"},
        prompt=PromptReference(
            prompt_id="entity-extraction",
            version="v1",
            checksum="sha256:example",
        ),
        correlation_id=correlation_id,
        input_artifact_ids=("story:123",),
        input_hash="sha256:input",
    )

    assert request.prompt is not None
    assert request.prompt.version == "v1"
    assert request.correlation_id == correlation_id
    assert request.input_artifact_ids == ("story:123",)


def test_ai_response_requires_normalized_output() -> None:
    with pytest.raises(ValidationError, match="must contain text or structured output"):
        AIResponse(provider="local-llama", model="example", latency_ms=10)


def test_provider_capabilities_enforce_task_format_model_and_allowlist() -> None:
    capabilities = ProviderCapabilities(
        provider_id="local-llama",
        locality=ProviderLocality.LOCAL,
        task_types=frozenset({AITaskType.CLASSIFICATION, AITaskType.CLAIM_EXTRACTION}),
        response_formats=frozenset({AIResponseFormat.TEXT, AIResponseFormat.STRUCTURED}),
        models=frozenset({"model-a"}),
    )
    supported = AIRequest(
        task_type=AITaskType.CLAIM_EXTRACTION,
        system_prompt="Extract claims.",
        input={},
        response_format=AIResponseFormat.STRUCTURED,
        model="model-a",
        allowed_providers=("local-llama",),
    )
    wrong_model = supported.model_copy(update={"model": "model-b"})
    wrong_provider = supported.model_copy(update={"allowed_providers": ("cloud-a",)})

    assert capabilities.supports(supported)
    assert not capabilities.supports(wrong_model)
    assert not capabilities.supports(wrong_provider)


def test_provider_capabilities_require_non_empty_task_and_format_sets() -> None:
    with pytest.raises(ValidationError, match="at least one supported task"):
        ProviderCapabilities(
            provider_id="local-llama",
            locality=ProviderLocality.LOCAL,
            task_types=frozenset(),
            response_formats=frozenset({AIResponseFormat.TEXT}),
        )

    with pytest.raises(ValidationError, match="at least one response format"):
        ProviderCapabilities(
            provider_id="local-llama",
            locality=ProviderLocality.LOCAL,
            task_types=frozenset({AITaskType.CLASSIFICATION}),
            response_formats=frozenset(),
        )


def test_secret_key_detection_checks_metadata_keys_not_news_content() -> None:
    assert metadata_contains_secret_key({"api_key": "value"})
    assert metadata_contains_secret_key({"provider_access_token": "value"})
    assert not metadata_contains_secret_key({"article_text": "The report mentioned a password leak."})
