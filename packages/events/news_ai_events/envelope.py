"""Versioned event envelope."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from .types import EventType


class EventEnvelope(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    schema_version: int = Field(default=1, ge=1)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    producer: str = Field(min_length=1, max_length=64)
    producer_version: str = Field(min_length=1, max_length=64)
    aggregate_type: str = Field(min_length=1, max_length=64)
    aggregate_id: UUID
    correlation_id: UUID = Field(default_factory=uuid4)
    causation_id: UUID | None = None
    idempotency_key: str = Field(min_length=1, max_length=255)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value
