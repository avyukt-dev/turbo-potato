from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from news_ai_ai import (
    AIFailureReason,
    AIPolicyConfigLoader,
    AIProvidersConfig,
    AIProvidersConfigLoader,
    AIReasoningEffort,
    AIRequest,
    AIResponseFormat,
    AIRoutingMode,
    AIStageConfigLoader,
    AIStageId,
    AITaskType,
    build_ai_router,
)
from news_ai_common.config import ConfigError, ConfigLoader
from pydantic import ValidationError

EXPECTED = {
    AIStageId.CLAIM_EXTRACTION: (
        AITaskType.CLAIM_EXTRACTION,
        "claim-extraction",
        "prompts/claim-extraction/v4.txt",
    ),
    AIStageId.EVIDENCE_ASSESSMENT: (
        AITaskType.EVIDENCE_ASSESSMENT,
        "evidence-assessment",
        "prompts/evidence-assessment/v2.txt",
    ),
    AIStageId.CONTENT_GENERATION: (
        AITaskType.CONTENT_GENERATION,
        "content-generation",
        "prompts/content/v4.txt",
    ),
    AIStageId.QUALITY_CHECKING: (
        AITaskType.QUALITY_CHECKING,
        "content-quality",
        "prompts/quality/v4.txt",
    ),
}


def test_production_provider_policy_and_stages_configure_groq_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = ConfigLoader("config")
    provider_config = AIProvidersConfigLoader(loader).load()
    assert [item.adapter_type for item in provider_config.providers] == ["groq", "llama_cpp"]
    groq, llama = provider_config.providers
    assert groq.provider_id == "groq"
    assert str(groq.base_url).rstrip("/") == "https://api.groq.com/openai/v1"
    assert groq.model == "openai/gpt-oss-120b"
    assert groq.default_max_completion_tokens == 8192
    assert groq.api_key_env == "GROQ_API_KEY"
    assert groq.max_context_tokens == 131072
    assert llama.provider_id == "local-llama"
    assert str(llama.base_url).rstrip("/") == "http://127.0.0.1:8080"
    assert llama.model == "local-news-ai"
    assert llama.request_timeout_seconds == 120
    assert llama.health_timeout_seconds == 2
    assert llama.max_context_tokens == 8192

    policy = AIPolicyConfigLoader(loader).load()
    assert policy.mode is AIRoutingMode.HYBRID
    assert len(policy.sensitivity_provider_allowlists) == 12
    assert all(
        value == frozenset({"groq", "local-llama"})
        for value in policy.sensitivity_provider_allowlists.values()
    )

    stage_loader = AIStageConfigLoader(loader)
    stages = stage_loader.load_all()
    assert set(stages) == set(AIStageId)
    for stage_id, (task, prompt_id, prompt_path) in EXPECTED.items():
        stage = stages[stage_id]
        assert stage.stage_id is stage_id
        assert stage.task_type is task
        assert stage.prompt.prompt_id == prompt_id
        assert stage.prompt.version == (
            "v4"
            if stage.stage_id
            in (
                AIStageId.CLAIM_EXTRACTION,
                AIStageId.CONTENT_GENERATION,
                AIStageId.QUALITY_CHECKING,
            )
            else "v2"
            if stage.stage_id is AIStageId.EVIDENCE_ASSESSMENT
            else "v1"
        )
        assert stage.prompt.path == prompt_path
        assert [(item.provider_id, item.model) for item in stage.providers] == [
            ("groq", "openai/gpt-oss-120b"),
            ("local-llama", "local-news-ai"),
        ]
        assert stage.request_defaults.reasoning_effort is AIReasoningEffort.MEDIUM
        assert stage.fallback_on == frozenset(
            {
                AIFailureReason.INVALID_RESPONSE,
                AIFailureReason.TIMEOUT,
                AIFailureReason.RATE_LIMIT,
                AIFailureReason.UNAVAILABLE,
            }
        )
        prompt = stage_loader.resolve_prompt(stage)
        assert hashlib.sha256(prompt.read_text(encoding="utf-8").strip().encode()).hexdigest()

    monkeypatch.setenv("GROQ_API_KEY", "test-placeholder")
    router = build_ai_router(loader)
    for _, (task, _, _) in EXPECTED.items():
        request = AIRequest(
            task_type=task,
            system_prompt="configuration equivalence",
            input={},
            response_format=AIResponseFormat.STRUCTURED,
        )
        assert router.candidate_provider_ids(request) == ("groq", "local-llama")


