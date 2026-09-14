from __future__ import annotations

import pytest
from news_ai_ai import (
    REASONING_ROUTING_POLICY_VERSION,
    AIReasoningEffort,
    AIRequest,
    AIResponseFormat,
    AIRouter,
    AIRoutingPolicyError,
    AIStageConfig,
    AIStageId,
    AIStagePromptConfig,
    AIStageProviderSelection,
    AIStageRequestDefaults,
    AITaskType,
)


def _selection() -> AIStageProviderSelection:
    return AIStageProviderSelection(provider_id="groq", model="openai/gpt-oss-120b")


def _stage(
    *, default_effort: AIReasoningEffort | None = None
) -> tuple[AIStageConfig, AIStageProviderSelection]:
    selection = _selection()
    return (
        AIStageConfig(
            stage_id=AIStageId.CLAIM_EXTRACTION,
            task_type=AITaskType.CLAIM_EXTRACTION,
            prompt=AIStagePromptConfig(
                prompt_id="claim-extraction",
                version="v1",
                path="prompts/claim-extraction/v1.txt",
            ),
            providers=(selection,),
            request_defaults=AIStageRequestDefaults(reasoning_effort=default_effort),
        ),
        selection,
    )


def _request(**updates: object) -> AIRequest:
    request = AIRequest(
        task_type=AITaskType.CLAIM_EXTRACTION,
        system_prompt="Extract claims.",
        input={"story": "example"},
        response_format=AIResponseFormat.STRUCTURED,
    )
    return request.model_copy(update=updates)


def test_router_rejects_stale_reasoning_policy_before_provider_execution() -> None:
    stage, selection = _stage()
    request = _request(
        reasoning_effort=AIReasoningEffort.MEDIUM,
        reasoning_policy_version="reasoning-routing-policy-v0",
    )

    with pytest.raises(AIRoutingPolicyError, match="reasoning policy version is not current"):
        AIRouter._attempt_request(request, stage, selection)


def test_stage_reasoning_default_gets_current_policy_provenance() -> None:
    stage, selection = _stage(default_effort=AIReasoningEffort.MEDIUM)

    attempt = AIRouter._attempt_request(_request(), stage, selection)

    assert attempt.reasoning_effort is AIReasoningEffort.MEDIUM
    assert attempt.reasoning_policy_version == REASONING_ROUTING_POLICY_VERSION
    assert attempt.reasoning_reasons == ()


def test_explicit_reasoning_effort_requires_policy_provenance() -> None:
    stage, selection = _stage()
    request = _request(reasoning_effort=AIReasoningEffort.MEDIUM)

    with pytest.raises(
        AIRoutingPolicyError, match="reasoning effort requires deterministic policy provenance"
    ):
        AIRouter._attempt_request(request, stage, selection)
