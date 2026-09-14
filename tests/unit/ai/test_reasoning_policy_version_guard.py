from __future__ import annotations

import pytest
from news_ai_ai import (
    AIReasoningEffort,
    AIRequest,
    AIResponseFormat,
    AIRouter,
    AIRoutingPolicyError,
    AIStageConfig,
    AIStageId,
    AIStagePromptConfig,
    AIStageProviderSelection,
    AITaskType,
)


def test_router_rejects_stale_reasoning_policy_before_provider_execution() -> None:
    selection = AIStageProviderSelection(provider_id="groq", model="openai/gpt-oss-120b")
    stage = AIStageConfig(
        stage_id=AIStageId.CLAIM_EXTRACTION,
        task_type=AITaskType.CLAIM_EXTRACTION,
        prompt=AIStagePromptConfig(
            prompt_id="claim-extraction",
            version="v1",
            path="prompts/claim-extraction/v1.txt",
        ),
        providers=(selection,),
    )
    request = AIRequest(
        task_type=AITaskType.CLAIM_EXTRACTION,
        system_prompt="Extract claims.",
        input={"story": "example"},
        reasoning_effort=AIReasoningEffort.MEDIUM,
        reasoning_policy_version="reasoning-routing-policy-v0",
        response_format=AIResponseFormat.STRUCTURED,
    )

    with pytest.raises(AIRoutingPolicyError, match="reasoning policy version is not current"):
        AIRouter._attempt_request(request, stage, selection)
