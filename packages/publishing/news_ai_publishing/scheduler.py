"""Bounded PostgreSQL due scanner. It never invokes a social adapter."""

from datetime import UTC

from news_ai_database import Publication
from news_ai_domain import PublicationStatus
from news_ai_events import EventEnvelope, EventType
from news_ai_events.outbox import build_outbox_record
from sqlalchemy import select

from .contracts import SchedulerConfig, utc
from .errors import PublicationError
from .service import PublicationService


class PublicationScheduler:
    def __init__(self, service: PublicationService, config: SchedulerConfig):
        self.service = service
        self.config = config

    def scan(self) -> int:
        if self.config.publishing_paused:
            return 0
        now = utc(self.service.clock())
        dispatched = 0
        # One graph per transaction avoids holding unrelated Story/account locks
        # across the batch (which could create cross-story lock-order cycles).
        for _ in range(self.config.batch_size):
            with self.service.session_factory() as session, session.begin():
                row = session.scalar(
                    select(Publication)
                    .where(
                        Publication.status == PublicationStatus.SCHEDULED,
                        Publication.scheduled_at <= now,
                        Publication.scheduled_event_id.is_(None),
                    )
                    .order_by(Publication.scheduled_at, Publication.created_at, Publication.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    break
                try:
                    self.service.revalidate(session, row)
                except PublicationError as exc:
                    row.status = PublicationStatus.BLOCKED
                    row.blocking_reason = exc.code
                    row.updated_at = now
                    self.service.audit(
                        session,
                        row,
                        "PUBLICATION_BLOCKED",
                        None,
                        now,
                        old=PublicationStatus.SCHEDULED,
                    )
                    continue
                event = EventEnvelope(
                    event_type=EventType.PUBLICATION_SCHEDULED,
                    occurred_at=now,
                    producer="scheduler",
                    producer_version="0.1.0",
                    aggregate_type="publication",
                    aggregate_id=row.id,
                    **({"correlation_id": row.correlation_id} if row.correlation_id else {}),
                    idempotency_key=f"publication.scheduled:{row.id}",
                    payload={
                        "publication_id": str(row.id),
                        "content_variant_id": str(row.content_variant_id),
                        "social_account_id": str(row.social_account_id),
                        # SQLite strips stored timezone information. Production
                        # PostgreSQL TIMESTAMPTZ and all input clocks stay strict.
                        "scheduled_at": utc(
                            row.scheduled_at.replace(tzinfo=UTC)
                            if session.bind.dialect.name == "sqlite"
                            else row.scheduled_at
                        ).isoformat(),
                    },
                )
                session.add(build_outbox_record(event))
                session.flush()
                row.scheduled_event_id = event.event_id
                row.updated_at = now
                self.service.audit(
                    session,
                    row,
                    "PUBLICATION_SCHEDULED",
                    None,
                    now,
                    old=PublicationStatus.SCHEDULED,
                )
                dispatched += 1
        return dispatched
