from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from news_ai_ai import (
    AIFailureReason,
    AIInvalidResponseError,
    AIProviderPolicyError,
    AIProviderRegistry,
    AIProviderTimeoutError,
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AIRouteAttemptOutcome,
    AIRouter,
    AIRoutingConfig,
    AIRoutingConfigLoader,
    AIRoutingExecutionError,
    AIRoutingMode,
    AIRoutingPolicyError,
    AITaskType,
    ProviderCapabilities,
    ProviderLocality,
    TaskRoutingPolicy,
)
from news_ai_common.config import ConfigLoader
from pydantic import ValidationError


@dataclass
class FakeProvider:
    provider_id: str
    locality: ProviderLocality
    outcomes: list[AIResponse | Exception]

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            locality=self.locality,
            task_types=frozenset({AITaskType.CLAIM_EXTRACTION}),
            response_formats=frozenset({AIResponseFormat.STRUCTURED}),
        )

    async def execute(self, request: AIRequest) -> AIResponse:
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(provider: str) -> AIResponse:
    return AIResponse(
        structured={"claims": []},
        provider=provider,
        model="model-a",
        latency_ms=10,
    )


def _request(**updates: object) -> AIRequest:
    request = AIRequest(
        task_type=AITaskType.CLAIM_EXTRACTION,
        system_prompt="Extract claims.",
        input={"story": "example"},
        response_format=AIResponseFormat.STRUCTURED,
    )
    return request.model_copy(update=updates)


def _registry() -> AIProviderRegistry:
    return AIProviderRegistry(
        [
            FakeProvider("local-a", ProviderLocality.LOCAL, [_response("local-a")]),
            FakeProvider("cloud-a", ProviderLocality.CLOUD, [_response("cloud-a")]),
        ]
    )


def _config(**updates: object) -> AIRoutingConfig:
    config = AIRoutingConfig(
        schema_version=1,
        mode=AIRoutingMode.HYBRID,
        routes={
            AITaskType.CLAIM_EXTRACTION: TaskRoutingPolicy(
                providers=("local-a", "cloud-a"),
                fallback_on=frozenset(
                    {
                        AIFailureReason.TIMEOUT,
                        AIFailureReason.UNAVAILABLE,
                        AIFailureReason.INVALID_RESPONSE,
                    }
                ),
            )
        },
        sensitivity_provider_allowlists={"SENSITIVE": frozenset({"local-a"})},
    )
    return config.model_copy(update=updates)


