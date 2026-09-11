from datetime import datetime
from uuid import uuid4

import pytest
from news_ai_events import EventEnvelope, EventType
from pydantic import ValidationError


def _event(**overrides: object) -> EventEnvelope:
    article_id = uuid4()
    values: dict[str, object] = {
        "event_type": EventType.ARTICLE_DISCOVERED,
        "producer": "collector",
        "producer_version": "0.1.0",
        "aggregate_type": "article",
        "aggregate_id": article_id,
        "idempotency_key": "article:source:url",
        "payload": {
            "article_id": str(article_id),
            "source_id": str(uuid4()),
            "source_feed_id": str(uuid4()),
            "canonical_url": "https://example.com/article",
            "title": "Example",
            "published_at": None,
        },
    }
    values.update(overrides)
    return EventEnvelope.model_validate(values)


def test_payload_defaults_are_not_shared() -> None:
    first = _event()
    second = _event()
    first.payload["title"] = "Changed"

    assert second.payload["title"] == "Example"


def test_naive_event_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _event(occurred_at=datetime(2026, 9, 9, 12, 0, 0))


def test_canonical_fact_check_completed_payload_is_accepted() -> None:
    story_id = uuid4()
    fact_check_id = uuid4()
    event = EventEnvelope(
        event_type=EventType.FACT_CHECK_COMPLETED,
        producer="research-worker",
        producer_version="0.1.0",
        aggregate_type="story",
        aggregate_id=story_id,
        idempotency_key=f"fact_check.completed:{fact_check_id}",
        payload={
            "story_id": str(story_id),
            "fact_check_id": str(fact_check_id),
            "label": "UNVERIFIED",
            "confidence_score": None,
            "review_required": True,
        },
    )

    assert event.payload["fact_check_id"] == str(fact_check_id)


@pytest.mark.parametrize(
    "payload_update,missing_field",
    [
        ({"fact_check_id": None}, "fact_check_id"),
        ({"fact_check_ids": [str(uuid4())]}, "fact_check_ids"),
        ({"confidence_score": 1.1}, "confidence_score"),
    ],
)
def test_fact_check_completed_rejects_missing_extra_and_invalid_fields(
    payload_update: dict[str, object],
    missing_field: str,
) -> None:
    story_id = uuid4()
    payload: dict[str, object] = {
        "story_id": str(story_id),
        "fact_check_id": str(uuid4()),
        "label": "TRUE",
        "confidence_score": 0.8,
        "review_required": False,
    }
    payload.update(payload_update)
    if payload_update.get("fact_check_id") is None:
        payload.pop("fact_check_id")

    with pytest.raises(ValidationError, match=missing_field):
        EventEnvelope(
            event_type=EventType.FACT_CHECK_COMPLETED,
            producer="research-worker",
            producer_version="0.1.0",
            aggregate_type="story",
            aggregate_id=story_id,
            idempotency_key="invalid-fact-check-event",
            payload=payload,
        )


def test_canonical_story_verified_payload_is_accepted() -> None:
    story_id = uuid4()
    event = EventEnvelope(
        event_type=EventType.STORY_VERIFIED,
        producer="research-worker",
        producer_version="0.1.0",
        aggregate_type="story",
        aggregate_id=story_id,
        idempotency_key=f"story.verified:{story_id}",
        payload={
            "story_id": str(story_id),
            "fact_check_ids": [str(uuid4())],
            "confidence_score": 0.75,
            "risk_level": "HIGH",
            "review_required": True,
        },
    )

    assert event.payload["risk_level"] == "HIGH"
    assert "claim_ids" not in event.payload


def test_story_verified_rejects_noncanonical_legacy_fields() -> None:
    story_id = uuid4()
    with pytest.raises(ValidationError, match="verification_stage_complete"):
        EventEnvelope(
            event_type=EventType.STORY_VERIFIED,
            producer="research-worker",
            producer_version="0.1.0",
            aggregate_type="story",
            aggregate_id=story_id,
            idempotency_key="legacy-story-verified",
            payload={
                "story_id": str(story_id),
                "fact_check_ids": [str(uuid4())],
                "confidence_score": None,
                "risk_level": "LOW",
                "review_required": False,
                "verification_stage_complete": True,
            },
        )


def test_unsupported_event_schema_version_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unsupported schema version 2"):
        _event(schema_version=2)


def test_payload_datetime_must_be_timezone_aware() -> None:
    publication_id = uuid4()
    with pytest.raises(ValidationError, match="scheduled_at must be timezone-aware"):
        EventEnvelope(
            event_type=EventType.PUBLICATION_SCHEDULED,
            producer="scheduler",
            producer_version="0.1.0",
            aggregate_type="publication",
            aggregate_id=publication_id,
            idempotency_key=f"publication.scheduled:{publication_id}",
            payload={
                "publication_id": str(publication_id),
                "content_variant_id": str(uuid4()),
                "social_account_id": str(uuid4()),
                "scheduled_at": "2026-09-10T12:00:00",
            },
        )
