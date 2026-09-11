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
from news_ai_database import (
    Claim,
    ClaimEvidence,
    EventOutbox,
    EvidenceItem,
    FactCheck,
    Job,
    ResearchRunClaim,
    Story,
)
from news_ai_domain import (
    ClaimVerificationStatus,
    FactCheckLabel,
    ReviewState,
    RiskLevel,
)
from news_ai_editorial import EditorialRiskPolicy
from news_ai_events import (
    EventEnvelope,
    EventType,
    EvidenceCollectedV1,
    FactCheckCompletedV1,
    parse_event_payload,
)
from news_ai_events.outbox import build_outbox_record
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .engine import EvidenceRelation
from .semantic import semantic_key


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
    event_ids: tuple[UUID, ...]
    fact_check_ids: tuple[UUID, ...]
    claim_results: tuple[ClaimVerificationResult, ...]
    stale_claim_ids: tuple[UUID, ...]
    created_count: int

    @property
    def event_id(self) -> UUID | None:
        return self.event_ids[0] if self.event_ids else None

    def as_handler_result(self) -> dict[str, Any]:
        return {
            "story_id": str(self.story_id),
            "event_ids": [str(item) for item in self.event_ids],
            "fact_check_ids": [str(item) for item in self.fact_check_ids],
            "stale_claim_ids": [str(item) for item in self.stale_claim_ids],
            "claim_statuses": {
                str(item.claim_id): item.status.value for item in self.claim_results
            },
        }


