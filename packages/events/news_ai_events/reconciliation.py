"""Explicit, bounded restoration of lost Redis transport from durable outbox truth."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from news_ai_database import (
    Article,
    ArticleDiscovery,
    ArticleVersion,
    Claim,
    ContentDraft,
    ContentQualityCheck,
    ContentVariant,
    EventOutbox,
    FactCheck,
    FactSheet,
    Job,
    ProcessedEvent,
    Publication,
    PublicationAttempt,
    PublicationAttemptPhase,
    PublicationAttemptStatus,
    Story,
    StorySource,
)
from news_ai_database.models import OutboxStatus
from news_ai_domain import PublicationStatus
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .consumer_contracts import CONSUMER_CONTRACT_BY_EVENT
from .envelope import EventEnvelope
from .outbox import envelope_from_outbox
from .types import EventType

DEFAULT_RECONCILIATION_LIMIT = 100
MAX_RECONCILIATION_LIMIT = 500
CURRENT_QUALITY_METHODOLOGY = "quality-gate-methodology-v3"


class ReconciliationMode(StrEnum):
    DRY_RUN = "DRY_RUN"
    APPLY = "APPLY"


class ReconciliationDisposition(StrEnum):
    REPLAY_REQUIRED = "REPLAY_REQUIRED"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"
    DOMAIN_COMPLETE = "DOMAIN_COMPLETE"
    UNSUPPORTED = "UNSUPPORTED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    INVALID_DURABLE_EVENT = "INVALID_DURABLE_EVENT"


class ReconciliationItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outbox_id: UUID
    event_id: UUID
    event_type: str = Field(max_length=128)
    disposition: ReconciliationDisposition
    stream: str | None = Field(default=None, max_length=128)
    consumer_group: str | None = Field(default=None, max_length=128)
    replayed: bool = False


class ReconciliationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stream: str = Field(max_length=128)
    consumer_group: str = Field(max_length=128)


class ReconciliationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: ReconciliationMode
    publishing_paused: bool | None
    limit: int
    after_outbox_id: UUID | None = None
    next_after_outbox_id: UUID | None = None
    scanned: int
    replay_required: int
    replayed: int
    already_processed: int
    domain_complete: int
    manual_review_required: int
    unsupported: int
    invalid: int
    targets: tuple[ReconciliationTarget, ...] = ()
    groups_created: tuple[ReconciliationTarget, ...] = ()
    error_code: str | None = Field(default=None, max_length=64)
    items: tuple[ReconciliationItem, ...] = ()


class EventPublisher(Protocol):
    async def publish(self, event: EventEnvelope) -> str: ...


class ConsumerGroupClient(Protocol):
    async def xgroup_create(self, name: str, groupname: str, **kwargs: Any) -> Any: ...


class PublishingControl(Protocol):
    def snapshot(self) -> Any: ...


StatePredicate = Callable[[Session, EventEnvelope], ReconciliationDisposition]


@dataclass(frozen=True, slots=True)
class EventReconciliationPolicy:
    """Closed ownership and durable-currency rule for one replayable input event."""

    event_type: EventType
    stream: str
    consumer_group: str
    current_state_predicate: StatePredicate


class EventReconciliationService:
    """Re-evaluate durable work and restore only policy-approved transport copies."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        publisher: EventPublisher,
        group_client: ConsumerGroupClient,
        publishing_control: PublishingControl,
    ) -> None:
        self.session_factory = session_factory
        self.publisher = publisher
        self.group_client = group_client
        self.publishing_control = publishing_control

    async def reconcile(
        self,
        *,
        mode: ReconciliationMode,
        limit: int = DEFAULT_RECONCILIATION_LIMIT,
        reason: str | None = None,
        event_type: EventType | None = None,
        after_outbox_id: UUID | None = None,
    ) -> ReconciliationReport:
        if not 1 <= limit <= MAX_RECONCILIATION_LIMIT:
            return self._empty(mode, limit, None, "INVALID_LIMIT")
        if mode is ReconciliationMode.APPLY and (
            reason is None or not reason.strip() or len(reason.strip()) > 500
        ):
            return self._empty(mode, limit, None, "REASON_REQUIRED")

        paused: bool | None = None
        if mode is ReconciliationMode.APPLY:
            try:
                snapshot = self.publishing_control.snapshot()
            except Exception:
                return self._empty(mode, limit, None, "CONTROL_UNAVAILABLE")
            if not snapshot.available:
                return self._empty(mode, limit, None, "CONTROL_UNAVAILABLE")
            paused = bool(snapshot.effective_pause)
            if not paused:
                return self._empty(mode, limit, False, "PUBLISHING_NOT_PAUSED")
        else:
            try:
                snapshot = self.publishing_control.snapshot()
                paused = bool(snapshot.effective_pause) if snapshot.available else None
            except Exception:
                paused = None

        try:
            candidates = self._classify(
                limit=limit,
                event_type=event_type,
                after_outbox_id=after_outbox_id,
            )
        except InvalidReconciliationCursor:
            return self._empty(
                mode,
                limit,
                paused,
                "INVALID_CURSOR",
                after_outbox_id=after_outbox_id,
            )
        except Exception:
            return self._empty(
                mode,
                limit,
                paused,
                "DATABASE_UNAVAILABLE",
                after_outbox_id=after_outbox_id,
            )

        if mode is ReconciliationMode.DRY_RUN:
            return self._report(
                mode,
                limit,
                paused,
                candidates,
                after_outbox_id=after_outbox_id,
            )

        created: list[ReconciliationTarget] = []
        ready_targets: set[tuple[str, str]] = set()
        updated = list(candidates)
        for index, (item, event) in enumerate(candidates):
            if item.disposition is not ReconciliationDisposition.REPLAY_REQUIRED or event is None:
                continue
            assert item.stream is not None and item.consumer_group is not None
            target_key = (item.stream, item.consumer_group)
            if target_key not in ready_targets:
                try:
                    was_created = await self._ensure_group(*target_key)
                except Exception:
                    return self._report(
                        mode,
                        limit,
                        paused,
                        updated,
                        after_outbox_id=after_outbox_id,
                        groups_created=created,
                        error_code="GROUP_RESTORE_FAILED",
                    )
                ready_targets.add(target_key)
                if was_created:
                    created.append(
                        ReconciliationTarget(stream=item.stream, consumer_group=item.consumer_group)
                    )
            try:
                await self.publisher.publish(event)
            except Exception:
                return self._report(
                    mode,
                    limit,
                    paused,
                    updated,
                    after_outbox_id=after_outbox_id,
                    groups_created=created,
                    error_code="TRANSPORT_REPLAY_FAILED",
                )
            updated[index] = (item.model_copy(update={"replayed": True}), event)
        return self._report(
            mode,
            limit,
            paused,
            updated,
            after_outbox_id=after_outbox_id,
            groups_created=created,
        )

    def _classify(
        self,
        *,
        limit: int,
        event_type: EventType | None,
        after_outbox_id: UUID | None,
    ) -> list[tuple[ReconciliationItem, EventEnvelope | None]]:
        with self.session_factory() as session:
            statement = select(EventOutbox).where(EventOutbox.status == OutboxStatus.PUBLISHED)
            if after_outbox_id is not None:
                cursor = session.get(EventOutbox, after_outbox_id)
                if cursor is None or cursor.status != OutboxStatus.PUBLISHED:
                    raise InvalidReconciliationCursor
                cursor_time = cursor.published_at or cursor.created_at
                statement = statement.where(
                    or_(
                        func.coalesce(EventOutbox.published_at, EventOutbox.created_at)
                        > cursor_time,
                        and_(
                            func.coalesce(EventOutbox.published_at, EventOutbox.created_at)
                            == cursor_time,
                            EventOutbox.id > cursor.id,
                        ),
                    )
                )
            if event_type is not None:
                statement = statement.where(EventOutbox.event_type == event_type.value)
            rows = list(
                session.scalars(
                    statement.order_by(
                        func.coalesce(EventOutbox.published_at, EventOutbox.created_at),
                        EventOutbox.id,
                    ).limit(limit)
                )
            )
            return [self._classify_row(session, row) for row in rows]

    def _classify_row(
        self, session: Session, row: EventOutbox
    ) -> tuple[ReconciliationItem, EventEnvelope | None]:
        base = {
            "outbox_id": row.id,
            "event_id": row.event_id,
            "event_type": row.event_type[:128],
        }
        try:
            event = envelope_from_outbox(row)
        except Exception:
            return (
                ReconciliationItem(
                    **base,
                    disposition=ReconciliationDisposition.INVALID_DURABLE_EVENT,
                ),
                None,
            )
        policy = RECONCILIATION_POLICY_BY_EVENT.get(event.event_type)
        if policy is None:
            return (
                ReconciliationItem(**base, disposition=ReconciliationDisposition.UNSUPPORTED),
                event,
            )
        item_base = {
            **base,
            "stream": policy.stream,
            "consumer_group": policy.consumer_group,
        }
        if session.get(ProcessedEvent, (event.event_id, policy.consumer_group)) is not None:
            return (
                ReconciliationItem(
                    **item_base,
                    disposition=ReconciliationDisposition.ALREADY_PROCESSED,
                ),
                event,
            )
        disposition = self._domain_disposition(session, event, policy)
        return ReconciliationItem(**item_base, disposition=disposition), event

    def _domain_disposition(
        self, session: Session, event: EventEnvelope, policy: EventReconciliationPolicy
    ) -> ReconciliationDisposition:
        downstream = _DOWNSTREAM_EVENTS.get(event.event_type, ())
        if downstream and session.scalar(
            select(EventOutbox.id)
            .where(
                EventOutbox.causation_id == event.event_id,
                EventOutbox.event_type.in_(tuple(item.value for item in downstream)),
            )
            .limit(1)
        ):
            return ReconciliationDisposition.DOMAIN_COMPLETE
        return policy.current_state_predicate(session, event)

    async def _ensure_group(self, stream: str, group: str) -> bool:
        try:
            await self.group_client.xgroup_create(stream, group, id="0", mkstream=True)
            return True
        except Exception as exc:
            if "BUSYGROUP" in str(exc).upper():
                return False
            raise RuntimeError("canonical consumer group restoration failed") from None

    @staticmethod
    def _empty(
        mode: ReconciliationMode,
        limit: int,
        paused: bool | None,
        error_code: str,
        *,
        after_outbox_id: UUID | None = None,
    ) -> ReconciliationReport:
        return ReconciliationReport(
            mode=mode,
            publishing_paused=paused,
            limit=limit,
            after_outbox_id=after_outbox_id,
            scanned=0,
            replay_required=0,
            replayed=0,
            already_processed=0,
            domain_complete=0,
            manual_review_required=0,
            unsupported=0,
            invalid=0,
            error_code=error_code,
        )

    @staticmethod
    def _report(
        mode: ReconciliationMode,
        limit: int,
        paused: bool | None,
        candidates: list[tuple[ReconciliationItem, EventEnvelope | None]],
        *,
        after_outbox_id: UUID | None = None,
        groups_created: list[ReconciliationTarget] | None = None,
        error_code: str | None = None,
    ) -> ReconciliationReport:
        items = tuple(item for item, _event in candidates)
        targets = tuple(
            ReconciliationTarget(stream=stream, consumer_group=group)
            for stream, group in sorted(
                {
                    (item.stream, item.consumer_group)
                    for item in items
                    if item.stream is not None and item.consumer_group is not None
                }
            )
        )

        def count(disposition: ReconciliationDisposition) -> int:
            return sum(item.disposition is disposition for item in items)

        return ReconciliationReport(
            mode=mode,
            publishing_paused=paused,
            limit=limit,
            after_outbox_id=after_outbox_id,
            next_after_outbox_id=items[-1].outbox_id if items else None,
            scanned=len(items),
            replay_required=count(ReconciliationDisposition.REPLAY_REQUIRED),
            replayed=sum(item.replayed for item in items),
            already_processed=count(ReconciliationDisposition.ALREADY_PROCESSED),
            domain_complete=count(ReconciliationDisposition.DOMAIN_COMPLETE),
            manual_review_required=count(ReconciliationDisposition.MANUAL_REVIEW_REQUIRED),
            unsupported=count(ReconciliationDisposition.UNSUPPORTED),
            invalid=count(ReconciliationDisposition.INVALID_DURABLE_EVENT),
            targets=targets,
            groups_created=tuple(groups_created or ()),
            error_code=error_code,
            items=items,
        )


