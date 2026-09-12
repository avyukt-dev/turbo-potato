"""Closed scheduling contracts and UTC clock boundary."""

from datetime import UTC, datetime
from uuid import UUID

from news_ai_domain import PublicationStatus
from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(UTC)


def system_clock() -> datetime:
    return datetime.now(UTC)


class CreatePublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    content_variant_id: UUID
    social_account_id: UUID
    platform: str | None = None
    scheduled_at: datetime | None = None
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")

    @field_validator("scheduled_at")
    @classmethod
    def timestamp(cls, value: datetime | None) -> datetime | None:
        return utc(value) if value is not None else None


class CancelPublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reason: str | None = Field(default=None, max_length=2000)


class PublicationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    content_variant_id: UUID
    content_variant_version: int
    content_draft_id: UUID
    fact_sheet_id: UUID
    fact_sheet_version: int
    review_decision_id: UUID
    quality_check_id: UUID
    social_account_id: UUID
    platform: str
    status: PublicationStatus
    scheduled_at: datetime | None
    scheduled_event_id: UUID | None
    blocking_reason: str | None
    created_at: datetime
    updated_at: datetime


class SchedulerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    poll_interval_seconds: int = Field(ge=1, le=3600)
    batch_size: int = Field(ge=1, le=1000)
    publishing_paused: bool
