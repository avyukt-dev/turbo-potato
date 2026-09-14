"""Build immutable Fact Sheet snapshots from verified PostgreSQL state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from news_ai_database import (
    Article,
    Claim,
    ClaimEvidence,
    EventOutbox,
    EvidenceItem,
    FactCheck,
    FactSheet,
    Source,
    Story,
    StorySource,
)
from news_ai_domain import (
    CLAIM_SEMANTICS_POLICY_VERSION,
    ClaimSemantics,
    ClaimVerificationStatus,
    FactCheckLabel,
    ReviewState,
    RiskLevel,
)
from news_ai_domain.values import VALUE_INTEGRITY_POLICY_VERSION, ClaimValues
from news_ai_events import (
    EventEnvelope,
    EventType,
    PermanentEventError,
    StaleWorkError,
    StoryVerifiedV1,
    parse_event_payload,
)
from news_ai_events.outbox import build_outbox_record
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .engine import EvidenceRelation
from .semantic import semantic_key

_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


class FactSheetClaimSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: UUID
    story_id: UUID
    claim_text: str
    claim_type: str
    semantics: ClaimSemantics | None = None
    values: ClaimValues | None = None
    status: ClaimVerificationStatus
    confidence_score: float | None = None
    importance_score: float | None = None
    risk_level: RiskLevel
    sensitive_topics: tuple[str, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    contradictory_evidence_ids: tuple[UUID, ...] = ()
    temporal_start: datetime | None = None
    temporal_end: datetime | None = None
    location_ids: tuple[UUID, ...] = ()


class FactSheetFactCheckSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_check_id: UUID
    story_id: UUID
    claim_id: UUID | None = None
    label: FactCheckLabel
    confidence_score: float | None = None
    summary: str
    supporting_evidence_ids: tuple[UUID, ...] = ()
    contradicting_evidence_ids: tuple[UUID, ...] = ()
    review_required: bool
    review_state: ReviewState


class FactSheetEvidenceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: UUID
    claim_id: UUID
    source_id: UUID | None = None
    article_id: UUID | None = None
    article_version_id: UUID | None = None
    title: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    relation: str
    strength_score: float | None = None
    excerpt: str | None = None
    provenance_note: str | None = None
    content_hash: str | None = None
    source_level: int | None = None
    source_policy_basis: str | None = None
    lineage_status: str | None = None
    lineage_basis: str | None = None
    independence_group: str | None = None
    originating_reference: str | None = None
    assessment_ai_run_ids: tuple[UUID, ...] = ()


class FactSheetSourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: UUID
    name: str
    source_level: int
    url: str | None = None
    publisher: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    language: str | None = None


class FactSheetArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_sheet_id: UUID
    story_id: UUID
    version: int
    headline: str
    summary: str
    claims: tuple[FactSheetClaimSnapshot, ...]
    fact_checks: tuple[FactSheetFactCheckSnapshot, ...] = ()
    evidence: tuple[FactSheetEvidenceSnapshot, ...]
    sources: tuple[FactSheetSourceSnapshot, ...]
    timeline: tuple[dict[str, Any], ...] = ()
    entities: tuple[dict[str, Any], ...] = ()
    locations: tuple[str, ...] = ()
    context: tuple[str, ...] = ()
    counterclaims: tuple[FactSheetClaimSnapshot, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    confidence_score: float | None = None
    risk_level: RiskLevel
    sensitive_topics: tuple[str, ...] = ()
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FactSheetGenerationResult:
    story_id: UUID
    fact_sheet_id: UUID
    version: int
    event_id: UUID
    created: bool

    def as_handler_result(self) -> dict[str, Any]:
        return {
            "story_id": str(self.story_id),
            "fact_sheet_id": str(self.fact_sheet_id),
            "fact_sheet_version": self.version,
            "event_id": str(self.event_id),
            "created": self.created,
        }


class FactSheetGenerator:
    """Snapshot verified state without adding interpretation or new factual assertions."""

    def __init__(
        self,
        *,
        producer: str = "research-worker",
        producer_version: str = "0.1.0",
        requested_platforms: tuple[str, ...] = (),
        requested_formats: tuple[str, ...] = (),
    ) -> None:
        self.producer = producer
        self.producer_version = producer_version
        self.requested_platforms = requested_platforms
        self.requested_formats = requested_formats

    def generate(
        self,
        session: Session,
        event: EventEnvelope,
    ) -> FactSheetGenerationResult:
        story_id, fact_check_ids = self._validate_verified_event(event)
        story = session.scalar(select(Story).where(Story.id == story_id).with_for_update())
        if story is None:
            raise ValueError("story.verified references missing story")
        if story.status != "VERIFIED":
            raise ValueError("Fact Sheet generation requires VERIFIED story state")

        claims = self._load_claims(session, story_id)
        checks = self._load_fact_checks(session, story_id, claims, fact_check_ids)
        if any(claim.status is ClaimVerificationStatus.UNASSESSED for claim in claims):
            raise ValueError("Fact Sheet cannot include UNASSESSED claim state")
        links, evidence = self._load_evidence(session, checks)
        claim_snapshots = self._claim_snapshots(claims, links)
        evidence_snapshots = self._evidence_snapshots(links, evidence)
        fact_check_snapshots = self._fact_check_snapshots(checks, links)
        source_snapshots = self._source_snapshots(session, story_id, evidence)
        metadata = dict(story.story_metadata or {})
        sensitive_topics = self._sensitive_topics(metadata, claims)
        risk_level = max(
            (story.risk_level, *(claim.risk_level for claim in claims)),
            key=_RISK_ORDER.__getitem__,
        )
        counterclaim_ids = set(_metadata_uuid_list(metadata, "counterclaim_ids"))
        counterclaims = tuple(
            snapshot for snapshot in claim_snapshots if snapshot.claim_id in counterclaim_ids
        )
        version = self._next_version(session, story_id)
        headline = (story.canonical_headline or "").strip()
        if not headline:
            raise ValueError("Fact Sheet requires a non-empty story headline")
        summary = (story.summary or "").strip() or headline
        operation_key = semantic_key(
            "fact-sheet",
            {
                "story_id": story_id,
                "headline": headline,
                "summary": summary,
                "story_metadata": metadata,
                "claims": [item.model_dump(mode="json") for item in claim_snapshots],
                "fact_checks": [item.model_dump(mode="json") for item in fact_check_snapshots],
                "evidence": [item.model_dump(mode="json") for item in evidence_snapshots],
                "sources": [item.model_dump(mode="json") for item in source_snapshots],
                "risk_level": risk_level,
                "sensitive_topics": sensitive_topics,
                "requested_platforms": self.requested_platforms,
                "requested_formats": self.requested_formats,
            },
        )
        existing = session.scalar(select(FactSheet).where(FactSheet.semantic_key == operation_key))
        if existing is not None:
            return self._existing_result(session, existing)

        fact_sheet = FactSheet(
            story_id=story_id,
            version=version,
            headline=headline,
            summary=summary,
            claims_snapshot=[item.model_dump(mode="json") for item in claim_snapshots],
            fact_checks_snapshot=[item.model_dump(mode="json") for item in fact_check_snapshots],
            evidence_snapshot=[item.model_dump(mode="json") for item in evidence_snapshots],
            sources_snapshot=[item.model_dump(mode="json") for item in source_snapshots],
            timeline=list(_metadata_dict_list(metadata, "timeline")),
            entities=list(_metadata_dict_list(metadata, "entities")),
            locations=list(_metadata_str_list(metadata, "locations")),
            context=list(_metadata_str_list(metadata, "context")),
            counterclaims=[item.model_dump(mode="json") for item in counterclaims],
            unresolved_questions=list(_metadata_str_list(metadata, "unresolved_questions")),
            confidence_score=story.confidence_score,
            risk_level=risk_level,
            sensitive_topics=list(sensitive_topics),
            ai_run_id=None,
            semantic_key=operation_key,
        )
        session.add(fact_sheet)
        session.flush()

        content_requested = EventEnvelope(
            event_type=EventType.CONTENT_REQUESTED,
            producer=self.producer,
            producer_version=self.producer_version,
            aggregate_type="fact_sheet",
            aggregate_id=fact_sheet.id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            idempotency_key=f"content.requested:{fact_sheet.id}:{version}",
            payload={
                "story_id": str(story_id),
                "fact_sheet_id": str(fact_sheet.id),
                "requested_platforms": list(self.requested_platforms),
                "requested_formats": list(self.requested_formats),
            },
        )
        session.add(build_outbox_record(content_requested))
        session.flush()
        return FactSheetGenerationResult(
            story_id=story_id,
            fact_sheet_id=fact_sheet.id,
            version=version,
            event_id=content_requested.event_id,
            created=True,
        )

    @staticmethod
    def _existing_result(session: Session, fact_sheet: FactSheet) -> FactSheetGenerationResult:
        outbox = session.scalar(
            select(EventOutbox).where(
                EventOutbox.event_type == EventType.CONTENT_REQUESTED.value,
                EventOutbox.aggregate_id == fact_sheet.id,
                EventOutbox.idempotency_key
                == f"content.requested:{fact_sheet.id}:{fact_sheet.version}",
            )
        )
        if outbox is None:
            raise ValueError("semantic Fact Sheet is missing its content.requested outbox event")
        return FactSheetGenerationResult(
            story_id=fact_sheet.story_id,
            fact_sheet_id=fact_sheet.id,
            version=fact_sheet.version,
            event_id=outbox.event_id,
            created=False,
        )

    @staticmethod
    def artifact_from_row(row: FactSheet) -> FactSheetArtifact:
        return FactSheetArtifact(
            fact_sheet_id=row.id,
            story_id=row.story_id,
            version=row.version,
            headline=row.headline,
            summary=row.summary,
            claims=tuple(
                FactSheetClaimSnapshot.model_validate(item) for item in row.claims_snapshot
            ),
            fact_checks=tuple(
                FactSheetFactCheckSnapshot.model_validate(item) for item in row.fact_checks_snapshot
            ),
            evidence=tuple(
                FactSheetEvidenceSnapshot.model_validate(item) for item in row.evidence_snapshot
            ),
            sources=tuple(
                FactSheetSourceSnapshot.model_validate(item) for item in row.sources_snapshot
            ),
            timeline=tuple(row.timeline or []),
            entities=tuple(row.entities or []),
            locations=tuple(row.locations or []),
            context=tuple(row.context or []),
            counterclaims=tuple(
                FactSheetClaimSnapshot.model_validate(item) for item in row.counterclaims
            ),
            unresolved_questions=tuple(row.unresolved_questions or []),
            confidence_score=(
                float(row.confidence_score) if row.confidence_score is not None else None
            ),
            risk_level=row.risk_level,
            sensitive_topics=tuple(row.sensitive_topics or []),
            created_at=row.created_at,
        )

    @staticmethod
    def _validate_verified_event(
        event: EventEnvelope,
    ) -> tuple[UUID, tuple[UUID, ...]]:
        payload = parse_event_payload(
            event.event_type,
            event.schema_version,
            event.payload,
            StoryVerifiedV1,
        )
        if event.aggregate_type != "story":
            raise ValueError("story.verified must use story aggregate")
        story_id = payload.story_id
        if story_id != event.aggregate_id:
            raise ValueError("story.verified story_id must match aggregate_id")
        return story_id, payload.fact_check_ids

    @staticmethod
    def _load_claims(
        session: Session,
        story_id: UUID,
    ) -> list[Claim]:
        claims = list(
            session.scalars(select(Claim).where(Claim.story_id == story_id).order_by(Claim.id))
        )
        if not claims:
            raise ValueError("Fact Sheet requires at least one durable story claim")
        return claims

    @staticmethod
    def _load_fact_checks(
        session: Session,
        story_id: UUID,
        claims: list[Claim],
        fact_check_ids: tuple[UUID, ...],
    ) -> list[FactCheck]:
        checks = list(session.scalars(select(FactCheck).where(FactCheck.id.in_(fact_check_ids))))
        by_id = {check.id: check for check in checks}
        if set(by_id) != set(fact_check_ids):
            raise ValueError("story.verified references missing fact checks")
        selected = [by_id[check_id] for check_id in fact_check_ids]
        selected_by_claim: dict[UUID, FactCheck] = {}
        for check in selected:
            if check.story_id != story_id or check.claim_id is None:
                raise ValueError("story.verified references mismatched fact check")
            if check.claim_id in selected_by_claim:
                raise ValueError("story.verified references multiple FactChecks for one claim")
            selected_by_claim[check.claim_id] = check
        claim_ids = tuple(claim.id for claim in claims)
        if set(selected_by_claim) != set(claim_ids):
            raise ValueError("story.verified FactChecks do not cover current durable claims")
        current_ids = {claim.current_fact_check_id for claim in claims}
        if None in current_ids or current_ids != set(fact_check_ids):
            raise StaleWorkError("story.verified references stale FactChecks")
        for claim in claims:
            check = selected_by_claim[claim.id]
            if (
                check.id != claim.current_fact_check_id
                or check.research_run_id != claim.current_research_run_id
                or check.research_generation != claim.research_generation
            ):
                raise StaleWorkError("story.verified FactCheck provenance is not current")
        return [selected_by_claim[claim_id] for claim_id in claim_ids]

    @staticmethod
    def _load_evidence(
        session: Session,
        checks: list[FactCheck],
    ) -> tuple[list[ClaimEvidence], dict[UUID, EvidenceItem]]:
        claim_ids = tuple(check.claim_id for check in checks if check.claim_id is not None)
        candidates = list(
            session.scalars(
                select(ClaimEvidence)
                .where(ClaimEvidence.claim_id.in_(claim_ids))
                .order_by(ClaimEvidence.claim_id, ClaimEvidence.evidence_id)
            )
        )
        candidate_ids = {link.evidence_id for link in candidates}
        candidate_evidence = {
            item.id: item
            for item in session.scalars(
                select(EvidenceItem).where(EvidenceItem.id.in_(candidate_ids))
            )
        }
        checks_by_claim = {check.claim_id: check for check in checks}
        links = []
        for link in candidates:
            item = candidate_evidence.get(link.evidence_id)
            if item is None:
                raise ValueError("claim evidence references missing evidence item")
            check = checks_by_claim[link.claim_id]
            research_run_id = (item.evidence_metadata or {}).get("research_run_id")
            if check.research_run_id is not None and research_run_id != str(check.research_run_id):
                continue
            links.append(link)
        evidence_ids = {link.evidence_id for link in links}
        if not evidence_ids:
            return links, {}
        evidence_items = list(
            session.scalars(select(EvidenceItem).where(EvidenceItem.id.in_(evidence_ids)))
        )
        by_id = {item.id: item for item in evidence_items}
        if set(by_id) != evidence_ids:
            raise ValueError("claim evidence references missing evidence item")
        return links, by_id

    @staticmethod
    def _claim_snapshots(
        claims: list[Claim],
        links: list[ClaimEvidence],
    ) -> tuple[FactSheetClaimSnapshot, ...]:
        links_by_claim: dict[UUID, list[ClaimEvidence]] = {}
        for link in links:
            links_by_claim.setdefault(link.claim_id, []).append(link)
        snapshots: list[FactSheetClaimSnapshot] = []
        for claim in claims:
            if (
                claim.semantic_type is None
                or claim.semantic_state is None
                or claim.semantic_policy_version != CLAIM_SEMANTICS_POLICY_VERSION
                or claim.semantic_ai_run_id is None
            ):
                raise PermanentEventError("CLAIM_SEMANTICS_MISSING")
            if (
                claim.value_anchors is None
                or claim.value_policy_version != VALUE_INTEGRITY_POLICY_VERSION
                or claim.value_ai_run_id is None
            ):
                raise PermanentEventError("CLAIM_VALUES_MISSING")
            claim_links = links_by_claim.get(claim.id, [])
            all_evidence = tuple(link.evidence_id for link in claim_links)
            contradictory = tuple(
                link.evidence_id
                for link in claim_links
                if link.relation == EvidenceRelation.CONTRADICTS.value
            )
            snapshots.append(
                FactSheetClaimSnapshot(
                    claim_id=claim.id,
                    story_id=claim.story_id,
                    claim_text=claim.claim_text,
                    claim_type=claim.claim_type or "UNKNOWN",
                    semantics=ClaimSemantics(
                        policy_version=claim.semantic_policy_version,
                        semantic_type=claim.semantic_type,
                        semantic_state=claim.semantic_state,
                        ai_run_id=claim.semantic_ai_run_id,
                    ),
                    status=claim.status,
                    values=ClaimValues(
                        policy_version=claim.value_policy_version,
                        anchors=claim.value_anchors,
                        ai_run_id=claim.value_ai_run_id,
                    ),
                    confidence_score=_decimal_float(claim.confidence_score),
                    importance_score=_decimal_float(claim.importance_score),
                    risk_level=claim.risk_level,
                    sensitive_topics=_metadata_str_list(
                        dict(claim.claim_metadata or {}), "sensitive_topics"
                    ),
                    evidence_ids=all_evidence,
                    contradictory_evidence_ids=contradictory,
                    temporal_start=claim.temporal_start,
                    temporal_end=claim.temporal_end,
                    location_ids=_metadata_uuid_list(
                        dict(claim.claim_metadata or {}), "location_ids"
                    ),
                )
            )
        return tuple(snapshots)

    @staticmethod
    def _evidence_snapshots(
        links: list[ClaimEvidence],
        evidence: dict[UUID, EvidenceItem],
    ) -> tuple[FactSheetEvidenceSnapshot, ...]:
        snapshots: list[FactSheetEvidenceSnapshot] = []
        for link in links:
            item = evidence[link.evidence_id]
            metadata = dict(item.evidence_metadata or {})
            snapshots.append(
                FactSheetEvidenceSnapshot(
                    evidence_id=item.id,
                    claim_id=link.claim_id,
                    source_id=item.source_id,
                    article_id=_metadata_optional_uuid(metadata, "article_id"),
                    article_version_id=_metadata_optional_uuid(metadata, "article_version_id"),
                    title=item.title,
                    url=item.url,
                    published_at=item.published_at,
                    retrieved_at=item.retrieved_at,
                    relation=link.relation,
                    strength_score=_decimal_float(link.strength_score),
                    excerpt=item.excerpt,
                    provenance_note=_assessment_note(item, link.claim_id, link.relation),
                    content_hash=item.content_hash,
                    source_level=_metadata_optional_int(metadata, "source_level"),
                    source_policy_basis=_metadata_optional_str(metadata, "source_policy_basis"),
                    lineage_status=_metadata_optional_str(metadata, "lineage_status"),
                    lineage_basis=_metadata_optional_str(metadata, "lineage_basis"),
                    independence_group=_metadata_optional_str(metadata, "independence_group"),
                    originating_reference=_metadata_optional_str(metadata, "originating_reference"),
                    assessment_ai_run_ids=_metadata_uuid_list(metadata, "assessment_ai_run_ids"),
                )
            )
        return tuple(snapshots)

    @staticmethod
    def _fact_check_snapshots(
        checks: list[FactCheck],
        links: list[ClaimEvidence],
    ) -> tuple[FactSheetFactCheckSnapshot, ...]:
        links_by_claim: dict[UUID, list[ClaimEvidence]] = {}
        for link in links:
            links_by_claim.setdefault(link.claim_id, []).append(link)
        snapshots: list[FactSheetFactCheckSnapshot] = []
        for check in checks:
            claim_links = links_by_claim.get(check.claim_id, []) if check.claim_id else []
            supporting = tuple(
                link.evidence_id
                for link in claim_links
                if link.relation
                in {
                    EvidenceRelation.DIRECT_SUPPORT.value,
                    EvidenceRelation.INDIRECT_SUPPORT.value,
                }
            )
            contradicting = tuple(
                link.evidence_id
                for link in claim_links
                if link.relation == EvidenceRelation.CONTRADICTS.value
            )
            snapshots.append(
                FactSheetFactCheckSnapshot(
                    fact_check_id=check.id,
                    story_id=check.story_id,
                    claim_id=check.claim_id,
                    label=check.label,
                    confidence_score=_decimal_float(check.confidence_score),
                    summary=check.summary or "",
                    supporting_evidence_ids=supporting,
                    contradicting_evidence_ids=contradicting,
                    review_required=check.review_required,
                    review_state=check.review_state,
                )
            )
        return tuple(snapshots)

    @staticmethod
    def _source_snapshots(
        session: Session,
        story_id: UUID,
        evidence: dict[UUID, EvidenceItem],
    ) -> tuple[FactSheetSourceSnapshot, ...]:
        articles = list(
            session.scalars(
                select(Article)
                .join(StorySource, StorySource.article_id == Article.id)
                .where(StorySource.story_id == story_id)
                .order_by(Article.id)
            )
        )
        source_ids = {article.source_id for article in articles}
        source_ids.update(
            item.source_id for item in evidence.values() if item.source_id is not None
        )
        if not source_ids:
            return ()
        sources = list(session.scalars(select(Source).where(Source.id.in_(source_ids))))
        by_id = {source.id: source for source in sources}
        if set(by_id) != source_ids:
            raise ValueError("Fact Sheet source references missing source rows")

        snapshots: list[FactSheetSourceSnapshot] = []
        seen: set[tuple[UUID, str | None]] = set()
        for article in articles:
            source = by_id[article.source_id]
            key = (source.id, article.canonical_url)
            if key in seen:
                continue
            seen.add(key)
            snapshots.append(
                FactSheetSourceSnapshot(
                    source_id=source.id,
                    name=source.name,
                    source_level=_source_level(source.authority_level),
                    url=article.canonical_url,
                    publisher=source.name,
                    published_at=article.published_at,
                    language=article.language or source.language,
                )
            )
        for item in sorted(evidence.values(), key=lambda value: str(value.id)):
            if item.source_id is None:
                continue
            source = by_id[item.source_id]
            key = (source.id, item.url)
            if key in seen:
                continue
            seen.add(key)
            snapshots.append(
                FactSheetSourceSnapshot(
                    source_id=source.id,
                    name=source.name,
                    source_level=_source_level(source.authority_level),
                    url=item.url or source.base_url,
                    publisher=source.name,
                    published_at=item.published_at,
                    retrieved_at=item.retrieved_at,
                    language=_metadata_optional_str(item.evidence_metadata, "language")
                    or source.language,
                )
            )
        return tuple(snapshots)

    @staticmethod
    def _sensitive_topics(metadata: dict[str, Any], claims: list[Claim]) -> tuple[str, ...]:
        topics = set(_metadata_str_list(metadata, "sensitive_topics"))
        for claim in claims:
            topics.update(_metadata_str_list(dict(claim.claim_metadata or {}), "sensitive_topics"))
        return tuple(sorted(topics))

    @staticmethod
    def _next_version(session: Session, story_id: UUID) -> int:
        current = session.scalar(
            select(func.max(FactSheet.version)).where(FactSheet.story_id == story_id)
        )
        return int(current or 0) + 1


def _metadata_str_list(metadata: dict[str, Any], key: str) -> tuple[str, ...]:
    value = metadata.get(key, [])
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} metadata must be a list of strings")
    normalized = tuple(item.strip() for item in value if item.strip())
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{key} metadata must not contain duplicates")
    return normalized


def _metadata_dict_list(metadata: dict[str, Any], key: str) -> tuple[dict[str, Any], ...]:
    value = metadata.get(key, [])
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{key} metadata must be a list of objects")
    return tuple(dict(item) for item in value)


def _metadata_uuid_list(metadata: dict[str, Any], key: str) -> tuple[UUID, ...]:
    value = metadata.get(key, [])
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{key} metadata must be a list")
    result = tuple(UUID(str(item)) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{key} metadata must not contain duplicates")
    return result


def _metadata_optional_str(metadata: dict[str, Any] | None, key: str) -> str | None:
    if not metadata:
        return None
    value = metadata.get(key)
    if value is None:
        return None
    return str(value).strip() or None


def _metadata_optional_uuid(metadata: dict[str, Any], key: str) -> UUID | None:
    value = metadata.get(key)
    return UUID(str(value)) if value is not None else None


def _metadata_optional_int(metadata: dict[str, Any], key: str) -> int | None:
    value = metadata.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{key} metadata must be an integer")
    return int(value)


def _assessment_note(item: EvidenceItem, claim_id: UUID, relation: str) -> str | None:
    assessments = (item.evidence_metadata or {}).get("relationship_assessments", [])
    if not isinstance(assessments, list):
        return None
    for assessment in assessments:
        if not isinstance(assessment, dict):
            continue
        if str(assessment.get("claim_id")) != str(claim_id):
            continue
        if str(assessment.get("relation")) != relation:
            continue
        notes = assessment.get("notes")
        if isinstance(notes, str) and notes.strip():
            return notes.strip()
    return None


def _source_level(value: int | None) -> int:
    if value is None:
        return 4
    return min(4, max(1, int(value)))


def _decimal_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None