class InvalidReconciliationCursor(Exception):
    """The requested durable page boundary does not identify a PUBLISHED row."""


_DOWNSTREAM_EVENTS: dict[EventType, tuple[EventType, ...]] = {
    EventType.ARTICLE_DISCOVERED: (EventType.ARTICLE_NORMALIZED,),
    EventType.ARTICLE_NORMALIZED: (EventType.STORY_CREATED, EventType.STORY_CLUSTERED),
    EventType.STORY_CREATED: (EventType.CLAIMS_EXTRACTED,),
    EventType.STORY_CLUSTERED: (EventType.CLAIMS_EXTRACTED,),
    EventType.CLAIMS_EXTRACTED: (EventType.EVIDENCE_REQUESTED,),
    EventType.EVIDENCE_REQUESTED: (EventType.EVIDENCE_COLLECTED,),
    EventType.EVIDENCE_COLLECTED: (EventType.FACT_CHECK_COMPLETED,),
    EventType.FACT_CHECK_COMPLETED: (EventType.STORY_VERIFIED,),
    EventType.STORY_VERIFIED: (EventType.CONTENT_REQUESTED,),
    EventType.CONTENT_REQUESTED: (EventType.CONTENT_GENERATED,),
    EventType.CONTENT_GENERATED: (EventType.CONTENT_QUALITY_CHECKED,),
}


