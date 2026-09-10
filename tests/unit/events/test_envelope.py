from datetime import datetime
from uuid import uuid4

import pytest
from news_ai_events import EventEnvelope, EventType
from pydantic import ValidationError


def _event(**overrides: object) -> EventEnvelope:
    values: dict[str, object] = {
        "event_type": EventType.ARTICLE_DISCOVERED,
        "producer": "collector",
        "producer_version": "0.1.0",
        "aggregate_type": "article",
        "aggregate_id": uuid4(),
        "idempotency_key": "article:source:url",
    }
    values.update(overrides)
    return EventEnvelope.model_validate(values)


def test_payload_defaults_are_not_shared() -> None:
    first = _event()
    second = _event()
    first.payload["x"] = 1

    assert second.payload == {}


def test_naive_event_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _event(occurred_at=datetime(2026, 9, 9, 12, 0, 0))
