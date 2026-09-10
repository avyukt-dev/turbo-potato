"""Dispatch committed outbox rows to Redis Streams with bounded retry and stale-lease recovery."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from news_ai_database.models import EventOutbox, OutboxStatus
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from .outbox import envelope_from_outbox, mark_failed, mark_published, mark_publishing, mark_retry


class EventPublisher(Protocol):
    async def publish(self, event: object) -> str: ...


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    delays_seconds: tuple[int, ...] = (30, 120, 600, 1800, 7200)

    @property
    def max_attempts(self) -> int:
        return len(self.delays_seconds)

    def next_attempt_at(self, attempt_count: int, *, now: datetime) -> datetime:
        index = max(0, min(attempt_count - 1, len(self.delays_seconds) - 1))
        return now + timedelta(seconds=self.delays_seconds[index])


@dataclass(frozen=True, slots=True)
class DispatchStats:
    claimed: int = 0
    published: int = 0
    retried: int = 0
    failed: int = 0
    recovered: int = 0


class OutboxDispatcher:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        publisher: EventPublisher,
        *,
        batch_size: int = 50,
        retry_policy: RetryPolicy | None = None,
        publishing_lease_seconds: int = 300,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if publishing_lease_seconds < 1:
            raise ValueError("publishing_lease_seconds must be >= 1")
        self.session_factory = session_factory
        self.publisher = publisher
        self.batch_size = batch_size
        self.retry_policy = retry_policy or RetryPolicy()
        self.publishing_lease_seconds = publishing_lease_seconds

    def _recover_stale(self, *, now: datetime) -> int:
        cutoff = now - timedelta(seconds=self.publishing_lease_seconds)
        recovered = 0
        with self.session_factory() as session, session.begin():
            records = list(
                session.scalars(
                    select(EventOutbox)
                    .where(
                        EventOutbox.status == OutboxStatus.PUBLISHING,
                        EventOutbox.publishing_started_at.is_not(None),
                        EventOutbox.publishing_started_at <= cutoff,
                    )
                    .order_by(EventOutbox.publishing_started_at)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            for record in records:
                if record.attempt_count >= self.retry_policy.max_attempts:
                    mark_failed(record, error="stale publishing lease exhausted retry budget")
                else:
                    mark_retry(
                        record,
                        error="stale publishing lease recovered",
                        next_attempt_at=now,
                    )
                recovered += 1
        return recovered

    def _claim_ready(self, *, now: datetime) -> list[UUID]:
        with self.session_factory() as session, session.begin():
            records = list(
                session.scalars(
                    select(EventOutbox)
                    .where(
                        EventOutbox.status == OutboxStatus.PENDING,
                        or_(
                            EventOutbox.next_attempt_at.is_(None),
                            EventOutbox.next_attempt_at <= now,
                        ),
                    )
                    .order_by(EventOutbox.created_at, EventOutbox.id)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            for record in records:
                mark_publishing(record, at=now)
            return [record.id for record in records]

    def _load_event(self, record_id: UUID):
        with self.session_factory() as session:
            record = session.get(EventOutbox, record_id)
            if record is None:
                raise RuntimeError(f"outbox row disappeared: {record_id}")
            if record.status != OutboxStatus.PUBLISHING:
                raise RuntimeError(f"outbox row is not claimed: {record_id}")
            return envelope_from_outbox(record), record.attempt_count

    def _mark_success(self, record_id: UUID, *, now: datetime) -> None:
        with self.session_factory() as session, session.begin():
            record = session.get(EventOutbox, record_id, with_for_update=True)
            if record is not None and record.status == OutboxStatus.PUBLISHING:
                mark_published(record, at=now)

    def _mark_error(self, record_id: UUID, *, error: Exception, now: datetime) -> bool:
        with self.session_factory() as session, session.begin():
            record = session.get(EventOutbox, record_id, with_for_update=True)
            if record is None or record.status != OutboxStatus.PUBLISHING:
                return False
            message = f"{type(error).__name__}: {error}"
            if record.attempt_count >= self.retry_policy.max_attempts:
                mark_failed(record, error=message)
                return False
            mark_retry(
                record,
                error=message,
                next_attempt_at=self.retry_policy.next_attempt_at(record.attempt_count, now=now),
            )
            return True

    async def dispatch_once(self, *, now: datetime | None = None) -> DispatchStats:
        current = now or datetime.now(UTC)
        recovered = self._recover_stale(now=current)
        record_ids = self._claim_ready(now=current)
        published = retried = failed = 0

        for record_id in record_ids:
            try:
                event, _attempt_count = self._load_event(record_id)
                await self.publisher.publish(event)
                self._mark_success(record_id, now=datetime.now(UTC))
                published += 1
            except Exception as exc:  # provider/network errors are normalized into retry state here
                if self._mark_error(record_id, error=exc, now=datetime.now(UTC)):
                    retried += 1
                else:
                    failed += 1

        return DispatchStats(
            claimed=len(record_ids),
            published=published,
            retried=retried,
            failed=failed,
            recovered=recovered,
        )