def _non_publication_disposition(
    session: Session, event: EventEnvelope
) -> ReconciliationDisposition:
    try:
        payload = event.payload
        if event.event_type is EventType.ARTICLE_DISCOVERED:
            discovery = session.scalar(
                select(ArticleDiscovery).where(ArticleDiscovery.event_id == event.event_id)
            )
            if discovery is None:
                article = session.get(Article, UUID(payload["article_id"]))
                return (
                    ReconciliationDisposition.MANUAL_REVIEW_REQUIRED
                    if article is not None
                    else ReconciliationDisposition.INVALID_DURABLE_EVENT
                )
            if discovery.article_id != UUID(payload["article_id"]):
                return ReconciliationDisposition.INVALID_DURABLE_EVENT
            return (
                ReconciliationDisposition.DOMAIN_COMPLETE
                if discovery.normalized_article_version_id is not None
                else ReconciliationDisposition.REPLAY_REQUIRED
            )
        if event.event_type is EventType.ARTICLE_NORMALIZED:
            version = session.get(ArticleVersion, UUID(payload["article_version_id"]))
            if (
                version is None
                or version.article_id != UUID(payload["article_id"])
                or version.content_hash != payload["content_hash"]
            ):
                return ReconciliationDisposition.INVALID_DURABLE_EVENT
            linked = session.scalar(
                select(StorySource.story_id)
                .where(StorySource.article_id == version.article_id)
                .limit(1)
            )
            return (
                ReconciliationDisposition.DOMAIN_COMPLETE
                if linked is not None
                else ReconciliationDisposition.REPLAY_REQUIRED
            )
        if event.event_type in {EventType.STORY_CREATED, EventType.STORY_CLUSTERED}:
            story_id = UUID(payload["story_id"])
            if story_id != event.aggregate_id or session.get(Story, story_id) is None:
                return ReconciliationDisposition.INVALID_DURABLE_EVENT
            has_claim = session.scalar(select(Claim.id).where(Claim.story_id == story_id).limit(1))
            return (
                ReconciliationDisposition.DOMAIN_COMPLETE
                if has_claim is not None
                else ReconciliationDisposition.REPLAY_REQUIRED
            )
        if event.event_type is EventType.CLAIMS_EXTRACTED:
            claim_ids = tuple(UUID(item) for item in payload["claim_ids"])
            claims = list(session.scalars(select(Claim).where(Claim.id.in_(claim_ids))))
            if len(claims) != len(claim_ids) or any(
                claim.story_id != UUID(payload["story_id"]) for claim in claims
            ):
                return ReconciliationDisposition.INVALID_DURABLE_EVENT
            return (
                ReconciliationDisposition.DOMAIN_COMPLETE
                if all(claim.current_research_run_id is not None for claim in claims)
                else ReconciliationDisposition.REPLAY_REQUIRED
            )
        if event.event_type is EventType.EVIDENCE_REQUESTED:
            job = session.get(Job, event.aggregate_id)
            if job is None or job.job_type != "RESEARCH":
                return ReconciliationDisposition.INVALID_DURABLE_EVENT
            return (
                ReconciliationDisposition.DOMAIN_COMPLETE
                if job.status in {"COMPLETED", "SUPERSEDED"}
                else ReconciliationDisposition.REPLAY_REQUIRED
                if job.status in {"PENDING", "RUNNING"}
                else ReconciliationDisposition.MANUAL_REVIEW_REQUIRED
            )
        if event.event_type is EventType.EVIDENCE_COLLECTED:
            run_id = UUID(payload["research_run_id"])
            if run_id != event.aggregate_id:
                return ReconciliationDisposition.INVALID_DURABLE_EVENT
            claim_ids = tuple(UUID(item) for item in payload["claim_ids"])
            claims = list(session.scalars(select(Claim).where(Claim.id.in_(claim_ids))))
            if len(claims) != len(claim_ids):
                return ReconciliationDisposition.INVALID_DURABLE_EVENT
            current = [claim for claim in claims if claim.current_research_run_id == run_id]
            if not current:
                return ReconciliationDisposition.DOMAIN_COMPLETE
            checks = {
                item.id: item
                for item in session.scalars(
                    select(FactCheck).where(
                        FactCheck.id.in_(
                            tuple(
                                claim.current_fact_check_id
                                for claim in current
                                if claim.current_fact_check_id is not None
                            )
                        )
                    )
                )
            }
            complete = all(
                claim.current_fact_check_id in checks
                and checks[claim.current_fact_check_id].research_run_id == run_id
                and checks[claim.current_fact_check_id].research_generation
                == claim.research_generation
                for claim in current
            )
            return (
                ReconciliationDisposition.DOMAIN_COMPLETE
                if complete
                else ReconciliationDisposition.REPLAY_REQUIRED
            )
        if event.event_type is EventType.FACT_CHECK_COMPLETED:
            check = session.get(FactCheck, UUID(payload["fact_check_id"]))
            if check is None or check.story_id != UUID(payload["story_id"]):
                return ReconciliationDisposition.INVALID_DURABLE_EVENT
            claim = session.get(Claim, check.claim_id)
            if claim is None:
                return ReconciliationDisposition.INVALID_DURABLE_EVENT
            if claim.current_fact_check_id != check.id:
                return ReconciliationDisposition.DOMAIN_COMPLETE
            story = session.get(Story, check.story_id)
            return (
                ReconciliationDisposition.DOMAIN_COMPLETE
                if story is not None and story.verification_semantic_key is not None
                else ReconciliationDisposition.REPLAY_REQUIRED
            )
        if event.event_type is EventType.STORY_VERIFIED:
            story_id = UUID(payload["story_id"])
            claims = list(session.scalars(select(Claim).where(Claim.story_id == story_id)))
            current_ids = {claim.current_fact_check_id for claim in claims}
            if None in current_ids or current_ids != {UUID(x) for x in payload["fact_check_ids"]}:
                return ReconciliationDisposition.DOMAIN_COMPLETE
            sheets = list(session.scalars(select(FactSheet).where(FactSheet.story_id == story_id)))
            expected = {str(item) for item in current_ids}
            complete = any(
                {str(item.get("fact_check_id")) for item in sheet.fact_checks_snapshot} == expected
                for sheet in sheets
            )
            return (
                ReconciliationDisposition.DOMAIN_COMPLETE
                if complete
                else ReconciliationDisposition.REPLAY_REQUIRED
            )
        if event.event_type is EventType.CONTENT_REQUESTED:
            sheet = session.get(FactSheet, UUID(payload["fact_sheet_id"]))
            if sheet is None or sheet.story_id != UUID(payload["story_id"]):
                return ReconciliationDisposition.INVALID_DURABLE_EVENT
            latest = session.scalar(
                select(func.max(FactSheet.version)).where(FactSheet.story_id == sheet.story_id)
            )
            if latest != sheet.version:
                return ReconciliationDisposition.DOMAIN_COMPLETE
            draft = session.scalar(
                select(ContentDraft.id)
                .where(
                    ContentDraft.fact_sheet_id == sheet.id,
                    ContentDraft.fact_sheet_version == sheet.version,
                )
                .limit(1)
            )
            return (
                ReconciliationDisposition.DOMAIN_COMPLETE
                if draft is not None
                else ReconciliationDisposition.REPLAY_REQUIRED
            )
        if event.event_type is EventType.CONTENT_GENERATED:
            draft = session.get(ContentDraft, UUID(payload["content_draft_id"]))
            if draft is None or draft.story_id != UUID(payload["story_id"]):
                return ReconciliationDisposition.INVALID_DURABLE_EVENT
            variant_ids = tuple(UUID(item) for item in payload["content_variant_ids"])
            variants = list(
                session.scalars(
                    select(ContentVariant).where(
                        ContentVariant.id.in_(variant_ids),
                        ContentVariant.content_draft_id == draft.id,
                    )
                )
            )
            if len(variants) != len(variant_ids):
                return ReconciliationDisposition.INVALID_DURABLE_EVENT
            checked = set(
                session.scalars(
                    select(ContentQualityCheck.content_variant_id).where(
                        ContentQualityCheck.content_variant_id.in_(variant_ids),
                        ContentQualityCheck.methodology_version == CURRENT_QUALITY_METHODOLOGY,
                    )
                )
            )
            return (
                ReconciliationDisposition.DOMAIN_COMPLETE
                if checked == set(variant_ids)
                else ReconciliationDisposition.REPLAY_REQUIRED
            )
    except (KeyError, TypeError, ValueError):
        return ReconciliationDisposition.INVALID_DURABLE_EVENT
    return ReconciliationDisposition.UNSUPPORTED


