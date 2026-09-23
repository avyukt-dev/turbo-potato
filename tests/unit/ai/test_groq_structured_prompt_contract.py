from __future__ import annotations

import asyncio

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


class _Completions:
    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    async def create(self, **payload: object):
        from types import SimpleNamespace

        self.payload = payload
        assert payload["response_format"] == {"type": "json_object"}
        assert any("json" in item["content"].lower() for item in payload["messages"])
        return SimpleNamespace(
            id="groq-json-contract-probe",
            model="openai/gpt-oss-120b",
            system_fingerprint=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )


class _Client:
    def __init__(self) -> None:
        from types import SimpleNamespace

        self.completions = _Completions()
        self.chat = SimpleNamespace(completions=self.completions)


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

        client = _Client()
        provider = GroqProvider(
            GroqProviderConfig(task_types=frozenset({stage.task_type})),
            api_key="test-secret",
            client_factory=lambda _: client,
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
