from datetime import UTC, datetime, timedelta
from uuid import uuid4

from news_ai_database.models import OutboxStatus
from news_ai_events import EventEnvelope, EventType
from news_ai_events.outbox import (
    build_outbox_record,
    envelope_from_outbox,
    mark_failed,
    mark_published,
    mark_publishing,
    mark_retry,
)


def _record():
    story_id = uuid4()
    article_id = uuid4()
    event = EventEnvelope(
        event_type=EventType.STORY_CREATED,
        producer="processor",
        producer_version="0.1.0",
        aggregate_type="story",
        aggregate_id=story_id,
        idempotency_key="story:example",
        payload={
            "story_id": str(story_id),
            "article_id": str(article_id),
            "cluster_key": "example",
        },
    )
    return event, build_outbox_record(event)


def test_event_converts_to_outbox_and_back() -> None:
    event, record = _record()

    restored = envelope_from_outbox(record)

    assert restored.event_id == event.event_id
    assert restored.event_type == EventType.STORY_CREATED
    assert restored.payload == event.payload


def test_outbox_lifecycle_helpers() -> None:
    _, record = _record()
    mark_publishing(record)
    assert record.status == OutboxStatus.PUBLISHING
    assert record.attempt_count == 1

    retry_at = datetime.now(UTC) + timedelta(minutes=1)
    mark_retry(record, error="temporary", next_attempt_at=retry_at)
    assert record.status == OutboxStatus.PENDING
    assert record.next_attempt_at == retry_at

    mark_published(record)
    assert record.status == OutboxStatus.PUBLISHED
    assert record.published_at is not None

    mark_failed(record, error="permanent")
    assert record.status == OutboxStatus.FAILED
    assert record.last_error == "permanent"