def test_routing_config_loader_uses_models_domain(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    (models / "routing.yaml").write_text(
        """
schema_version: 1
mode: HYBRID
routes:
  CLAIM_EXTRACTION:
    providers:
      - local-a
      - cloud-a
    fallback_on:
      - TIMEOUT
sensitivity_provider_allowlists:
  SENSITIVE:
    - local-a
""".strip(),
        encoding="utf-8",
    )

    loaded = AIRoutingConfigLoader(ConfigLoader(tmp_path)).load()

    assert loaded.mode is AIRoutingMode.HYBRID
    route = loaded.routes[AITaskType.CLAIM_EXTRACTION]
    assert route.providers == ("local-a", "cloud-a")
    assert loaded.sensitivity_provider_allowlists["SENSITIVE"] == frozenset({"local-a"})


def test_routing_policy_rejects_duplicate_providers_and_unsafe_fallback() -> None:
    with pytest.raises(ValidationError, match="providers must be unique"):
        TaskRoutingPolicy(providers=("local-a", "local-a"))

    with pytest.raises(ValidationError, match="cannot authorize fallback"):
        TaskRoutingPolicy(
            providers=("local-a", "cloud-a"),
            fallback_on=frozenset({AIFailureReason.POLICY_REJECTION}),
        )


def test_router_rejects_unregistered_provider_references_at_startup() -> None:
    config = _config(
        routes={
            AITaskType.CLAIM_EXTRACTION: TaskRoutingPolicy(providers=("missing",)),
        }
    )

    with pytest.raises(AIRoutingPolicyError, match="unregistered provider"):
        AIRouter(_registry(), config)


def test_router_applies_mode_and_request_provider_restrictions() -> None:
    local_router = AIRouter(_registry(), _config(mode=AIRoutingMode.LOCAL))
    cloud_router = AIRouter(_registry(), _config(mode=AIRoutingMode.CLOUD))

    assert local_router.candidate_provider_ids(_request()) == ("local-a",)
    assert cloud_router.candidate_provider_ids(_request()) == ("cloud-a",)
    hybrid_router = AIRouter(_registry(), _config())
    assert hybrid_router.candidate_provider_ids(_request(allowed_providers=("cloud-a",))) == (
        "cloud-a",
    )


def test_sensitive_routing_requires_explicit_allowlist_for_every_tag() -> None:
    router = AIRouter(_registry(), _config())

    assert router.candidate_provider_ids(_request(sensitivity=("SENSITIVE",))) == ("local-a",)

    with pytest.raises(AIRoutingPolicyError, match="no AI provider allowlist"):
        router.candidate_provider_ids(_request(sensitivity=("UNKNOWN",)))


def test_router_falls_back_only_for_configured_failure_reason() -> None:
    local = FakeProvider(
        "local-a",
        ProviderLocality.LOCAL,
        [AIProviderTimeoutError("slow")],
    )
    cloud = FakeProvider("cloud-a", ProviderLocality.CLOUD, [_response("cloud-a")])
    router = AIRouter(AIProviderRegistry([local, cloud]), _config())

    result = asyncio.run(router.execute(_request()))

    assert result.response.provider == "cloud-a"
    attempts = [
        (attempt.provider_id, attempt.outcome, attempt.failure_reason)
        for attempt in result.attempts
    ]
    assert attempts == [
        ("local-a", AIRouteAttemptOutcome.FAILED, AIFailureReason.TIMEOUT),
        ("cloud-a", AIRouteAttemptOutcome.SUCCESS, None),
    ]


def test_invalid_structured_output_can_use_explicitly_allowed_fallback() -> None:
    local = FakeProvider(
        "local-a",
        ProviderLocality.LOCAL,
        [AIInvalidResponseError("bad structured output")],
    )
    cloud = FakeProvider("cloud-a", ProviderLocality.CLOUD, [_response("cloud-a")])
    router = AIRouter(AIProviderRegistry([local, cloud]), _config())

    result = asyncio.run(router.execute(_request()))

    assert result.response.provider == "cloud-a"
    assert result.attempts[0].failure_reason is AIFailureReason.INVALID_RESPONSE


def test_router_stops_when_failure_is_not_authorized_for_fallback() -> None:
    local = FakeProvider(
        "local-a",
        ProviderLocality.LOCAL,
        [AIProviderPolicyError("rejected")],
    )
    cloud_response = _response("cloud-a")
    cloud = FakeProvider("cloud-a", ProviderLocality.CLOUD, [cloud_response])
    router = AIRouter(AIProviderRegistry([local, cloud]), _config())

    with pytest.raises(AIRoutingExecutionError) as captured:
        asyncio.run(router.execute(_request()))

    assert len(captured.value.attempts) == 1
    assert captured.value.attempts[0].failure_reason is AIFailureReason.POLICY_REJECTION
    assert cloud.outcomes == [cloud_response]


def test_sensitive_fallback_cannot_escape_sensitivity_allowlist() -> None:
    local = FakeProvider(
        "local-a",
        ProviderLocality.LOCAL,
        [AIProviderTimeoutError("slow")],
    )
    cloud_response = _response("cloud-a")
    cloud = FakeProvider("cloud-a", ProviderLocality.CLOUD, [cloud_response])
    router = AIRouter(AIProviderRegistry([local, cloud]), _config())

    with pytest.raises(AIRoutingExecutionError) as captured:
        asyncio.run(router.execute(_request(sensitivity=("SENSITIVE",))))

    assert len(captured.value.attempts) == 1
    assert captured.value.attempts[0].provider_id == "local-a"
    assert cloud.outcomes == [cloud_response]


def test_router_rejects_tasks_without_an_explicit_route() -> None:
    router = AIRouter(_registry(), _config())
    request = _request(task_type=AITaskType.SUMMARIZATION)

    with pytest.raises(AIRoutingPolicyError, match="no AI route configured"):
        router.candidate_provider_ids(request)
