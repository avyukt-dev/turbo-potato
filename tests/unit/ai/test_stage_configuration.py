from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from news_ai_ai import (
    AIFailureReason,
    AIPolicyConfigLoader,
    AIProvidersConfig,
    AIProvidersConfigLoader,
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
        "prompts/claim-extraction/v1.txt",
    ),
    AIStageId.EVIDENCE_ASSESSMENT: (
        AITaskType.EVIDENCE_ASSESSMENT,
        "evidence-assessment",
        "prompts/evidence-assessment/v1.txt",
    ),
    AIStageId.CONTENT_GENERATION: (
        AITaskType.CONTENT_GENERATION,
        "content-generation",
        "prompts/content/v1.txt",
    ),
    AIStageId.QUALITY_CHECKING: (
        AITaskType.QUALITY_CHECKING,
        "content-quality",
        "prompts/quality/v1.txt",
    ),
}


def test_production_provider_policy_and_all_closed_stages_preserve_llama_behavior() -> None:
    loader = ConfigLoader("config")
    provider_config = AIProvidersConfigLoader(loader).load()
    assert len(provider_config.providers) == 1
    provider = provider_config.providers[0]
    assert provider.adapter_type == "llama_cpp"
    assert provider.provider_id == "local-llama"
    assert str(provider.base_url).rstrip("/") == "http://127.0.0.1:8080"
    assert provider.model == "local-news-ai"
    assert provider.request_timeout_seconds == 120
    assert provider.health_timeout_seconds == 2
    assert provider.max_context_tokens == 8192

    policy = AIPolicyConfigLoader(loader).load()
    assert policy.mode is AIRoutingMode.LOCAL
    assert len(policy.sensitivity_provider_allowlists) == 12
    assert all(
        value == frozenset({"local-llama"})
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
        assert stage.prompt.version == "v1"
        assert stage.prompt.path == prompt_path
        assert [(item.provider_id, item.model) for item in stage.providers] == [
            ("local-llama", "local-news-ai")
        ]
        assert stage.fallback_on == frozenset({AIFailureReason.INVALID_RESPONSE})
        prompt = stage_loader.resolve_prompt(stage)
        assert hashlib.sha256(prompt.read_text(encoding="utf-8").strip().encode()).hexdigest()

    router = build_ai_router(loader)
    for _, (task, _, _) in EXPECTED.items():
        request = AIRequest(
            task_type=task,
            system_prompt="configuration equivalence",
            input={},
            response_format=AIResponseFormat.STRUCTURED,
        )
        assert router.candidate_provider_ids(request) == ("local-llama",)


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
