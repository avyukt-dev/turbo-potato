from __future__ import annotations

import pytest
from news_ai_ai import (
    REASONING_ROUTING_POLICY_VERSION,
    AIReasoningEffort,
    AIReasoningReason,
    ReasoningDecision,
    escalate_reasoning_for_validation,
    select_reasoning_effort,
)
from pydantic import ValidationError


def test_normal_factual_work_remains_medium_under_versioned_policy() -> None:
    decision = select_reasoning_effort()

    assert decision.policy_version == REASONING_ROUTING_POLICY_VERSION
    assert decision.effort is AIReasoningEffort.MEDIUM
    assert decision.reasons == ()


def test_explicit_routine_baseline_may_remain_low() -> None:
    decision = select_reasoning_effort(baseline=AIReasoningEffort.LOW)

    assert decision.effort is AIReasoningEffort.LOW
    assert decision.reasons == ()


@pytest.mark.parametrize(
    ("signal", "reason"),
    [
        ("high_risk", AIReasoningReason.HIGH_RISK),
        ("attribution_or_intent", AIReasoningReason.ATTRIBUTION_OR_INTENT),
        ("causal_reasoning", AIReasoningReason.CAUSAL_REASONING),
        ("credible_source_conflict", AIReasoningReason.CREDIBLE_SOURCE_CONFLICT),
    ],
)
def test_material_signal_escalates_to_high(signal: str, reason: AIReasoningReason) -> None:
    decision = select_reasoning_effort(**{signal: True})

    assert decision.effort is AIReasoningEffort.HIGH
    assert decision.reasons == (reason,)


def test_multiple_reasons_are_retained_in_stable_order() -> None:
    decision = select_reasoning_effort(
        high_risk=True,
        causal_reasoning=True,
        credible_source_conflict=True,
    )

    assert decision.reasons == (
        AIReasoningReason.HIGH_RISK,
        AIReasoningReason.CAUSAL_REASONING,
        AIReasoningReason.CREDIBLE_SOURCE_CONFLICT,
    )


def test_low_baseline_still_escalates_to_high_for_material_signal() -> None:
    decision = select_reasoning_effort(
        baseline=AIReasoningEffort.LOW,
        high_risk=True,
    )

    assert decision.effort is AIReasoningEffort.HIGH
    assert decision.reasons == (AIReasoningReason.HIGH_RISK,)


def test_high_cannot_be_an_unexplained_baseline() -> None:
    with pytest.raises(ValueError, match="explicit escalation reason"):
        select_reasoning_effort(baseline=AIReasoningEffort.HIGH)


def test_reasoning_decision_enforces_reason_consistency() -> None:
    with pytest.raises(ValidationError, match="at least one escalation reason"):
        ReasoningDecision(effort=AIReasoningEffort.HIGH)

    with pytest.raises(ValidationError, match="non-HIGH"):
        ReasoningDecision(
            effort=AIReasoningEffort.MEDIUM,
            reasons=(AIReasoningReason.HIGH_RISK,),
        )


def test_validation_failure_escalation_is_bounded_and_provenance_ready() -> None:
    initial = select_reasoning_effort()
    escalated = escalate_reasoning_for_validation(initial)
    repeated = escalate_reasoning_for_validation(escalated)

    assert escalated.effort is AIReasoningEffort.HIGH
    assert escalated.reasons == (AIReasoningReason.VALIDATION_FAILURE,)
    assert repeated == escalated
