from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from news_ai_ai import (
    REASONING_ROUTING_POLICY_VERSION,
    AIFailureReason,
    AIInvalidResponseError,
    AIPolicyConfig,
    AIProviderRegistry,
    AIReasoningEffort,
    AIReasoningReason,
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AIRouteAttemptOutcome,
    AIRouter,
    AIRoutingExecutionError,
    AIRoutingMode,
    AIStageConfig,
    AIStageId,
    AIStagePromptConfig,
    AIStageProviderSelection,
    AIStageRequestDefaults,
    AITaskType,
    ProviderCapabilities,
    ProviderLocality,
)


@dataclass
class FakeProvider:
    provider_id: str
    model: str
    honors_reasoning_effort: bool
    outcomes: list[AIResponse | Exception]
    requests: list[AIRequest] = field(default_factory=list)

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            locality=ProviderLocality.CLOUD,
            task_types=frozenset(AITaskType),
            response_formats=frozenset({AIResponseFormat.STRUCTURED}),
            models=frozenset({self.model}),
            honors_reasoning_effort=self.honors_reasoning_effort,
        )

    async def execute(self, request: AIRequest) -> AIResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(provider: str, model: str, *, ok: bool) -> AIResponse:
    return AIResponse(
        provider=provider,
        model=model,
        structured={"ok": ok},
        latency_ms=1,
    )


def _router(*providers: FakeProvider) -> AIRouter:
    selections = tuple(
        AIStageProviderSelection(provider_id=item.provider_id, model=item.model)
        for item in providers
    )
    stages = {
        stage_id: AIStageConfig(
            stage_id=stage_id,
            task_type=task,
            prompt=AIStagePromptConfig(
                prompt_id=stage_id.value,
                version="v1",
                path=f"prompts/{stage_id.value}/v1.txt",
            ),
            providers=selections,
            fallback_on=frozenset({AIFailureReason.INVALID_RESPONSE}),
            request_defaults=AIStageRequestDefaults(
                reasoning_effort=AIReasoningEffort.MEDIUM
            ),
        )
        for stage_id, task in (
            (AIStageId.CLAIM_EXTRACTION, AITaskType.CLAIM_EXTRACTION),
            (AIStageId.EVIDENCE_ASSESSMENT, AITaskType.EVIDENCE_ASSESSMENT),
            (AIStageId.CONTENT_GENERATION, AITaskType.CONTENT_GENERATION),
            (AIStageId.QUALITY_CHECKING, AITaskType.QUALITY_CHECKING),
        )
    }
    return AIRouter(
        AIProviderRegistry(providers),
        AIPolicyConfig(
            mode=AIRoutingMode.CLOUD,
            sensitivity_provider_allowlists={},
        ),
        stages,
    )


def _request(**updates: object) -> AIRequest:
    request = AIRequest(
        task_type=AITaskType.CLAIM_EXTRACTION,
        system_prompt="Return valid structured output.",
        input={},
        response_format=AIResponseFormat.STRUCTURED,
    )
    return request.model_copy(update=updates)


def _validate(response: AIResponse) -> None:
    if response.structured != {"ok": True}:
        raise AIInvalidResponseError("invalid structured output")


def test_medium_validation_failure_retries_same_reasoning_aware_provider_at_high() -> None:
    provider = FakeProvider(
        "cloud-a",
        "model-a",
        True,
        [
            _response("cloud-a", "model-a", ok=False),
            _response("cloud-a", "model-a", ok=True),
        ],
    )

    result = asyncio.run(_router(provider).execute(_request(), response_validator=_validate))

    assert [item.reasoning_effort for item in provider.requests] == [
        AIReasoningEffort.MEDIUM,
        AIReasoningEffort.HIGH,
    ]
    assert [item.outcome for item in result.attempts] == [
        AIRouteAttemptOutcome.FAILED,
        AIRouteAttemptOutcome.SUCCESS,
    ]
    assert result.attempts[0].failure_reason is AIFailureReason.INVALID_RESPONSE
    assert result.attempts[1].reasoning_policy_version == REASONING_ROUTING_POLICY_VERSION
    assert result.attempts[1].reasoning_reasons == (
        AIReasoningReason.VALIDATION_FAILURE,
    )


def test_explicit_high_never_retries_high_again() -> None:
    provider = FakeProvider(
        "cloud-a",
        "model-a",
        True,
        [
            _response("cloud-a", "model-a", ok=False),
            _response("cloud-a", "model-a", ok=True),
        ],
    )
    request = _request(
        reasoning_effort=AIReasoningEffort.HIGH,
        reasoning_policy_version=REASONING_ROUTING_POLICY_VERSION,
        reasoning_reasons=(AIReasoningReason.HIGH_RISK,),
    )

    with pytest.raises(AIRoutingExecutionError):
        asyncio.run(_router(provider).execute(request, response_validator=_validate))

    assert len(provider.requests) == 1


def test_reasoning_agnostic_provider_skips_fake_high_retry_and_falls_back() -> None:
    local = FakeProvider(
        "local-a",
        "model-a",
        False,
        [_response("local-a", "model-a", ok=False)],
    )
    cloud = FakeProvider(
        "cloud-b",
        "model-b",
        True,
        [_response("cloud-b", "model-b", ok=True)],
    )

    result = asyncio.run(_router(local, cloud).execute(_request(), response_validator=_validate))

    assert len(local.requests) == 1
    assert local.requests[0].reasoning_effort is AIReasoningEffort.MEDIUM
    assert cloud.requests[0].reasoning_effort is AIReasoningEffort.MEDIUM
    assert result.response.provider == "cloud-b"


def test_failed_high_retry_carries_high_decision_into_authorized_fallback() -> None:
    primary = FakeProvider(
        "cloud-a",
        "model-a",
        True,
        [
            _response("cloud-a", "model-a", ok=False),
            _response("cloud-a", "model-a", ok=False),
        ],
    )
    fallback = FakeProvider(
        "cloud-b",
        "model-b",
        True,
        [_response("cloud-b", "model-b", ok=True)],
    )

    result = asyncio.run(
        _router(primary, fallback).execute(_request(), response_validator=_validate)
    )

    assert [item.reasoning_effort for item in primary.requests] == [
        AIReasoningEffort.MEDIUM,
        AIReasoningEffort.HIGH,
    ]
    assert fallback.requests[0].reasoning_effort is AIReasoningEffort.HIGH
    assert fallback.requests[0].reasoning_reasons == (
        AIReasoningReason.VALIDATION_FAILURE,
    )
    assert result.attempts[-1].outcome is AIRouteAttemptOutcome.SUCCESS
