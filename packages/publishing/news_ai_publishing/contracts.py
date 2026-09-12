"""Closed scheduling contracts and UTC clock boundary."""

import re
from datetime import UTC, datetime
from uuid import UUID

from news_ai_database import PublicationAttemptPhase, PublicationAttemptStatus
from news_ai_domain import PublicationStatus
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .execution_config import PublisherConfig


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


class PublicationAttemptView(BaseModel):
    """Bounded diagnostics only; no command, token, raw response or credentials."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: UUID
    publication_id: UUID
    attempt_number: int
    status: PublicationAttemptStatus
    phase: PublicationAttemptPhase
    execution_request_hash: str
    trigger_event_id: UUID | None
    provider_operation_id: str | None
    external_post_id: str | None
    external_url: str | None
    error_code: str | None
    error_class: str | None
    error_message: str | None
    retryable: bool | None
    ambiguous: bool
    retry_after_at: datetime | None
    started_at: datetime
    completed_at: datetime | None
    provider_metadata: dict[str, bool | str | list[str]] = Field(default_factory=dict)

    @field_validator("provider_metadata", mode="before")
    @classmethod
    def safe_provider_metadata(cls, value):
        # Allowlisted operational checkpoints only, never raw provider JSON.
        safe = {}
        if not isinstance(value, dict):
            return safe
        if isinstance(value.get("mock"), bool):
            safe["mock"] = value["mock"]
        status = value.get("recovery_container_status")
        if status in {"UNKNOWN", "FINISHED", "IN_PROGRESS", "ERROR", "EXPIRED", "PUBLISHED"}:
            safe["recovery_container_status"] = status
        children = value.get("child_container_ids")
        if (
            isinstance(children, list)
            and len(children) <= 20
            and all(
                isinstance(item, str)
                and re.fullmatch(r"(?:[0-9]{1,255}|mock_child_[0-9a-f]{24}_[0-9]+)", item)
                for item in children
            )
        ):
            safe["child_container_ids"] = children
        return safe


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
    started_at: datetime | None = None
    published_at: datetime | None = None
    external_post_id: str | None = None
    external_url: str | None = None
    failure_reason: str | None = None
    attempt_count: int = 0
    next_retry_at: datetime | None = None


class SchedulerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    poll_interval_seconds: int = Field(ge=1, le=3600)
    batch_size: int = Field(ge=1, le=1000)
    publishing_paused: bool
    publisher: PublisherConfig = Field(default_factory=PublisherConfig)