def test_claim_v4_prompt_matches_output_contract() -> None:
    loader = ConfigLoader("config")
    stage_loader = AIStageConfigLoader(loader)
    stage = stage_loader.load(AIStageId.CLAIM_EXTRACTION)
    prompt = stage_loader.resolve_prompt(stage).read_text(encoding="utf-8")

    assert stage.prompt.version == "v4"
    assert stage.prompt.path == "prompts/claim-extraction/v4.txt"
    assert "A successful output requires at least one independently researchable claim." in prompt
    assert 'If there are no independently verifiable claims, return {"claims":[]}.' not in prompt
    assert "Every value object MUST contain kind." in prompt
    assert "value_kind never replaces kind." in prompt
    assert '{"kind":"EXACT_COPY_ONLY","value_kind":"<underlying kind>"}' in prompt
    assert (
        '{"source_text":"4.82%","value":{"kind":"PERCENT","quantity":{"relation":"EXACT",'
        '"amount":"4.82","upper":null}}}'
        in prompt
    )
    assert (
        '{"source_text":"$5 million","value":{"kind":"EXACT_COPY_ONLY","value_kind":"CURRENCY"}}'
        in prompt
    )


def test_provider_configuration_is_closed_and_secret_is_only_an_environment_reference() -> None:
    with pytest.raises(ValidationError):
        AIProvidersConfig.model_validate(
            {
                "schema_version": 1,
                "providers": [
                    {
                        "adapter_type": "cloud_magic",
                        "provider_id": "cloud",
                        "base_url": "https://example.com",
                        "model": "model",
                        "task_types": ["CLAIM_EXTRACTION"],
                    }
                ],
            }
        )
    config = AIProvidersConfig.model_validate(
        {
            "schema_version": 1,
            "providers": [
                {
                    "adapter_type": "llama_cpp",
                    "provider_id": "local-llama",
                    "base_url": "http://127.0.0.1:8080",
                    "model": "local-news-ai",
                    "task_types": ["CLAIM_EXTRACTION"],
                    "api_key_env": "NEWS_AI_LLAMA_API_KEY",
                }
            ],
        }
    )
    assert config.providers[0].api_key_env == "NEWS_AI_LLAMA_API_KEY"
    assert "secret-value" not in config.model_dump_json()

    with pytest.raises(ValidationError, match="must be unique"):
        AIProvidersConfig.model_validate(
            {
                "providers": [
                    config.providers[0].model_dump(),
                    config.providers[0].model_dump(),
                ]
            }
        )


def test_production_factory_requires_groq_key_and_builds_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = ConfigLoader("config")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        build_ai_router(loader)
    monkeypatch.setenv("GROQ_API_KEY", "test-placeholder")
    router = build_ai_router(loader)
    assert {item.provider_id for item in router.registry.capabilities()} == {
        "groq",
        "local-llama",
    }


def test_stage_loader_fails_when_required_file_or_prompt_is_missing(tmp_path: Path) -> None:
    loader = AIStageConfigLoader(ConfigLoader(tmp_path))
    with pytest.raises(ConfigError, match="not found"):
        loader.load(AIStageId.CLAIM_EXTRACTION)

    stage_dir = tmp_path / "models" / "stages"
    stage_dir.mkdir(parents=True)
    (stage_dir / "claim-extraction.yaml").write_text(
        """schema_version: 1
stage_id: claim-extraction
task_type: CLAIM_EXTRACTION
prompt: {prompt_id: claim-extraction, version: v1, path: prompts/missing/v1.txt}
providers: [{provider_id: local-llama, model: local-news-ai}]
fallback_on: [INVALID_RESPONSE]
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="prompt file not found"):
        loader.load(AIStageId.CLAIM_EXTRACTION)

    prompt = tmp_path / "prompts" / "missing" / "v1.txt"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("prompt", encoding="utf-8")
    stage_file = stage_dir / "claim-extraction.yaml"
    stage_file.write_text(
        stage_file.read_text(encoding="utf-8").replace(
            "prompt_id: claim-extraction", "prompt_id: evidence-assessment"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="prompt identity"):
        loader.load(AIStageId.CLAIM_EXTRACTION)
