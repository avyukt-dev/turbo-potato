from datetime import UTC, datetime
from uuid import uuid4

import pytest
from news_ai_events import EventEnvelope, EventType
from news_ai_events.payloads import PublicationScheduledV1
from pydantic import ValidationError


def payload():
    return {
        "publication_id": str(uuid4()),
        "content_variant_id": str(uuid4()),
        "social_account_id": str(uuid4()),
        "scheduled_at": "2030-01-01T12:00:00Z",
    }


def test_canonical_scheduled_payload_and_version():
    data = payload()
    parsed = PublicationScheduledV1.model_validate(data)
    assert parsed.scheduled_at == datetime(2030, 1, 1, 12, tzinfo=UTC)
    with pytest.raises(ValidationError):
        EventEnvelope(
            event_type=EventType.PUBLICATION_SCHEDULED,
            schema_version=2,
            producer="scheduler",
            producer_version="0.1.0",
            aggregate_type="publication",
            aggregate_id=uuid4(),
            idempotency_key="scheduled",
            payload=data,
        )


@pytest.mark.parametrize("mutation", ["missing", "uuid", "naive", "secret", "body"])
def test_scheduled_contract_rejects_invalid_or_expansive_payload(mutation):
    data = payload()
    if mutation == "missing":
        del data["publication_id"]
    elif mutation == "uuid":
        data["social_account_id"] = "not-a-uuid"
    elif mutation == "naive":
        data["scheduled_at"] = "2030-01-01T12:00:00"
    else:
        data["access_token" if mutation == "secret" else "body"] = "untrusted"
    with pytest.raises(ValidationError):
        PublicationScheduledV1.model_validate(data)
