from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from news_ai_ai import (
    AIFailureReason,
    AIInvalidResponseError,
    AIPolicyConfig,
    AIPolicyConfigLoader,
    AIProviderPolicyError,
    AIProviderRegistry,
    AIProviderTimeoutError,
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AIRouteAttemptOutcome,
    AIRouter,
    AIRoutingExecutionError,
    AIRoutingMode,
    AIRoutingPolicyError,
    AIStageConfig,
    AIStageConfigLoader,
    AIStageId,
    AIStagePromptConfig,
    AIStageProviderSelection,
    AITaskType,
    ProviderCapabilities,
    ProviderLocality,
)
from news_ai_common.config import ConfigError, ConfigLoader
from pydantic import ValidationError


@dataclass
class FakeProvider:
    provider_id: str
    locality: ProviderLocality
    models: frozenset[str]
    outcomes: list[AIResponse | Exception]
    requests: list[AIRequest] = field(default_factory=list)

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            locality=self.locality,
            task_types=frozenset(AITaskType),
            response_formats=frozenset({AIResponseFormat.STRUCTURED, AIResponseFormat.TEXT}),
            models=self.models,
        )

    async def execute(self, request: AIRequest) -> AIResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(provider: str, model: str) -> AIResponse:
    return AIResponse(structured={"claims": []}, provider=provider, model=model, latency_ms=10)


def _request(**updates: object) -> AIRequest:
    request = AIRequest(
        task_type=AITaskType.CLAIM_EXTRACTION,
        system_prompt="Extract claims.",
        input={"story": "example"},
        response_format=AIResponseFormat.STRUCTURED,
    )
    return request.model_copy(update=updates)


def _stage(
    stage_id: AIStageId,
    task_type: AITaskType,
    providers: tuple[tuple[str, str], ...],
    fallback_on: frozenset[AIFailureReason] = frozenset(),
) -> AIStageConfig:
    return AIStageConfig(
        schema_version=1,
        stage_id=stage_id,
        task_type=task_type,
        prompt=AIStagePromptConfig(
            prompt_id=stage_id.value,
            version="v1",
            path=f"prompts/{stage_id.value}/v1.txt",
        ),
        providers=tuple(
            AIStageProviderSelection(provider_id=provider, model=model)
            for provider, model in providers
        ),
        fallback_on=fallback_on,
    )


def _stages(
    providers: tuple[tuple[str, str], ...] = (("local-a", "model-a"), ("cloud-a", "model-b")),
    fallback_on: frozenset[AIFailureReason] = frozenset(
        {AIFailureReason.TIMEOUT, AIFailureReason.UNAVAILABLE, AIFailureReason.INVALID_RESPONSE}
    ),
) -> dict[AIStageId, AIStageConfig]:
    return {
        stage_id: _stage(stage_id, task_type, providers, fallback_on)
        for stage_id, task_type in (
            (AIStageId.CLAIM_EXTRACTION, AITaskType.CLAIM_EXTRACTION),
            (AIStageId.EVIDENCE_ASSESSMENT, AITaskType.EVIDENCE_ASSESSMENT),
            (AIStageId.CONTENT_GENERATION, AITaskType.CONTENT_GENERATION),
            (AIStageId.QUALITY_CHECKING, AITaskType.QUALITY_CHECKING),
        )
    }


def _policy(**updates: object) -> AIPolicyConfig:
    policy = AIPolicyConfig(
        schema_version=1,
        mode=AIRoutingMode.HYBRID,
        sensitivity_provider_allowlists={"SENSITIVE": frozenset({"local-a"})},
    )
    return policy.model_copy(update=updates)


def _registry(local_outcomes=None, cloud_outcomes=None) -> AIProviderRegistry:
    return AIProviderRegistry(
        (
            FakeProvider(
                "local-a",
                ProviderLocality.LOCAL,
                frozenset({"model-a"}),
                local_outcomes or [_response("local-a", "model-a")],
            ),
            FakeProvider(
                "cloud-a",
                ProviderLocality.CLOUD,
                frozenset({"model-b"}),
                cloud_outcomes or [_response("cloud-a", "model-b")],
            ),
        )
    )


