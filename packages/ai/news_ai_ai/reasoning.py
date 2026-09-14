"""Deterministic reasoning-effort selection for production AI work."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

from .contracts import AIReasoningEffort, AIReasoningReason

REASONING_ROUTING_POLICY_VERSION = "reasoning-routing-policy-v1"


class ReasoningDecision(BaseModel):
    """Versioned reasoning decision derived only from application-owned signals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = REASONING_ROUTING_POLICY_VERSION
    effort: AIReasoningEffort
    reasons: tuple[AIReasoningReason, ...] = ()

    @model_validator(mode="after")
    def validate_decision(self) -> ReasoningDecision:
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("reasoning escalation reasons must be unique")
        if self.effort is AIReasoningEffort.HIGH and not self.reasons:
            raise ValueError("HIGH reasoning requires at least one escalation reason")
        if self.effort is not AIReasoningEffort.HIGH and self.reasons:
            raise ValueError("non-HIGH reasoning cannot contain escalation reasons")
        return self

    def request_updates(self) -> dict[str, object]:
        """Return typed AIRequest fields without coupling callers to field spelling."""

        return {
            "reasoning_effort": self.effort,
            "reasoning_policy_version": self.policy_version,
            "reasoning_reasons": self.reasons,
        }


def select_reasoning_effort(
    *,
    baseline: AIReasoningEffort = AIReasoningEffort.MEDIUM,
    high_risk: bool = False,
    attribution_or_intent: bool = False,
    causal_reasoning: bool = False,
    credible_source_conflict: bool = False,
) -> ReasoningDecision:
    """Choose effort from deterministic facts; a model never self-escalates.

    LOW is allowed only when a caller explicitly owns a routine constrained task.
    Current factual production stages use MEDIUM as their normal baseline. Material
    deterministic signals raise either LOW or MEDIUM to HIGH.
    """

    if baseline is AIReasoningEffort.HIGH:
        raise ValueError("HIGH must result from an explicit escalation reason")

    reasons: list[AIReasoningReason] = []
    if high_risk:
        reasons.append(AIReasoningReason.HIGH_RISK)
    if attribution_or_intent:
        reasons.append(AIReasoningReason.ATTRIBUTION_OR_INTENT)
    if causal_reasoning:
        reasons.append(AIReasoningReason.CAUSAL_REASONING)
    if credible_source_conflict:
        reasons.append(AIReasoningReason.CREDIBLE_SOURCE_CONFLICT)

    return ReasoningDecision(
        effort=AIReasoningEffort.HIGH if reasons else baseline,
        reasons=tuple(reasons),
    )