@dataclass(frozen=True, slots=True)
class StoryVerificationResult:
    story_id: UUID
    event_id: UUID | None
    claim_ids: tuple[UUID, ...]
    fact_check_ids: tuple[UUID, ...]
    ready: bool
    created: bool
    stale: bool = False

    def as_handler_result(self) -> dict[str, Any]:
        return {
            "story_id": str(self.story_id),
            "event_id": str(self.event_id) if self.event_id is not None else None,
            "claim_ids": [str(item) for item in self.claim_ids],
            "fact_check_ids": [str(item) for item in self.fact_check_ids],
            "ready": self.ready,
            "created": self.created,
            "stale": self.stale,
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
_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


FACT_CHECK_METHODOLOGY_VERSION = "fact-check-methodology-v1"


class FactCheckEngine:
    """Evaluate assessed evidence without converting research insufficiency into falsity."""

    def __init__(
        self,
        policy: FactCheckPolicy,
        risk_policy: EditorialRiskPolicy,
        *,
        producer: str = "research-worker",
        producer_version: str = "0.1.0",
        methodology_version: str = FACT_CHECK_METHODOLOGY_VERSION,
    ) -> None:
        if not methodology_version.strip():
            raise ValueError("fact-check methodology version must not be empty")
        self.policy = policy
        self.risk_policy = risk_policy
        self.producer = producer
        self.producer_version = producer_version
        self.methodology_version = methodology_version

    def verify_evidence_collection(
        self,
        session: Session,
        event: EventEnvelope,
    ) -> FactCheckBatchResult:
        story_id, claim_ids, evidence_ids, research_run_id = self._parse_collected_event(event)
        story = session.get(Story, story_id)
        if story is None:
            raise ValueError("evidence.collected references missing story")
        research_job = session.scalar(
            select(Job).where(Job.id == research_run_id).with_for_update()
        )
        if research_job is None or research_job.job_type != "RESEARCH":
            raise ValueError("evidence.collected references missing research run")
        if research_job.status != "COMPLETED":
            raise ValueError("fact checking requires a completed research run")

        claims = self._load_claims(session, story_id, claim_ids, lock=True)
        generations = {
            row.claim_id: row.research_generation
            for row in session.scalars(
                select(ResearchRunClaim).where(
                    ResearchRunClaim.research_run_id == research_run_id,
                    ResearchRunClaim.claim_id.in_(claim_ids),
                )
            )
        }
        if set(generations) != set(claim_ids):
            raise ValueError("evidence.collected research run lacks claim generation provenance")
        current_claims = [
            claim
            for claim in claims
            if claim.current_research_run_id == research_run_id
            and claim.research_generation == generations[claim.id]
        ]
        stale_claim_ids = tuple(claim.id for claim in claims if claim not in current_claims)
        results: list[ClaimVerificationResult] = []
        fact_checks: list[FactCheck] = []
        event_ids: list[UUID] = []
        created_count = 0
        for claim in current_claims:
            links = self._load_links(session, claim.id, evidence_ids)
            result = self._evaluate_claim(claim, links)
            claim.status = result.status
            operation_key = self._fact_check_semantic_key(
                story_id=story_id,
                research_run_id=research_run_id,
                claim=claim,
                links=links,
            )
            existing = session.scalar(
                select(FactCheck).where(FactCheck.semantic_key == operation_key)
            )
            if existing is not None:
                claim.status = result.status
                claim.current_fact_check_id = existing.id
                fact_checks.append(existing)
                results.append(result)
                event_ids.append(self._fact_check_event_id(session, existing))
                continue
            fact_check = FactCheck(
                story_id=story_id,
                claim_id=claim.id,
                label=result.label,
                confidence_score=None,
                summary=result.summary,
                reasoning_summary=result.reasoning_summary,
                primary_evidence_count=result.primary_evidence_count,
                supporting_count=result.supporting_count,
                contradicting_count=result.contradicting_count,
                review_required=True,
                review_state=ReviewState.NOT_READY,
                ai_run_id=None,
                research_run_id=research_run_id,
                research_generation=generations[claim.id],
                methodology_version=self.methodology_version,
                semantic_key=operation_key,
            )
            session.add(fact_check)
            session.flush()
            claim.current_fact_check_id = fact_check.id
            fact_checks.append(fact_check)
            results.append(result)
            completed = EventEnvelope(
                event_type=EventType.FACT_CHECK_COMPLETED,
                producer=self.producer,
                producer_version=self.producer_version,
                aggregate_type="story",
                aggregate_id=story_id,
                correlation_id=event.correlation_id,
                causation_id=event.event_id,
                idempotency_key=f"fact_check.completed:{fact_check.id}",
                payload={
                    "story_id": str(story_id),
                    "fact_check_id": str(fact_check.id),
                    "label": fact_check.label.value,
                    "confidence_score": (
                        float(fact_check.confidence_score)
                        if fact_check.confidence_score is not None
                        else None
                    ),
                    "review_required": fact_check.review_required,
                },
            )
            session.add(build_outbox_record(completed))
            event_ids.append(completed.event_id)
            created_count += 1
        session.flush()
        return FactCheckBatchResult(
            story_id=story_id,
            event_ids=tuple(event_ids),
            fact_check_ids=tuple(item.id for item in fact_checks),
            claim_results=tuple(results),
            stale_claim_ids=stale_claim_ids,
            created_count=created_count,
        )

    def mark_story_verified(
        self,
        session: Session,
        event: EventEnvelope,
    ) -> StoryVerificationResult:
        payload = parse_event_payload(
            event.event_type,
            event.schema_version,
            event.payload,
            FactCheckCompletedV1,
        )
        if event.aggregate_type != "story":
            raise ValueError("fact_check.completed must use story aggregate")
        story_id = payload.story_id
        if story_id != event.aggregate_id:
            raise ValueError("fact_check.completed story_id must match aggregate_id")

        story = session.scalar(select(Story).where(Story.id == story_id).with_for_update())
        if story is None:
            raise ValueError("fact_check.completed references missing story")
        trigger = session.get(FactCheck, payload.fact_check_id)
        if trigger is None or trigger.story_id != story_id or trigger.claim_id is None:
            raise ValueError("fact_check.completed references missing or mismatched FactCheck")
        if (
            trigger.label is not payload.label
            or _decimal_float(trigger.confidence_score) != payload.confidence_score
            or trigger.review_required is not payload.review_required
        ):
            raise ValueError("fact_check.completed payload does not match durable FactCheck state")
        trigger_claim = session.get(Claim, trigger.claim_id, with_for_update=True)
        if trigger_claim is None:
            raise ValueError("fact_check.completed references missing durable Claim")
        if trigger_claim.current_fact_check_id != trigger.id:
            return StoryVerificationResult(
                story_id,
                None,
                (trigger_claim.id,),
                (),
                False,
                False,
                True,
            )

        claims = list(
            session.scalars(select(Claim).where(Claim.story_id == story_id).order_by(Claim.id))
        )
        claim_ids = tuple(claim.id for claim in claims)
        if not claims:
            raise ValueError("story verification requires at least one durable claim")
        if any(claim.status is ClaimVerificationStatus.UNASSESSED for claim in claims):
            return StoryVerificationResult(story_id, None, claim_ids, (), False, False)

        checks = self._current_fact_checks(session, claims)
        if set(checks) != set(claim_ids):
            return StoryVerificationResult(story_id, None, claim_ids, (), False, False)
        if checks[trigger.claim_id].id != trigger.id:
            return StoryVerificationResult(
                story_id,
                None,
                claim_ids,
                tuple(checks[claim_id].id for claim_id in claim_ids),
                True,
                False,
            )

        ordered_checks = tuple(checks[claim_id] for claim_id in claim_ids)
        fact_check_ids = tuple(check.id for check in ordered_checks)
        confidence = _aggregate_confidence(ordered_checks, story.confidence_score)
        risk_level = max(
            (story.risk_level, *(claim.risk_level for claim in claims)),
            key=_RISK_ORDER.__getitem__,
        )
        sensitive_topics = _sensitive_topics(story, claims)
        review_required = any(check.review_required for check in ordered_checks) or (
            self.risk_policy.requires_review(sensitive_topics)
        )
        operation_key = semantic_key(
            "story-verification",
            {
                "story_id": story_id,
                "fact_check_ids": fact_check_ids,
                "confidence_score": confidence,
                "risk_level": risk_level,
                "review_required": review_required,
            },
        )
        existing = session.scalar(
            select(EventOutbox).where(
                EventOutbox.event_type == EventType.STORY_VERIFIED.value,
                EventOutbox.idempotency_key == f"story.verified:{operation_key}",
            )
        )
        if story.verification_semantic_key == operation_key:
            if existing is None:
                raise ValueError("verified story is missing its canonical outbox event")
            return StoryVerificationResult(
                story_id, existing.event_id, claim_ids, fact_check_ids, True, False
            )
        if existing is not None:
            story.verification_semantic_key = operation_key
            return StoryVerificationResult(
                story_id, existing.event_id, claim_ids, fact_check_ids, True, False
            )

        story.status = "VERIFIED"
        story.confidence_score = Decimal(str(confidence)) if confidence is not None else None
        story.verification_semantic_key = operation_key
        verified = EventEnvelope(
            event_type=EventType.STORY_VERIFIED,
            producer=self.producer,
            producer_version=self.producer_version,
            aggregate_type="story",
            aggregate_id=story_id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            idempotency_key=f"story.verified:{operation_key}",
            payload={
                "story_id": str(story_id),
                "fact_check_ids": [str(item) for item in fact_check_ids],
                "confidence_score": confidence,
                "risk_level": risk_level.value,
                "review_required": review_required,
            },
        )
        session.add(build_outbox_record(verified))
        session.flush()
        return StoryVerificationResult(
            story_id=story_id,
            event_id=verified.event_id,
            claim_ids=claim_ids,
            fact_check_ids=fact_check_ids,
            ready=True,
            created=True,
        )

    def _fact_check_semantic_key(
        self,
        *,
        story_id: UUID,
        research_run_id: UUID,
        claim: Claim,
        links: tuple[_EvidenceLink, ...],
    ) -> str:
        return semantic_key(
            "fact-check",
            {
                "methodology_version": self.methodology_version,
                "story_id": story_id,
                "research_run_id": research_run_id,
                "claim_id": claim.id,
                "claim_text": claim.claim_text,
                "claim_risk": claim.risk_level,
                "evidence": [
                    {
                        "id": link.evidence_id,
                        "relation": link.relation,
                        "strength": link.strength,
                        "source_level": link.source_level,
                        "independence_group": link.independence_group,
                    }
                    for link in sorted(links, key=lambda item: str(item.evidence_id))
                ],
                "policy": self.policy.model_dump(mode="json"),
            },
        )

    @staticmethod
    def _fact_check_event_id(session: Session, fact_check: FactCheck) -> UUID:
        outbox = session.scalar(
            select(EventOutbox).where(
                EventOutbox.event_type == EventType.FACT_CHECK_COMPLETED.value,
                EventOutbox.idempotency_key == f"fact_check.completed:{fact_check.id}",
            )
        )
        if outbox is None:
            raise ValueError("semantic FactCheck is missing its canonical outbox event")
        return outbox.event_id

    @staticmethod
    def _current_fact_checks(session: Session, claims: list[Claim]) -> dict[UUID, FactCheck]:
        check_ids = {
            claim.current_fact_check_id
            for claim in claims
            if claim.current_fact_check_id is not None
        }
        checks = {
            check.id: check
            for check in session.scalars(select(FactCheck).where(FactCheck.id.in_(check_ids)))
        }
        current: dict[UUID, FactCheck] = {}
        for claim in claims:
            if claim.current_fact_check_id is None:
                continue
            check = checks.get(claim.current_fact_check_id)
            if (
                check is None
                or check.claim_id != claim.id
                or check.research_run_id != claim.current_research_run_id
                or check.research_generation != claim.research_generation
            ):
                raise ValueError("Claim current FactCheck provenance is inconsistent")
            current[claim.id] = check
        return current

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
        *,
        lock: bool = False,
    ) -> list[Claim]:
        statement = select(Claim).where(Claim.id.in_(claim_ids)).order_by(Claim.id)
        if lock:
            statement = statement.with_for_update()
        claims = list(session.scalars(statement))
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
            metadata = _resolved_source_metadata(evidence)
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
        payload = parse_event_payload(
            event.event_type,
            event.schema_version,
            event.payload,
            EvidenceCollectedV1,
        )
        if event.aggregate_type != "research_run":
            raise ValueError("evidence.collected must use research_run aggregate")
        story_id = payload.story_id
        claim_ids = payload.claim_ids
        evidence_ids = payload.evidence_ids
        research_run_id = payload.research_run_id
        if research_run_id != event.aggregate_id:
            raise ValueError("evidence.collected research_run_id must match aggregate_id")
        return story_id, claim_ids, evidence_ids, research_run_id


def _resolved_source_metadata(evidence: EvidenceItem) -> dict[str, Any]:
    metadata = evidence.evidence_metadata or {}
    source_level = metadata.get("source_level")
    independence_group = metadata.get("independence_group")
    return {
        "source_level": (
            source_level
            if isinstance(source_level, int) and not isinstance(source_level, bool)
            else None
        ),
        "independence_group": (independence_group if isinstance(independence_group, str) else None),
    }


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


def _decimal_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _aggregate_confidence(
    checks: tuple[FactCheck, ...],
    existing: Decimal | None,
) -> float | None:
    values = [
        float(check.confidence_score) for check in checks if check.confidence_score is not None
    ]
    if values:
        return sum(values) / len(values)
    return _decimal_float(existing)


def _sensitive_topics(story: Story, claims: list[Claim]) -> frozenset[str]:
    topics: set[str] = set()
    for metadata in (story.story_metadata, *(claim.claim_metadata for claim in claims)):
        raw = (metadata or {}).get("sensitive_topics", [])
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise ValueError("sensitive_topics metadata must be a list of strings")
        topics.update(item.strip() for item in raw if item.strip())
    return frozenset(topics)
