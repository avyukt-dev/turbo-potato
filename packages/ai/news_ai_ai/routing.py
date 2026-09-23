"""Stage-centric AI routing with bounded, policy-authorized fallback."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from news_ai_common.config import ConfigError, ConfigLoader
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .contracts import (
    AIReasoningEffort,
    AIReasoningReason,
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AITaskType,
    ModelId,
    ProviderId,
    ProviderLocality,
)
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
from .reasoning import REASONING_ROUTING_POLICY_VERSION
from .registry import AIProviderRegistry

ResponseValidator = Callable[[AIResponse], None]


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


class AIStageId(StrEnum):
    CLAIM_EXTRACTION = "claim-extraction"
    EVIDENCE_ASSESSMENT = "evidence-assessment"
    CONTENT_GENERATION = "content-generation"
    QUALITY_CHECKING = "quality-checking"


_STAGE_TASKS: dict[AIStageId, AITaskType] = {
    AIStageId.CLAIM_EXTRACTION: AITaskType.CLAIM_EXTRACTION,
    AIStageId.EVIDENCE_ASSESSMENT: AITaskType.EVIDENCE_ASSESSMENT,
    AIStageId.CONTENT_GENERATION: AITaskType.CONTENT_GENERATION,
    AIStageId.QUALITY_CHECKING: AITaskType.QUALITY_CHECKING,
}
_TASK_STAGES = {task: stage for stage, task in _STAGE_TASKS.items()}
_STAGE_PROMPT_IDS: dict[AIStageId, str] = {
    AIStageId.CLAIM_EXTRACTION: "claim-extraction",
    AIStageId.EVIDENCE_ASSESSMENT: "evidence-assessment",
    AIStageId.CONTENT_GENERATION: "content-generation",
    AIStageId.QUALITY_CHECKING: "content-quality",
}


class AIStageProviderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: ProviderId
    model: ModelId


class AIStagePromptConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    prompt_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9-]*$")
    version: str = Field(min_length=2, max_length=64, pattern=r"^v[1-9][0-9]*$")
    path: str = Field(min_length=1, max_length=512)

    @field_validator("path")
    @classmethod
    def require_safe_prompt_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or path.suffix.lower() != ".txt" or ".." in path.parts:
            raise ValueError("prompt path must be a safe relative text path")
        return value


class AIStageRequestDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reasoning_effort: AIReasoningEffort | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    rate_limit_retry_delays_seconds: tuple[float, ...] = Field(default=(), max_length=5)

    @field_validator("rate_limit_retry_delays_seconds")
    @classmethod
    def require_bounded_rate_limit_delays(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(delay < 0 or delay > 300 for delay in value):
            raise ValueError("rate-limit retry delays must be between 0 and 300 seconds")
        return value

    @field_validator("reasoning_effort")
    @classmethod
    def forbid_unexplained_high_default(
        cls, value: AIReasoningEffort | None
    ) -> AIReasoningEffort | None:
        if value is AIReasoningEffort.HIGH:
            raise ValueError(
                "stage default HIGH reasoning requires deterministic runtime escalation"
            )
        return value


class AIStageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    stage_id: AIStageId
    task_type: AITaskType
    prompt: AIStagePromptConfig
    providers: tuple[AIStageProviderSelection, ...]
    fallback_on: frozenset[AIFailureReason] = frozenset()
    request_defaults: AIStageRequestDefaults = Field(default_factory=AIStageRequestDefaults)

    @field_validator("providers")
    @classmethod
    def require_unique_providers(
        cls, value: tuple[AIStageProviderSelection, ...]
    ) -> tuple[AIStageProviderSelection, ...]:
        if not value:
            raise ValueError("stage configuration must contain at least one provider")
        routes = [(item.provider_id, item.model) for item in value]
        if len(routes) != len(set(routes)):
            raise ValueError("stage provider/model routes must be unique")
        return value

    @field_validator("fallback_on")
    @classmethod
    def forbid_unsafe_fallback_reasons(
        cls, value: frozenset[AIFailureReason]
    ) -> frozenset[AIFailureReason]:
        if value & {AIFailureReason.POLICY_REJECTION, AIFailureReason.OTHER}:
            raise ValueError("policy rejection and unknown failures cannot authorize fallback")
        return value


class AIPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    mode: AIRoutingMode = AIRoutingMode.HYBRID
    sensitivity_provider_allowlists: dict[str, frozenset[ProviderId]]

    @field_validator("sensitivity_provider_allowlists")
    @classmethod
    def validate_sensitivity_allowlists(
        cls, value: dict[str, frozenset[str]]
    ) -> dict[str, frozenset[str]]:
        for sensitivity, providers in value.items():
            if not sensitivity.strip():
                raise ValueError("sensitivity routing keys must not be blank")
            if not providers:
                raise ValueError("sensitivity provider allowlists must not be empty")
        return value


class AIPolicyConfigLoader:
    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader

    def load(self) -> AIPolicyConfig:
        return self.loader.load_model("models/policy.yaml", AIPolicyConfig)


class AIStageConfigLoader:
    """Load the closed set of production stages; arbitrary directory files are ignored."""

    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader

    def load(self, stage_id: AIStageId) -> AIStageConfig:
        config = self.loader.load_model(f"models/stages/{stage_id.value}.yaml", AIStageConfig)
        if config.stage_id is not stage_id:
            raise ConfigError(f"stage configuration identity does not match {stage_id.value}")
        if config.task_type is not _STAGE_TASKS[stage_id]:
            raise ConfigError(f"stage configuration task does not match {stage_id.value}")
        if config.prompt.prompt_id != _STAGE_PROMPT_IDS[stage_id]:
            raise ConfigError(f"prompt identity does not match {stage_id.value}")
        self.resolve_prompt(config)
        return config

    def load_all(self) -> dict[AIStageId, AIStageConfig]:
        return {stage_id: self.load(stage_id) for stage_id in AIStageId}

    def resolve_prompt(self, config: AIStageConfig) -> Path:
        candidate = (self.loader.root / config.prompt.path).resolve()
        try:
            candidate.relative_to(self.loader.root)
        except ValueError as exc:
            raise ConfigError("prompt path escapes configured root") from exc
        if candidate.suffix.lower() != ".txt":
            raise ConfigError("prompt configuration must reference a text file")
        if not candidate.is_file():
            raise ConfigError(f"prompt file not found: {config.prompt.path}")
        return candidate


class AIRouteAttemptOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class AIRouteAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: ProviderId
    model: ModelId | None = None
    reasoning_effort: AIReasoningEffort | None = None
    reasoning_policy_version: str | None = None
    reasoning_reasons: tuple[AIReasoningReason, ...] = ()
    outcome: AIRouteAttemptOutcome
    failure_reason: AIFailureReason | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> AIRouteAttempt:
        if self.outcome is AIRouteAttemptOutcome.SUCCESS and self.failure_reason is not None:
            raise ValueError("successful route attempt cannot contain a failure reason")
        if self.outcome is AIRouteAttemptOutcome.FAILED and self.failure_reason is None:
            raise ValueError("failed route attempt must contain a failure reason")
        if self.reasoning_reasons and self.reasoning_policy_version is None:
            raise ValueError("reasoning attempt reasons require a policy version")
        if self.reasoning_reasons and self.reasoning_effort is not AIReasoningEffort.HIGH:
            raise ValueError("reasoning attempt reasons require HIGH effort")
        return self


class AIFallbackDecision(StrEnum):
    EXHAUSTED = "EXHAUSTED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


class AIRoutingFailureProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_type: AITaskType
    prompt_id: str | None = None
    prompt_version: str | None = None
    prompt_checksum: str | None = None
    input_artifact_ids: tuple[str, ...] = ()
    input_hash: str | None = None
    correlation_id: UUID | None = None
    attempts: tuple[AIRouteAttempt, ...] = Field(min_length=1)
    final_failure_reason: AIFailureReason
    fallback_decision: AIFallbackDecision

    @model_validator(mode="after")
    def validate_terminal_failure(self) -> AIRoutingFailureProvenance:
        last = self.attempts[-1]
        if last.outcome is not AIRouteAttemptOutcome.FAILED or last.failure_reason is None:
            raise ValueError("AI routing failure provenance must end with a failed attempt")
        if last.failure_reason is not self.final_failure_reason:
            raise ValueError("final failure reason must match the last route attempt")
        return self

    @classmethod
    def from_request(
        cls,
        request: AIRequest,
        attempts: tuple[AIRouteAttempt, ...],
        fallback_decision: AIFallbackDecision,
    ) -> AIRoutingFailureProvenance:
        if not attempts:
            raise ValueError("AI routing failure provenance requires at least one attempt")
        prompt = request.prompt
        final_failure_reason = attempts[-1].failure_reason
        if final_failure_reason is None:
            raise ValueError("AI routing failure provenance requires a failed final attempt")
        return cls(
            task_type=request.task_type,
            prompt_id=prompt.prompt_id if prompt else None,
            prompt_version=prompt.version if prompt else None,
            prompt_checksum=prompt.checksum if prompt else None,
            input_artifact_ids=request.input_artifact_ids,
            input_hash=request.input_hash,
            correlation_id=request.correlation_id,
            attempts=attempts,
            final_failure_reason=final_failure_reason,
            fallback_decision=fallback_decision,
        )


class AIRoutedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    response: AIResponse
    attempts: tuple[AIRouteAttempt, ...]


class AIRoutingError(AIProviderError):
    pass


class AIRoutingPolicyError(AIRoutingError):
    pass


class AIRoutingExecutionError(AIRoutingError):
    def __init__(
        self,
        attempts: tuple[AIRouteAttempt, ...],
        *,
        provenance: AIRoutingFailureProvenance | None = None,
    ) -> None:
        self.attempts = attempts
        self.provenance = provenance
        self.fallback_decision = provenance.fallback_decision if provenance else None
        last = attempts[-1] if attempts else None
        reason = last.failure_reason.value if last and last.failure_reason else "UNKNOWN"
        super().__init__(f"AI routing execution failed: {reason}")

    @classmethod
    def from_request(
        cls,
        request: AIRequest,
        attempts: tuple[AIRouteAttempt, ...],
        fallback_decision: AIFallbackDecision,
    ) -> AIRoutingExecutionError:
        provenance = AIRoutingFailureProvenance.from_request(
            request,
            attempts,
            fallback_decision,
        )
        return cls(attempts, provenance=provenance)

    @property
    def failure_provenance_payload(self) -> dict[str, Any] | None:
        if self.provenance is None:
            return None
        return self.provenance.model_dump(mode="json")


def is_retryable_routing_failure(error: AIRoutingExecutionError) -> bool:
    """Return whether at least one transient route can still make progress safely."""

    reasons = {
        attempt.failure_reason for attempt in error.attempts if attempt.failure_reason is not None
    }
    hard_failures = {
        AIFailureReason.CONTEXT_TOO_LARGE,
        AIFailureReason.POLICY_REJECTION,
        AIFailureReason.OTHER,
    }
    transient_failures = {
        AIFailureReason.UNAVAILABLE,
        AIFailureReason.TIMEOUT,
        AIFailureReason.RATE_LIMIT,
        AIFailureReason.LOCAL_RESOURCE_EXHAUSTED,
    }
    return not bool(reasons & hard_failures) and bool(reasons & transient_failures)


def _validated_request_update(request: AIRequest, **updates: object) -> AIRequest:
    try:
        return AIRequest.model_validate({**request.model_dump(mode="python"), **updates})
    except ValidationError as exc:
        raise AIRoutingPolicyError("AI request violates execution contract") from exc


class AIRouter:
    """Select a provider/model from the stage and apply global authorization policy."""

    def __init__(
        self,
        registry: AIProviderRegistry,
        policy: AIPolicyConfig,
        stages: Mapping[AIStageId, AIStageConfig],
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.stages = dict(stages)
        self._validate_configuration()

    def stage_config(self, task_type: AITaskType) -> AIStageConfig:
        stage_id = _TASK_STAGES.get(task_type)
        if stage_id is None or stage_id not in self.stages:
            raise AIRoutingPolicyError(f"no AI stage configured for task {task_type.value}")
        return self.stages[stage_id]

    def candidate_provider_ids(self, request: AIRequest) -> tuple[str, ...]:
        return tuple(item.provider_id for item in self._candidate_selections(request))

    def _candidate_selections(self, request: AIRequest) -> tuple[AIStageProviderSelection, ...]:
        stage = self.stage_config(request.task_type)
        self._reject_conflicting_model(request, stage)
        self._require_sensitivity_policy(request)
        candidates: list[AIStageProviderSelection] = []
        for selection in stage.providers:
            provider = self.registry.get(selection.provider_id)
            attempt_request = self._attempt_request(request, stage, selection)
            if not self._mode_allows(provider.capabilities.locality):
                continue
            if not self._sensitivity_allows(selection.provider_id, request.sensitivity):
                continue
            if not provider.capabilities.supports(attempt_request):
                continue
            candidates.append(selection)
        if not candidates:
            raise AIRoutingPolicyError("no configured AI provider is authorized and compatible")
        return tuple(candidates)

    def validate_stage(self, task_type: AITaskType) -> None:
        self.candidate_provider_ids(
            AIRequest(
                task_type=task_type,
                system_prompt="stage route validation",
                input={},
                response_format=AIResponseFormat.STRUCTURED,
            )
        )

    async def execute(
        self, request: AIRequest, *, response_validator: ResponseValidator | None = None
    ) -> AIRoutedResponse:
        stage = self.stage_config(request.task_type)
        candidates = self._candidate_selections(request)
        attempts: list[AIRouteAttempt] = []
        route_request = request

        for index, selection in enumerate(candidates):
            provider_id = selection.provider_id
            provider = self.registry.get(provider_id)
            attempt_request = self._attempt_request(route_request, stage, selection)
            rate_limit_retry_index = 0

            while True:
                try:
                    response = await self.registry.execute(provider_id, attempt_request)
                    if response_validator is not None:
                        response_validator(response)
                except AIProviderError as exc:
                    reason = _failure_reason(exc)
                    attempts.append(self._attempt_record(selection, attempt_request, reason))
                    if reason is AIFailureReason.RATE_LIMIT and rate_limit_retry_index < len(
                        stage.request_defaults.rate_limit_retry_delays_seconds
                    ):
                        configured_delay = stage.request_defaults.rate_limit_retry_delays_seconds[
                            rate_limit_retry_index
                        ]
                        provider_delay = (
                            exc.retry_after_seconds
                            if isinstance(exc, AIProviderRateLimitError)
                            and exc.retry_after_seconds is not None
                            else 0
                        )
                        rate_limit_retry_index += 1
                        await asyncio.sleep(max(configured_delay, provider_delay))
                        continue
                    if self._should_escalate_validation(attempt_request, reason):
                        route_request = self._validation_escalated_request(route_request)
                        if provider.capabilities.honors_reasoning_effort:
                            attempt_request = self._attempt_request(route_request, stage, selection)
                            continue
                    if index + 1 >= len(candidates) or reason not in stage.fallback_on:
                        fallback_decision = (
                            AIFallbackDecision.NOT_AUTHORIZED
                            if reason not in stage.fallback_on
                            else AIFallbackDecision.EXHAUSTED
                        )
                        raise AIRoutingExecutionError.from_request(
                            request,
                            tuple(attempts),
                            fallback_decision,
                        ) from exc
                    break

                attempts.append(self._attempt_record(selection, attempt_request, None))
                return AIRoutedResponse(response=response, attempts=tuple(attempts))

        raise AIRoutingExecutionError.from_request(
            request,
            tuple(attempts),
            AIFallbackDecision.EXHAUSTED,
        )

    @staticmethod
    def _attempt_record(
        selection: AIStageProviderSelection,
        request: AIRequest,
        failure_reason: AIFailureReason | None,
    ) -> AIRouteAttempt:
        return AIRouteAttempt(
            provider_id=selection.provider_id,
            model=selection.model,
            reasoning_effort=request.reasoning_effort,
            reasoning_policy_version=request.reasoning_policy_version,
            reasoning_reasons=request.reasoning_reasons,
            outcome=(
                AIRouteAttemptOutcome.FAILED
                if failure_reason is not None
                else AIRouteAttemptOutcome.SUCCESS
            ),
            failure_reason=failure_reason,
        )

    @staticmethod
    def _should_escalate_validation(
        request: AIRequest,
        failure_reason: AIFailureReason,
    ) -> bool:
        return (
            failure_reason is AIFailureReason.INVALID_RESPONSE
            and request.reasoning_effort is AIReasoningEffort.MEDIUM
            and request.reasoning_policy_version in {None, REASONING_ROUTING_POLICY_VERSION}
        )

    @staticmethod
    def _validation_escalated_request(request: AIRequest) -> AIRequest:
        reasons = tuple(
            dict.fromkeys((*request.reasoning_reasons, AIReasoningReason.VALIDATION_FAILURE))
        )
        return _validated_request_update(
            request,
            reasoning_effort=AIReasoningEffort.HIGH,
            reasoning_policy_version=REASONING_ROUTING_POLICY_VERSION,
            reasoning_reasons=reasons,
        )

    def _validate_configuration(self) -> None:
        if set(self.stages) != set(AIStageId):
            raise AIRoutingPolicyError("all production AI stages must be configured")
        for stage_id, stage in self.stages.items():
            if stage.stage_id is not stage_id or stage.task_type is not _STAGE_TASKS[stage_id]:
                raise AIRoutingPolicyError("AI stage identity and task mapping must be canonical")
        referenced = {
            item.provider_id for stage in self.stages.values() for item in stage.providers
        }
        referenced.update(
            item
            for providers in self.policy.sensitivity_provider_allowlists.values()
            for item in providers
        )
        for provider_id in sorted(referenced):
            try:
                provider = self.registry.get(provider_id)
            except KeyError as exc:
                raise AIRoutingPolicyError(
                    f"AI configuration references unregistered provider {provider_id!r}"
                ) from exc
            for stage in self.stages.values():
                for selection in stage.providers:
                    if selection.provider_id != provider_id:
                        continue
                    if (
                        provider.capabilities.models
                        and selection.model not in provider.capabilities.models
                    ):
                        raise AIRoutingPolicyError(
                            f"AI stage model is not exposed by provider {provider_id!r}"
                        )
                    tasks = provider.capabilities.model_task_types.get(selection.model)
                    formats = provider.capabilities.model_response_formats.get(selection.model)
                    if tasks is not None and stage.task_type not in tasks:
                        raise AIRoutingPolicyError(
                            f"AI stage task is not supported by provider model {selection.model!r}"
                        )
                    if formats is not None and AIResponseFormat.STRUCTURED not in formats:
                        raise AIRoutingPolicyError(
                            "AI stage response format is not supported by provider model "
                            f"{selection.model!r}"
                        )

    @staticmethod
    def _reject_conflicting_model(request: AIRequest, stage: AIStageConfig) -> None:
        if request.model is not None and request.model != stage.providers[0].model:
            raise AIRoutingPolicyError("request model conflicts with stage-owned configuration")

    @staticmethod
    def _attempt_request(
        request: AIRequest,
        stage: AIStageConfig,
        selection: AIStageProviderSelection,
    ) -> AIRequest:
        uses_stage_reasoning_default = (
            request.reasoning_effort is None and stage.request_defaults.reasoning_effort is not None
        )
        attempt = _validated_request_update(
            request,
            model=selection.model,
            max_tokens=(
                request.max_tokens
                if request.max_tokens is not None
                else stage.request_defaults.max_tokens
            ),
            reasoning_effort=(
                request.reasoning_effort
                if request.reasoning_effort is not None
                else stage.request_defaults.reasoning_effort
            ),
            reasoning_policy_version=(
                REASONING_ROUTING_POLICY_VERSION
                if uses_stage_reasoning_default and request.reasoning_policy_version is None
                else request.reasoning_policy_version
            ),
        )
        if (
            attempt.reasoning_policy_version is not None
            and attempt.reasoning_policy_version != REASONING_ROUTING_POLICY_VERSION
        ):
            raise AIRoutingPolicyError("reasoning policy version is not current")
        return attempt

    def _mode_allows(self, locality: ProviderLocality) -> bool:
        if self.policy.mode is AIRoutingMode.HYBRID:
            return True
        if self.policy.mode is AIRoutingMode.LOCAL:
            return locality is ProviderLocality.LOCAL
        return locality is ProviderLocality.CLOUD

    def _require_sensitivity_policy(self, request: AIRequest) -> None:
        missing = sorted(
            item
            for item in request.sensitivity
            if item not in self.policy.sensitivity_provider_allowlists
        )
        if missing:
            raise AIRoutingPolicyError(
                f"no AI provider allowlist configured for sensitivity: {', '.join(missing)}"
            )

    def _sensitivity_allows(self, provider_id: str, sensitivities: tuple[str, ...]) -> bool:
        return all(
            provider_id in self.policy.sensitivity_provider_allowlists[item]
            for item in sensitivities
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