def _publication_disposition(session: Session, event: EventEnvelope) -> ReconciliationDisposition:
    try:
        publication_id = UUID(event.payload["publication_id"])
    except (KeyError, TypeError, ValueError):
        return ReconciliationDisposition.INVALID_DURABLE_EVENT
    row = session.get(Publication, publication_id)
    if (
        row is None
        or event.aggregate_id != row.id
        or row.scheduled_event_id != event.event_id
        or str(row.content_variant_id) != event.payload.get("content_variant_id")
        or str(row.social_account_id) != event.payload.get("social_account_id")
    ):
        return ReconciliationDisposition.INVALID_DURABLE_EVENT
    if row.status in {PublicationStatus.PUBLISHED, PublicationStatus.CANCELLED}:
        return ReconciliationDisposition.DOMAIN_COMPLETE
    attempt = session.scalar(
        select(PublicationAttempt)
        .where(PublicationAttempt.publication_id == row.id)
        .order_by(PublicationAttempt.attempt_number.desc())
        .limit(1)
    )
    if attempt is None:
        return (
            ReconciliationDisposition.REPLAY_REQUIRED
            if row.status is PublicationStatus.SCHEDULED and row.external_post_id is None
            else ReconciliationDisposition.DOMAIN_COMPLETE
            if row.status in {PublicationStatus.BLOCKED, PublicationStatus.FAILED}
            else ReconciliationDisposition.MANUAL_REVIEW_REQUIRED
        )
    if attempt.ambiguous or attempt.status is PublicationAttemptStatus.AMBIGUOUS:
        return ReconciliationDisposition.MANUAL_REVIEW_REQUIRED
    external_id = attempt.external_post_id or row.external_post_id
    if external_id is not None:
        return (
            ReconciliationDisposition.REPLAY_REQUIRED
            if attempt.status is PublicationAttemptStatus.IN_PROGRESS
            and attempt.phase
            in {
                PublicationAttemptPhase.PUBLISH_RESPONSE_RECEIVED,
                PublicationAttemptPhase.VERIFYING,
            }
            else ReconciliationDisposition.DOMAIN_COMPLETE
            if attempt.status is PublicationAttemptStatus.SUCCEEDED
            else ReconciliationDisposition.MANUAL_REVIEW_REQUIRED
        )
    if attempt.phase in {
        PublicationAttemptPhase.PUBLISH_INTENT_RECORDED,
        PublicationAttemptPhase.PUBLISH_RESPONSE_RECEIVED,
        PublicationAttemptPhase.VERIFYING,
        PublicationAttemptPhase.COMPLETE,
    }:
        return ReconciliationDisposition.MANUAL_REVIEW_REQUIRED
    if attempt.status is PublicationAttemptStatus.IN_PROGRESS and attempt.phase in {
        PublicationAttemptPhase.PREPARING,
        PublicationAttemptPhase.PREPARED,
    }:
        return ReconciliationDisposition.REPLAY_REQUIRED
    if row.status in {PublicationStatus.BLOCKED, PublicationStatus.FAILED}:
        return ReconciliationDisposition.DOMAIN_COMPLETE
    return ReconciliationDisposition.MANUAL_REVIEW_REQUIRED


RECONCILIATION_POLICIES = tuple(
    EventReconciliationPolicy(
        event_type=contract.event_type,
        stream=contract.stream,
        consumer_group=contract.consumer_group,
        current_state_predicate=(
            _publication_disposition
            if contract.event_type is EventType.PUBLICATION_SCHEDULED
            else _non_publication_disposition
        ),
    )
    for contract in CONSUMER_CONTRACT_BY_EVENT.values()
)
RECONCILIATION_POLICY_BY_EVENT = {policy.event_type: policy for policy in RECONCILIATION_POLICIES}

if len(RECONCILIATION_POLICY_BY_EVENT) != len(RECONCILIATION_POLICIES):  # pragma: no cover
    raise RuntimeError("event reconciliation policies contain duplicate ownership")
