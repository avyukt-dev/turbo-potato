"""Deterministic reasoning signals derived from immutable editorial facts."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from news_ai_ai import ReasoningDecision, select_reasoning_effort
from news_ai_domain import ClaimSemanticType, ClaimVerificationStatus, RiskLevel

from .contracts import BriefClaim, EditorialBrief

_UNRESOLVED_STATUSES = frozenset(
    {
        ClaimVerificationStatus.PARTIALLY_SUPPORTED,
        ClaimVerificationStatus.DISPUTED,
        ClaimVerificationStatus.UNVERIFIED,
    }
)


def editorial_reasoning_decision(
    brief: EditorialBrief,
    *,
    claim_ids: Iterable[UUID] | None = None,
) -> ReasoningDecision:
    """Select MEDIUM/HIGH from durable risk, verification, and claim semantics."""

    claims = _selected_claims(brief, claim_ids)
    return select_reasoning_effort(
        high_risk=brief.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL},
        credible_source_conflict=any(
            claim.status is ClaimVerificationStatus.DISPUTED for claim in claims
        ),
        attribution_or_intent=any(
            _is_unresolved(claim)
            and claim.semantics is not None
            and claim.semantics.semantic_type
            in {ClaimSemanticType.ATTRIBUTION, ClaimSemanticType.POLICY_COMMITMENT}
            for claim in claims
        ),
        causal_reasoning=any(
            _is_unresolved(claim)
            and claim.semantics is not None
            and claim.semantics.semantic_type is ClaimSemanticType.CAUSAL
            for claim in claims
        ),
    )


def _selected_claims(
    brief: EditorialBrief,
    claim_ids: Iterable[UUID] | None,
) -> tuple[BriefClaim, ...]:
    if claim_ids is None:
        return brief.claims
    requested = tuple(claim_ids)
    if len(requested) != len(set(requested)):
        raise ValueError("reasoning claim_ids must be unique")
    by_id = {claim.claim_id: claim for claim in brief.claims}
    if any(claim_id not in by_id for claim_id in requested):
        raise ValueError("reasoning claim_ids must belong to the Editorial Brief")
    return tuple(by_id[claim_id] for claim_id in requested)


def _is_unresolved(claim: BriefClaim) -> bool:
    return claim.status in _UNRESOLVED_STATUSES
