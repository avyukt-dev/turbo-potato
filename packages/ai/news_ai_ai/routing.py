"""Configuration-driven AI provider routing with bounded fallback."""

from __future__ import annotations

from enum import StrEnum

from news_ai_common.config import ConfigDomain, ConfigLoader
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import AIRequest, AIResponse, AITaskType, ProviderId, ProviderLocality
from .provider import (
    AIContextTooLargeError,
    AIInvalidResponseError,
    AILocalResourceExhaustedError,
    AIProviderError,
    AIProviderPolicyError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)
from .registry import AIProviderRegistry


class AIRoutingMode(StrEnum):
    LOCAL = "LOCAL"
    CLOUD = "CLOUD"
    HYBRID = "HYBRID"


class AIFailureReason(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    CONTEXT_TOO_LARGE = "CONTEXT_TOO_LARGE"
    LOCAL_RESOURCE_EXHAUSTED = "LOCAL_RESOURCE_EXHAUSTED"
    POLICY_REJECTION = "POLICY_REJECTION"
    OTHER = "OTHER"


class AIRouteAttemptOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class TaskRoutingPolicy(BaseModel):
    """Ordered provider preference and explicitly allowed fallback failures for one task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    providers: tuple[ProviderId, ...]
    fallback_on: frozenset[AIFailureReason] = frozenset()

    @field_validator("providers")
    @classmethod
    def validate_providers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("task routing policy must contain at least one provider")
        if len(value) != len(set(value)):
            raise ValueError("task routing providers must be unique")
        return value

    @field_validator("fallback_on")
    @classmethod
    def forbid_unsafe_fallback_reasons(
        cls,
        value: frozenset[AIFailureReason],
    ) -> frozenset[AIFailureReason]:
        forbidden = {AIFailureReason.POLICY_REJECTION, AIFailureReason.OTHER}
        if value & forbidden:
            raise ValueError("policy rejection and unknown failures cannot authorize fallback")
        return value


class AIRoutingConfig(BaseModel):
    """Routing policy owned exclusively by config/models/routing.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    mode: AIRoutingMode = AIRoutingMode.HYBRID
    routes: dict[AITaskType, TaskRoutingPolicy]
    sensitivity_provider_allowlists: dict[str, frozenset[ProviderId]] = Field(
        default_factory=dict
    )

    @field_validator("routes")
    @classmethod
    def require_routes(
        cls,
        value: dict[AITaskType, TaskRoutingPolicy],
    ) -> dict[AITaskType, TaskRoutingPolicy]:
        if not value:
            raise ValueError("AI routing config must define at least one task route")
        return value

    @field_validator("sensitivity_provider_allowlists")
    @classmethod
    def validate_sensitivity_allowlists(
        cls,
        value: dict[str, frozenset[str]],
    ) -> dict[str, frozenset[str]]:
        for sensitivity, providers in value.items():
            if not sensitivity.strip():
                raise ValueError("sensitivity routing keys must not be blank")
            if not providers:
                raise ValueError("sensitivity provider allowlists must not be empty")
        return value


class AIRoutingConfigLoader:
    """Load the routing policy from the canonical model-configuration domain."""

    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader

    def load(self) -> AIRoutingConfig:
        return self.loader.load_domain_file(
            ConfigDomain.MODELS,
            "routing.yaml",
            AIRoutingConfig,
        )


class AIRouteAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ProviderId
    outcome: AIRouteAttemptOutcome
    failure_reason: AIFailureReason | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> AIRouteAttempt:
        if self.outcome is AIRouteAttemptOutcome.SUCCESS and self.failure_reason is not None:
            raise ValueError("successful route attempt cannot contain a failure reason")
        if self.outcome is AIRouteAttemptOutcome.FAILED and self.failure_reason is None:
            raise ValueError("failed route attempt must contain a failure reason")
        return self


class AIRoutedResponse(BaseModel):
    """Successful response plus the bounded fallback history used to obtain it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    response: AIResponse
    attempts: tuple[AIRouteAttempt, ...]


class AIRoutingError(AIProviderError):
    """Base failure for configuration-driven AI routing."""


class AIRoutingPolicyError(AIRoutingError):
    """Routing policy cannot authorize any provider for the request."""


class AIRoutingExecutionError(AIRoutingError):
    """Execution failed before an allowed fallback could produce a response."""

    def __init__(self, attempts: tuple[AIRouteAttempt, ...]) -> None:
        self.attempts = attempts
        last = attempts[-1] if attempts else None
        reason = last.failure_reason.value if last and last.failure_reason else "UNKNOWN"
        super().__init__(f"AI routing execution failed: {reason}")


class AIRouter:
    """Select providers from configured policy and execute bounded, policy-safe fallback."""

    def __init__(self, registry: AIProviderRegistry, config: AIRoutingConfig) -> None:
        self.registry = registry
        self.config = config
        self._validate_provider_references()

    def candidate_provider_ids(self, request: AIRequest) -> tuple[str, ...]:
        policy = self.config.routes.get(request.task_type)
        if policy is None:
            raise AIRoutingPolicyError(f"no AI route configured for task {request.task_type.value}")

        self._require_sensitivity_policy(request)
        candidates: list[str] = []
        for provider_id in policy.providers:
            provider = self.registry.get(provider_id)
            if not self._mode_allows(provider.capabilities.locality):
                continue
            if not self._sensitivity_allows(provider_id, request.sensitivity):
                continue
            if not provider.capabilities.supports(request):
                continue
            candidates.append(provider_id)

        if not candidates:
            raise AIRoutingPolicyError("no configured AI provider is authorized and compatible")
        return tuple(candidates)

    async def execute(self, request: AIRequest) -> AIRoutedResponse:
        policy = self.config.routes.get(request.task_type)
        if policy is None:
            raise AIRoutingPolicyError(f"no AI route configured for task {request.task_type.value}")

        candidates = self.candidate_provider_ids(request)
        attempts: list[AIRouteAttempt] = []
        for index, provider_id in enumerate(candidates):
            try:
                response = await self.registry.execute(provider_id, request)
            except AIProviderError as exc:
                reason = _failure_reason(exc)
                attempts.append(
                    AIRouteAttempt(
                        provider_id=provider_id,
                        outcome=AIRouteAttemptOutcome.FAILED,
                        failure_reason=reason,
                    )
                )
                has_next = index + 1 < len(candidates)
                if not has_next or reason not in policy.fallback_on:
                    raise AIRoutingExecutionError(tuple(attempts)) from exc
                continue

            attempts.append(
                AIRouteAttempt(
                    provider_id=provider_id,
                    outcome=AIRouteAttemptOutcome.SUCCESS,
                )
            )
            return AIRoutedResponse(response=response, attempts=tuple(attempts))

        raise AIRoutingExecutionError(tuple(attempts))

    def _validate_provider_references(self) -> None:
        referenced = {
            provider_id
            for policy in self.config.routes.values()
            for provider_id in policy.providers
        }
        referenced.update(
            provider_id
            for providers in self.config.sensitivity_provider_allowlists.values()
            for provider_id in providers
        )
        for provider_id in sorted(referenced):
            try:
                self.registry.get(provider_id)
            except KeyError as exc:
                raise AIRoutingPolicyError(
                    f"AI routing config references unregistered provider {provider_id!r}"
                ) from exc

    def _mode_allows(self, locality: ProviderLocality) -> bool:
        if self.config.mode is AIRoutingMode.HYBRID:
            return True
        if self.config.mode is AIRoutingMode.LOCAL:
            return locality is ProviderLocality.LOCAL
        return locality is ProviderLocality.CLOUD

    def _require_sensitivity_policy(self, request: AIRequest) -> None:
        missing = sorted(
            sensitivity
            for sensitivity in request.sensitivity
            if sensitivity not in self.config.sensitivity_provider_allowlists
        )
        if missing:
            raise AIRoutingPolicyError(
                f"no AI provider allowlist configured for sensitivity: {', '.join(missing)}"
            )

    def _sensitivity_allows(self, provider_id: str, sensitivities: tuple[str, ...]) -> bool:
        return all(
            provider_id in self.config.sensitivity_provider_allowlists[sensitivity]
            for sensitivity in sensitivities
        )


def _failure_reason(error: AIProviderError) -> AIFailureReason:
    if isinstance(error, AIProviderPolicyError):
        return AIFailureReason.POLICY_REJECTION
    if isinstance(error, AIProviderTimeoutError):
        return AIFailureReason.TIMEOUT
    if isinstance(error, AIProviderRateLimitError):
        return AIFailureReason.RATE_LIMIT
    if isinstance(error, AIContextTooLargeError):
        return AIFailureReason.CONTEXT_TOO_LARGE
    if isinstance(error, AILocalResourceExhaustedError):
        return AIFailureReason.LOCAL_RESOURCE_EXHAUSTED
    if isinstance(error, AIInvalidResponseError):
        return AIFailureReason.INVALID_RESPONSE
    if isinstance(error, AIProviderUnavailableError):
        return AIFailureReason.UNAVAILABLE
    return AIFailureReason.OTHER
