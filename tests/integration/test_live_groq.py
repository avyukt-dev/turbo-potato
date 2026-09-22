from __future__ import annotations

import asyncio
import os

import pytest
from news_ai_ai import (
    REASONING_ROUTING_POLICY_VERSION,
    AIReasoningEffort,
    AIRequest,
    AIResponseFormat,
    AITaskType,
    GroqProvider,
    GroqProviderConfig,
)

pytestmark = pytest.mark.skipif(
    os.getenv("NEWS_AI_RUN_LIVE_GROQ") != "1" or not os.getenv("GROQ_API_KEY"),
    reason="live Groq acceptance requires explicit opt-in and GROQ_API_KEY",
)


def test_live_groq_structured_completion_normalizes_without_reasoning() -> None:
    async def run() -> None:
        provider = GroqProvider(
            GroqProviderConfig(task_types=frozenset({AITaskType.CLAIM_EXTRACTION})),
            api_key=os.environ["GROQ_API_KEY"],
        )
        try:
            response = await provider.execute(
                AIRequest(
                    task_type=AITaskType.CLAIM_EXTRACTION,
                    system_prompt="Return one JSON object with the key ok and value true.",
                    input={"synthetic": True},
                    model="openai/gpt-oss-120b",
                    reasoning_effort=AIReasoningEffort.MEDIUM,
                    reasoning_policy_version=REASONING_ROUTING_POLICY_VERSION,
                    response_format=AIResponseFormat.STRUCTURED,
                    # GPT-OSS reasoning tokens consume the completion budget; 64 tokens can
                    # exhaust the budget before valid JSON is produced.
                    max_tokens=1024,
                    timeout_seconds=30,
                )
            )
        finally:
            await provider.close()
        assert response.provider == "groq"
        assert response.model == "openai/gpt-oss-120b"
        assert response.structured == {"ok": True}
        assert "reasoning" not in response.model_dump_json()

    asyncio.run(run())