def test_policy_loader_uses_models_domain_and_rejects_empty_allowlist(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    (models / "policy.yaml").write_text(
        """schema_version: 1
mode: LOCAL
sensitivity_provider_allowlists:
  SENSITIVE: [local-a]
""",
        encoding="utf-8",
    )
    loaded = AIPolicyConfigLoader(ConfigLoader(tmp_path)).load()
    assert loaded.mode is AIRoutingMode.LOCAL
    assert loaded.sensitivity_provider_allowlists["SENSITIVE"] == frozenset({"local-a"})
    with pytest.raises(ValidationError, match="must not be empty"):
        AIPolicyConfig(sensitivity_provider_allowlists={"SENSITIVE": frozenset()})


def test_stage_contract_rejects_duplicate_providers_unsafe_fallback_and_extra() -> None:
    with pytest.raises(ValidationError, match="provider IDs must be unique"):
        _stage(
            AIStageId.CLAIM_EXTRACTION,
            AITaskType.CLAIM_EXTRACTION,
            (("local-a", "a"), ("local-a", "b")),
        )
    with pytest.raises(ValidationError, match="cannot authorize fallback"):
        _stage(
            AIStageId.CLAIM_EXTRACTION,
            AITaskType.CLAIM_EXTRACTION,
            (("local-a", "a"),),
            frozenset({AIFailureReason.OTHER}),
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        AIStageConfig.model_validate(
            {**_stages()[AIStageId.CLAIM_EXTRACTION].model_dump(), "unknown": True}
        )


def test_stage_loader_rejects_wrong_identity_task_and_path_escape(tmp_path: Path) -> None:
    stage_dir = tmp_path / "models" / "stages"
    prompt_dir = tmp_path / "prompts" / "claim-extraction"
    stage_dir.mkdir(parents=True)
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "v1.txt").write_text("prompt", encoding="utf-8")
    path = stage_dir / "claim-extraction.yaml"
    path.write_text(
        """schema_version: 1
stage_id: evidence-assessment
task_type: CLAIM_EXTRACTION
prompt: {prompt_id: claim-extraction, version: v1, path: prompts/claim-extraction/v1.txt}
providers: [{provider_id: local-a, model: model-a}]
fallback_on: [INVALID_RESPONSE]
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="identity"):
        AIStageConfigLoader(ConfigLoader(tmp_path)).load(AIStageId.CLAIM_EXTRACTION)
    wrong_task = path.read_text().replace("evidence-assessment", "claim-extraction")
    wrong_task = wrong_task.replace("task_type: CLAIM_EXTRACTION", "task_type: QUALITY_CHECKING")
    path.write_text(wrong_task, encoding="utf-8")
    with pytest.raises(ConfigError, match="task"):
        AIStageConfigLoader(ConfigLoader(tmp_path)).load(AIStageId.CLAIM_EXTRACTION)
    with pytest.raises(ValidationError, match="safe relative"):
        AIStagePromptConfig(prompt_id="x", version="v1", path="../secret.txt")


def test_router_applies_mode_request_and_sensitivity_restrictions() -> None:
    local = AIRouter(_registry(), _policy(mode=AIRoutingMode.LOCAL), _stages())
    cloud = AIRouter(_registry(), _policy(mode=AIRoutingMode.CLOUD), _stages())
    assert local.candidate_provider_ids(_request()) == ("local-a",)
    assert cloud.candidate_provider_ids(_request()) == ("cloud-a",)
    hybrid = AIRouter(_registry(), _policy(), _stages())
    assert hybrid.candidate_provider_ids(_request(allowed_providers=("cloud-a",))) == ("cloud-a",)
    assert hybrid.candidate_provider_ids(_request(sensitivity=("SENSITIVE",))) == ("local-a",)
    with pytest.raises(AIRoutingPolicyError, match="no AI provider allowlist"):
        hybrid.candidate_provider_ids(_request(sensitivity=("UNKNOWN",)))


def test_fallback_uses_each_stage_selected_model_and_records_attempts() -> None:
    registry = _registry(local_outcomes=[AIProviderTimeoutError("slow")])
    local = registry.get("local-a")
    cloud = registry.get("cloud-a")
    result = asyncio.run(AIRouter(registry, _policy(), _stages()).execute(_request()))
    assert [local.requests[0].model, cloud.requests[0].model] == ["model-a", "model-b"]
    assert result.response.model == "model-b"
    assert [(item.provider_id, item.outcome, item.failure_reason) for item in result.attempts] == [
        ("local-a", AIRouteAttemptOutcome.FAILED, AIFailureReason.TIMEOUT),
        ("cloud-a", AIRouteAttemptOutcome.SUCCESS, None),
    ]


def test_invalid_response_falls_back_but_policy_rejection_and_sensitive_route_do_not() -> None:
    invalid_registry = _registry(local_outcomes=[AIInvalidResponseError("invalid")])
    result = asyncio.run(AIRouter(invalid_registry, _policy(), _stages()).execute(_request()))
    assert result.attempts[0].failure_reason is AIFailureReason.INVALID_RESPONSE
    policy_registry = _registry(local_outcomes=[AIProviderPolicyError("denied")])
    cloud = policy_registry.get("cloud-a")
    with pytest.raises(AIRoutingExecutionError) as caught:
        asyncio.run(AIRouter(policy_registry, _policy(), _stages()).execute(_request()))
    assert caught.value.attempts[0].failure_reason is AIFailureReason.POLICY_REJECTION
    assert not cloud.requests
    sensitive_registry = _registry(local_outcomes=[AIProviderTimeoutError("slow")])
    sensitive_cloud = sensitive_registry.get("cloud-a")
    with pytest.raises(AIRoutingExecutionError):
        asyncio.run(
            AIRouter(sensitive_registry, _policy(), _stages()).execute(
                _request(sensitivity=("SENSITIVE",))
            )
        )
    assert not sensitive_cloud.requests


def test_startup_rejects_missing_stage_provider_and_unsupported_model() -> None:
    with pytest.raises(AIRoutingPolicyError, match="all production AI stages"):
        AIRouter(
            _registry(),
            _policy(),
            {AIStageId.CLAIM_EXTRACTION: next(iter(_stages().values()))},
        )
    stages = _stages(providers=(("missing", "model"),))
    with pytest.raises(AIRoutingPolicyError, match="unregistered provider"):
        AIRouter(_registry(), _policy(), stages)
    stages = _stages(providers=(("local-a", "unsupported"),))
    with pytest.raises(AIRoutingPolicyError, match="not exposed"):
        AIRouter(_registry(), _policy(), stages)


def test_explicit_request_model_must_agree_with_stage_configuration() -> None:
    router = AIRouter(_registry(), _policy(), _stages())
    assert router.candidate_provider_ids(_request(model="model-a")) == ("local-a", "cloud-a")
    with pytest.raises(AIRoutingPolicyError, match="conflicts"):
        router.candidate_provider_ids(_request(model="other"))


def test_unsupported_task_fails_closed() -> None:
    router = AIRouter(_registry(), _policy(), _stages())
    with pytest.raises(AIRoutingPolicyError, match="no AI stage"):
        router.candidate_provider_ids(_request(task_type=AITaskType.SUMMARIZATION))
