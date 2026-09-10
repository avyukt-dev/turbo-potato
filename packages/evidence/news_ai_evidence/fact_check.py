"""Deterministic claim verification and fact-check persistence.

This module evaluates explicit claim-evidence relationships. It never treats search ranking,
article count, or AI agreement as proof, and it preserves UNVERIFIED != REFUTED.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from news_ai_common.config import ConfigDomain, ConfigLoader
from news_ai_database import Claim, ClaimEvidence, EvidenceItem, FactCheck, Job, Story
from news_ai_domain import (
    ClaimVerificationStatus,
    FactCheckLabel,
    ReviewState,
    RiskLevel,
)
from news_ai_events import EventEnvelope, EventType
from news_ai_events.outbox import build_outbox_record
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .engine import EvidenceRelation


class FactCheckInvariants(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unverified_is_false: bool
    verdict_is_separate_from_claim_verification_status: bool
    count_independent_groups_not_articles: bool
    primary_evidence_can_satisfy_corroboration: bool


class FactCheckThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    credible_strength: Decimal = Field(ge=0, le=1)
    decisive_strength: Decimal = Field(ge=0, le=1)
    min_independent_groups: int = Field(ge=0, le=10)

    @model_validator(mode="after")
    def validate_strength_order(self) -> FactCheckThreshold:
        if self.decisive_strength < self.credible_strength:
            raise ValueError("decisive_strength must be >= credible_strength")
        return self


class FactCheckPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    labels: tuple[FactCheckLabel, ...]
    invariants: FactCheckInvariants
    thresholds: dict[RiskLevel, FactCheckThreshold]

    @model_validator(mode="after")
    def validate_canonical_policy(self) -> FactCheckPolicy:
        if set(self.labels) != set(FactCheckLabel) or len(self.labels) != len(FactCheckLabel):
            raise ValueError("fact-check policy must contain each canonical label exactly once")
        if self.invariants.unverified_is_false:
            raise ValueError("UNVERIFIED must never be configured as FALSE")
        if not self.invariants.verdict_is_separate_from_claim_verification_status:
            raise ValueError(
                "fact-check verdict must remain separate from claim verification status"
            )
        if not self.invariants.count_independent_groups_not_articles:
            raise ValueError("corroboration must count independent groups, not article count")
        if set(self.thresholds) != set(RiskLevel):
            raise ValueError("fact-check thresholds must define every canonical risk level")
        return self


class FactCheckPolicyLoader:
    def __init__(self, loader: ConfigLoader) -> None:
        self.loader = loader

    def load(self) -> FactCheckPolicy:
        return self.loader.load_domain_file(
            ConfigDomain.RESEARCH,
            "fact-check.yaml",
            FactCheckPolicy,
        )


class ClaimVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: UUID
    status: ClaimVerificationStatus
    label: FactCheckLabel
    supporting_count: int = Field(ge=0)
    contradicting_count: int = Field(ge=0)
    qualifying_count: int = Field(ge=0)
    primary_evidence_count: int = Field(ge=0)
    independent_support_groups: int = Field(ge=0)
    independent_contradiction_groups: int = Field(ge=0)
    strongest_support: Decimal = Field(ge=0, le=1)
    strongest_contradiction: Decimal = Field(ge=0, le=1)
    evidence_ids: tuple[UUID, ...]
    summary: str
    reasoning_summary: str


@dataclass(frozen=True, slots=True)
class FactCheckBatchResult:
    story_id: UUID
    event_id: UUID
    fact_check_ids: tuple[UUID, ...]
    claim_results: tuple[ClaimVerificationResult, ...]

    def as_handler_result(self) -> dict[str, Any]:
        return {
            "story_id": str(self.story_id),
            "event_id": str(self.event_id),
            "fact_check_ids": [str(item) for item in self.fact_check_ids],
            "claim_statuses": {
                str(item.claim_id): item.status.value for item in self.claim_results
            },
        }


@dataclass(frozen=True, slots=True)
class StoryVerificationResult:
    story_id: UUID
    event_id: UUID
    claim_ids: tuple[UUID, ...]

    def as_handler_result(self) -> dict[str, Any]:
        return {
            "story_id": str(self.story_id),
            "event_id": str(self.event_id),
            "claim_ids": [str(item) for item in self.claim_ids],
        }


@dataclass(frozen=True, slots=True)
class _EvidenceLink:
    evidence_id: UUID
    relation: EvidenceRelation
    strength: Decimal
    source_level: int | None
    independence_group: str | None


_SUPPORT_RELATIONS = {
    EvidenceRelation.DIRECT_SUPPORT,
    EvidenceRelation.INDIRECT_SUPPORT,
}


class FactCheckEngine:
    """Evaluate assessed evidence without converting research insufficiency into falsity."""

    def __init__(
        self,
        policy: FactCheckPolicy,
        *,
        producer: str = "research-worker",
        producer_version: str = "0.1.0",
    ) -> None:
        self.policy = policy
        self.producer = producer
        self.producer_version = producer_version

    def verify_evidence_collection(
        self,
        session: Session,
        event: EventEnvelope,
    ) -> FactCheckBatchResult:
        story_id, claim_ids, evidence_ids, research_run_id = self._parse_collected_event(event)
        story = session.get(Story, story_id)
        if story is None:
            raise ValueError("evidence.collected references missing story")
        research_job = session.get(Job, research_run_id)
        if research_job is None or research_job.job_type != "RESEARCH":
            raise ValueError("evidence.collected references missing research run")
        if research_job.status != "COMPLETED":
            raise ValueError("fact checking requires a completed research run")

        claims = self._load_claims(session, story_id, claim_ids)
        results: list[ClaimVerificationResult] = []
        fact_checks: list[FactCheck] = []
        for claim in claims:
            links = self._load_links(session, claim.id, evidence_ids)
            result = self._evaluate_claim(claim, links)
            claim.status = result.status
            fact_check = FactCheck(
                story_id=story_id,
                claim_id=claim.id,
                label=result.label,
                confidence_score=None,
                summary=result.summary,
                reasoning_summary=result.reasoning_summary,
                review_required=True,
                review_state=ReviewState.NOT_READY,
            )
            session.add(fact_check)
            fact_checks.append(fact_check)
            results.append(result)

        session.flush()
        completed = EventEnvelope(
            event_type=EventType.FACT_CHECK_COMPLETED,
            producer=self.producer,
            producer_version=self.producer_version,
            aggregate_type="story",
            aggregate_id=story_id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            idempotency_key=f"fact_check.completed:{event.event_id}",
            payload={
                "story_id": str(story_id),
                "claim_ids": [str(item) for item in claim_ids],
                "fact_check_ids": [str(item.id) for item in fact_checks],
                "research_run_id": str(research_run_id),
                "claim_statuses": {str(item.claim_id): item.status.value for item in results},
                "labels": {str(item.claim_id): item.label.value for item in results},
            },
        )
        session.add(build_outbox_record(completed))
        session.flush()
        return FactCheckBatchResult(
            story_id=story_id,
            event_id=completed.event_id,
            fact_check_ids=tuple(item.id for item in fact_checks),
            claim_results=tuple(results),
        )

    def mark_story_verified(
        self,
        session: Session,
        event: EventEnvelope,
    ) -> StoryVerificationResult:
        if event.event_type != EventType.FACT_CHECK_COMPLETED:
            raise ValueError("story verification requires fact_check.completed")
        if event.aggregate_type != "story":
            raise ValueError("fact_check.completed must use story aggregate")
        story_id = UUID(str(event.payload.get("story_id")))
        if story_id != event.aggregate_id:
            raise ValueError("fact_check.completed story_id must match aggregate_id")
        claim_ids = _uuid_tuple(event.payload.get("claim_ids"), "claim_ids")
        fact_check_ids = _uuid_tuple(event.payload.get("fact_check_ids"), "fact_check_ids")
        if len(claim_ids) != len(fact_check_ids):
            raise ValueError("fact_check.completed claim and fact-check counts must match")

        story = session.get(Story, story_id)
        if story is None:
            raise ValueError("fact_check.completed references missing story")
        claims = self._load_claims(session, story_id, claim_ids)
        if any(claim.status is ClaimVerificationStatus.UNASSESSED for claim in claims):
            raise ValueError("story cannot complete verification with UNASSESSED claims")

        checks = list(session.scalars(select(FactCheck).where(FactCheck.id.in_(fact_check_ids))))
        by_id = {item.id: item for item in checks}
        if set(by_id) != set(fact_check_ids):
            raise ValueError("fact_check.completed references missing fact-check rows")
        for claim_id, fact_check_id in zip(claim_ids, fact_check_ids, strict=True):
            check = by_id[fact_check_id]
            if check.story_id != story_id or check.claim_id != claim_id:
                raise ValueError("fact_check.completed references mismatched fact-check row")

        story.status = "VERIFIED"
        verified = EventEnvelope(
            event_type=EventType.STORY_VERIFIED,
            producer=self.producer,
            producer_version=self.producer_version,
            aggregate_type="story",
            aggregate_id=story_id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            idempotency_key=f"story.verified:{event.event_id}",
            payload={
                "story_id": str(story_id),
                "claim_ids": [str(item) for item in claim_ids],
                "fact_check_ids": [str(item) for item in fact_check_ids],
                "verification_stage_complete": True,
            },
        )
        session.add(build_outbox_record(verified))
        session.flush()
        return StoryVerificationResult(
            story_id=story_id,
            event_id=verified.event_id,
            claim_ids=claim_ids,
        )

    def _evaluate_claim(
        self,
        claim: Claim,
        links: tuple[_EvidenceLink, ...],
    ) -> ClaimVerificationResult:
        threshold = self.policy.thresholds[claim.risk_level]
        support = tuple(item for item in links if item.relation in _SUPPORT_RELATIONS)
        contradict = tuple(item for item in links if item.relation is EvidenceRelation.CONTRADICTS)
        qualify = tuple(item for item in links if item.relation is EvidenceRelation.QUALIFIES)
        support_score = _strongest(support)
        contradiction_score = _strongest(contradict)
        qualifier_score = _strongest(qualify)
        credible_support = support_score >= threshold.credible_strength
        credible_contradiction = contradiction_score >= threshold.credible_strength
        strong_support = support_score >= threshold.decisive_strength and self._corroborated(
            support, threshold
        )
        strong_contradiction = (
            contradiction_score >= threshold.decisive_strength
            and self._corroborated(contradict, threshold)
        )

        if credible_support and credible_contradiction:
            status = ClaimVerificationStatus.DISPUTED
            label = FactCheckLabel.UNVERIFIED
        elif strong_support:
            if qualifier_score >= threshold.credible_strength:
                status = ClaimVerificationStatus.PARTIALLY_SUPPORTED
                label = FactCheckLabel.PARTIALLY_TRUE
            else:
                status = ClaimVerificationStatus.SUPPORTED
                label = FactCheckLabel.TRUE
        elif strong_contradiction:
            status = ClaimVerificationStatus.REFUTED
            label = FactCheckLabel.FALSE
        elif credible_support:
            status = ClaimVerificationStatus.PARTIALLY_SUPPORTED
            label = FactCheckLabel.PARTIALLY_TRUE
        elif credible_contradiction:
            status = ClaimVerificationStatus.DISPUTED
            label = FactCheckLabel.UNVERIFIED
        else:
            status = ClaimVerificationStatus.UNVERIFIED
            label = FactCheckLabel.UNVERIFIED

        primary_count = sum(item.source_level == 1 for item in links)
        support_groups = _independence_groups(support, threshold.credible_strength)
        contradiction_groups = _independence_groups(
            contradict,
            threshold.credible_strength,
        )
        summary = _summary_for(status)
        reasoning = (
            f"support={len(support)} strongest={support_score}; "
            f"contradiction={len(contradict)} strongest={contradiction_score}; "
            f"qualifying={len(qualify)}; primary={primary_count}; "
            f"independent_support_groups={len(support_groups)}; "
            f"independent_contradiction_groups={len(contradiction_groups)}"
        )
        return ClaimVerificationResult(
            claim_id=claim.id,
            status=status,
            label=label,
            supporting_count=len(support),
            contradicting_count=len(contradict),
            qualifying_count=len(qualify),
            primary_evidence_count=primary_count,
            independent_support_groups=len(support_groups),
            independent_contradiction_groups=len(contradiction_groups),
            strongest_support=support_score,
            strongest_contradiction=contradiction_score,
            evidence_ids=tuple(item.evidence_id for item in links),
            summary=summary,
            reasoning_summary=reasoning,
        )

    def _corroborated(
        self,
        links: tuple[_EvidenceLink, ...],
        threshold: FactCheckThreshold,
    ) -> bool:
        credible = tuple(item for item in links if item.strength >= threshold.credible_strength)
        if not credible:
            return False
        if self.policy.invariants.primary_evidence_can_satisfy_corroboration and any(
            item.source_level == 1 for item in credible
        ):
            return True
        groups = _independence_groups(credible, threshold.credible_strength)
        return len(groups) >= threshold.min_independent_groups

    @staticmethod
    def _load_claims(
        session: Session,
        story_id: UUID,
        claim_ids: tuple[UUID, ...],
    ) -> list[Claim]:
        claims = list(session.scalars(select(Claim).where(Claim.id.in_(claim_ids))))
        by_id = {item.id: item for item in claims}
        if set(by_id) != set(claim_ids):
            raise ValueError("verification event references missing claims")
        ordered = [by_id[item] for item in claim_ids]
        if any(item.story_id != story_id for item in ordered):
            raise ValueError("verification event contains claim from another story")
        return ordered

    @staticmethod
    def _load_links(
        session: Session,
        claim_id: UUID,
        evidence_ids: tuple[UUID, ...],
    ) -> tuple[_EvidenceLink, ...]:
        if not evidence_ids:
            return ()
        rows = session.execute(
            select(ClaimEvidence, EvidenceItem)
            .join(EvidenceItem, ClaimEvidence.evidence_id == EvidenceItem.id)
            .where(
                ClaimEvidence.claim_id == claim_id,
                ClaimEvidence.evidence_id.in_(evidence_ids),
            )
        ).all()
        links: list[_EvidenceLink] = []
        for relation, evidence in rows:
            metadata = _assessment_metadata(evidence, claim_id)
            links.append(
                _EvidenceLink(
                    evidence_id=evidence.id,
                    relation=EvidenceRelation(relation.relation),
                    strength=relation.strength_score or Decimal("0"),
                    source_level=metadata.get("source_level"),
                    independence_group=metadata.get("independence_group"),
                )
            )
        return tuple(links)

    @staticmethod
    def _parse_collected_event(
        event: EventEnvelope,
    ) -> tuple[UUID, tuple[UUID, ...], tuple[UUID, ...], UUID]:
        if event.event_type != EventType.EVIDENCE_COLLECTED:
            raise ValueError("fact checking requires evidence.collected")
        if event.aggregate_type != "research_run":
            raise ValueError("evidence.collected must use research_run aggregate")
        story_id = UUID(str(event.payload.get("story_id")))
        claim_ids = _uuid_tuple(event.payload.get("claim_ids"), "claim_ids")
        evidence_ids = _uuid_tuple(
            event.payload.get("evidence_ids", []),
            "evidence_ids",
            allow_empty=True,
        )
        research_run_id = UUID(str(event.payload.get("research_run_id")))
        if research_run_id != event.aggregate_id:
            raise ValueError("evidence.collected research_run_id must match aggregate_id")
        return story_id, claim_ids, evidence_ids, research_run_id


def _uuid_tuple(value: Any, name: str, *, allow_empty: bool = False) -> tuple[UUID, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "possibly empty" if allow_empty else "non-empty"
        raise ValueError(f"{name} must be a {qualifier} list")
    parsed = tuple(UUID(str(item)) for item in value)
    if len(parsed) != len(set(parsed)):
        raise ValueError(f"{name} must not contain duplicates")
    return parsed


def _assessment_metadata(evidence: EvidenceItem, claim_id: UUID) -> dict[str, Any]:
    raw = evidence.evidence_metadata.get("relationship_assessments", [])
    if not isinstance(raw, list):
        return {}
    for item in raw:
        if isinstance(item, dict) and str(item.get("claim_id")) == str(claim_id):
            source_level = item.get("source_level")
            independence_group = item.get("independence_group")
            return {
                "source_level": source_level if isinstance(source_level, int) else None,
                "independence_group": (
                    independence_group if isinstance(independence_group, str) else None
                ),
            }
    return {}


def _strongest(links: tuple[_EvidenceLink, ...]) -> Decimal:
    return max((item.strength for item in links), default=Decimal("0"))


def _independence_groups(
    links: tuple[_EvidenceLink, ...],
    minimum_strength: Decimal,
) -> set[str]:
    return {
        item.independence_group
        for item in links
        if item.strength >= minimum_strength and item.independence_group is not None
    }


def _summary_for(status: ClaimVerificationStatus) -> str:
    return {
        ClaimVerificationStatus.SUPPORTED: "Assessed evidence supports the claim.",
        ClaimVerificationStatus.PARTIALLY_SUPPORTED: (
            "Assessed evidence supports part of the claim but is not decisive."
        ),
        ClaimVerificationStatus.DISPUTED: (
            "Credible assessed evidence conflicts or materially challenges the claim."
        ),
        ClaimVerificationStatus.UNVERIFIED: (
            "Available assessed evidence is insufficient for a supported or refuted conclusion."
        ),
        ClaimVerificationStatus.REFUTED: "Assessed evidence decisively contradicts the claim.",
        ClaimVerificationStatus.UNASSESSED: "The claim has not been assessed.",
    }[status]
