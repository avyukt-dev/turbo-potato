from uuid import uuid4

from news_ai_events import EventEnvelope, EventType
from news_ai_events.outbox import build_outbox_record


def test_event_converts_to_pending_outbox_record() -> None:
    event = EventEnvelope(
        event_type=EventType.STORY_CREATED,
        producer="processor",
        producer_version="0.1.0",
        aggregate_type="story",
        aggregate_id=uuid4(),
        idempotency_key="story:example",
        payload={"story_id": "example"},
    )

    record = build_outbox_record(event)

    assert record.event_id == event.event_id
    assert record.event_type == "story.created"
    assert record.payload == {"story_id": "example"}
