from __future__ import annotations

from uuid import uuid4

from news_ai_ai import AIReasoningEffort, AIReasoningReason
from news_ai_content.contracts import (
    BriefClaim,
    ContentFormat,
    ContentPlatform,
    ContentTarget,
    EditorialBrief,
)
from news_ai_content.reasoning import editorial_reasoning_decision
from news_ai_domain import (
    CLAIM_SEMANTICS_POLICY_VERSION,
    ClaimSemantics,
    ClaimSemanticState,
    ClaimSemanticType,
    ClaimVerificationStatus,
    FactCheckLabel,
    RiskLevel,
)


def _claim(
    *,
    status: ClaimVerificationStatus = ClaimVerificationStatus.SUPPORTED,
    semantic_type: ClaimSemanticType = ClaimSemanticType.GENERAL_FACT,
) -> BriefClaim:
    return BriefClaim(
        claim_id=uuid4(),
        text="A factual claim.",
        status=status,
        fact_check_id=uuid4(),
        label=(
            FactCheckLabel.UNVERIFIED
            if status
            in {
                ClaimVerificationStatus.DISPUTED,
                ClaimVerificationStatus.UNVERIFIED,
            }
            else FactCheckLabel.TRUE
        ),
        semantics=ClaimSemantics(
            policy_version=CLAIM_SEMANTICS_POLICY_VERSION,
            semantic_type=semantic_type,
            semantic_state=ClaimSemanticState.OBSERVED,
        ),
    )


def _brief(*claims: BriefClaim, risk_level: RiskLevel = RiskLevel.LOW) -> EditorialBrief:
    return EditorialBrief(
        story_id=uuid4(),
        fact_sheet_id=uuid4(),
        fact_sheet_version=1,
        headline="Headline",
        summary="Summary",
        editorial_angle="Explain the verified state.",
        key_points=("Point",),
        exclusions=(),
        tone="neutral",
        claims=claims or (_claim(),),
        risk_level=risk_level,
        style_rules={},
        target=ContentTarget(
            platform=ContentPlatform.INSTAGRAM,
            format=ContentFormat.CAROUSEL,
        ),
        human_review_required=True,
    )


def test_supported_routine_editorial_work_stays_medium() -> None:
    decision = editorial_reasoning_decision(_brief(_claim()))

    assert decision.effort is AIReasoningEffort.MEDIUM
    assert decision.reasons == ()


def test_high_or_critical_story_risk_escalates_to_high() -> None:
    for risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        decision = editorial_reasoning_decision(_brief(_claim(), risk_level=risk_level))
        assert decision.effort is AIReasoningEffort.HIGH
        assert AIReasoningReason.HIGH_RISK in decision.reasons


def test_disputed_claim_records_credible_source_conflict() -> None:
    decision = editorial_reasoning_decision(
        _brief(_claim(status=ClaimVerificationStatus.DISPUTED))
    )

    assert decision.effort is AIReasoningEffort.HIGH
    assert AIReasoningReason.CREDIBLE_SOURCE_CONFLICT in decision.reasons


def test_unresolved_attribution_policy_intent_and_causality_escalate() -> None:
    attribution = editorial_reasoning_decision(
        _brief(
            _claim(
                status=ClaimVerificationStatus.UNVERIFIED,
                semantic_type=ClaimSemanticType.ATTRIBUTION,
            )
        )
    )
    policy_intent = editorial_reasoning_decision(
        _brief(
            _claim(
                status=ClaimVerificationStatus.PARTIALLY_SUPPORTED,
                semantic_type=ClaimSemanticType.POLICY_COMMITMENT,
            )
        )
    )
    causal = editorial_reasoning_decision(
        _brief(
            _claim(
                status=ClaimVerificationStatus.PARTIALLY_SUPPORTED,
                semantic_type=ClaimSemanticType.CAUSAL,
            )
        )
    )

    assert AIReasoningReason.ATTRIBUTION_OR_INTENT in attribution.reasons
    assert AIReasoningReason.ATTRIBUTION_OR_INTENT in policy_intent.reasons
    assert AIReasoningReason.CAUSAL_REASONING in causal.reasons


def test_resolved_attribution_does_not_escalate_by_semantic_type_alone() -> None:
    decision = editorial_reasoning_decision(
        _brief(_claim(semantic_type=ClaimSemanticType.ATTRIBUTION))
    )

    assert decision.effort is AIReasoningEffort.MEDIUM
    assert decision.reasons == ()


def test_claim_subset_ignores_unrelated_conflict() -> None:
    selected = _claim()
    unrelated = _claim(status=ClaimVerificationStatus.DISPUTED)
    brief = _brief(selected, unrelated)

    decision = editorial_reasoning_decision(brief, claim_ids=(selected.claim_id,))

    assert decision.effort is AIReasoningEffort.MEDIUM
    assert decision.reasons == ()
