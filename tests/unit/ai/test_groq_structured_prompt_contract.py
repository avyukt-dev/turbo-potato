from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from news_ai_ai import (
    AIRequest,
    AIResponseFormat,
    AIStageConfigLoader,
    AIStageId,
    GroqProvider,
    GroqProviderConfig,
)
from news_ai_common.config import ConfigLoader


@pytest.mark.parametrize(
    "stage_id",
    [AIStageId.CONTENT_GENERATION, AIStageId.QUALITY_CHECKING],
)
def test_production_structured_prompt_satisfies_groq_json_mode(stage_id: AIStageId) -> None:
    async def run() -> None:
        loader = ConfigLoader("config")
        stage_loader = AIStageConfigLoader(loader)
        stage = stage_loader.load(stage_id)
        prompt = stage_loader.resolve_prompt(stage).read_text(encoding="utf-8")

        async def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            assert payload["response_format"] == {"type": "json_object"}
            assert any("json" in message["content"].lower() for message in payload["messages"])
            return httpx.Response(
                200,
                json={
                    "id": "groq-json-contract-probe",
                    "model": "openai/gpt-oss-120b",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "{}"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = GroqProvider(
                GroqProviderConfig(task_types=frozenset({stage.task_type})),
                api_key="test-secret",
                client=client,
            )
            response = await provider.execute(
                AIRequest(
                    task_type=stage.task_type,
                    system_prompt=prompt,
                    input={"contract_probe": True},
                    response_format=AIResponseFormat.STRUCTURED,
                )
            )

        assert response.structured == {}

    asyncio.run(run())
