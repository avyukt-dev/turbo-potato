"""Transactional outbox bridge.

The caller adds the returned record to the same SQLAlchemy transaction as the business-state change.
Redis publication happens later from committed outbox rows.
"""

from news_ai_database.models import EventOutbox

from .envelope import EventEnvelope


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
