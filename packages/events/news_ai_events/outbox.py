"""Transactional outbox bridge.

The caller adds the returned record to the same SQLAlchemy transaction as the business-state change.
Redis publication happens later from committed outbox rows.
"""

from datetime import datetime, timezone

from news_ai_database.models import EventOutbox, OutboxStatus

from .envelope import EventEnvelope
from .types import EventType


def build_outbox_record(event: EventEnvelope) -> EventOutbox:
    return EventOutbox(
        event_id=event.event_id,
        event_type=event.event_type.value,
        schema_version=event.schema_version,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        idempotency_key=event.idempotency_key,
        payload=event.payload,
    )


def envelope_from_outbox(record: EventOutbox) -> EventEnvelope:
    return EventEnvelope(
        event_id=record.event_id,
        event_type=EventType(record.event_type),
        schema_version=record.schema_version,
        producer="outbox-publisher",
        producer_version="0.1.0",
        aggregate_type=record.aggregate_type,
        aggregate_id=record.aggregate_id,
        correlation_id=record.correlation_id,
        causation_id=record.causation_id,
        idempotency_key=record.idempotency_key,
        payload=record.payload,
    )


def mark_publishing(record: EventOutbox) -> None:
    record.status = OutboxStatus.PUBLISHING
    record.attempt_count = (record.attempt_count or 0) + 1
    record.last_error = None


def mark_published(record: EventOutbox, *, at: datetime | None = None) -> None:
    record.status = OutboxStatus.PUBLISHED
    record.published_at = at or datetime.now(timezone.utc)
    record.next_attempt_at = None
    record.last_error = None


def mark_retry(record: EventOutbox, *, error: str, next_attempt_at: datetime) -> None:
    record.status = OutboxStatus.PENDING
    record.next_attempt_at = next_attempt_at
    record.last_error = error


def mark_failed(record: EventOutbox, *, error: str) -> None:
    record.status = OutboxStatus.FAILED
    record.next_attempt_at = None
    record.last_error = error
