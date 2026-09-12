"""Scheduling persistence; deliberately no external execution/attempt state."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from news_ai_domain import PublicationStatus
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .models import JSON_TYPE, TimestampMixin, UUIDPrimaryKeyMixin


class SocialAccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    AUTH_ERROR = "AUTH_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    DISABLED = "DISABLED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"


class SocialAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "social_accounts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','PAUSED','AUTH_ERROR','RATE_LIMITED',"
            "'DISABLED','REAUTH_REQUIRED')",
            name="account_status",
        ),
        UniqueConstraint("platform", "account_identifier", name="uq_social_accounts_destination"),
    )
    platform: Mapped[str] = mapped_column(String(32))
    account_name: Mapped[str] = mapped_column(String(200))
    account_identifier: Mapped[str] = mapped_column(String(256))
    status: Mapped[SocialAccountStatus] = mapped_column(
        SAEnum(SocialAccountStatus, native_enum=False, length=32, validate_strings=True)
    )
    credential_reference: Mapped[str | None] = mapped_column(String(256))
    capabilities: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    rate_limit_state: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    account_metadata: Mapped[dict] = mapped_column("metadata", JSON_TYPE, default=dict)


class MediaAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Caller-owned artifact references only: no generation, upload or delivery code."""

    __tablename__ = "media_assets"
    __table_args__ = (CheckConstraint("length(file_hash) = 64", name="media_file_hash"),)
    asset_type: Mapped[str] = mapped_column(String(32))
    storage_provider: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(Text)
    public_url: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(128))
    file_hash: Mapped[str] = mapped_column(String(64))
    visual_check_status: Mapped[str | None] = mapped_column(String(32))
    source_metadata: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)


class Publication(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "publications"
    __table_args__ = (
        CheckConstraint(
            "content_variant_version >= 1 AND fact_sheet_version >= 1", name="publication_versions"
        ),
        CheckConstraint(
            "status IN ('DRAFT','READY_FOR_REVIEW','APPROVED','SCHEDULED',"
            "'PUBLISHING','RETRYING','BLOCKED','PUBLISHED','FAILED','CANCELLED')",
            name="publication_status",
        ),
        CheckConstraint(
            "status != 'SCHEDULED' OR scheduled_at IS NOT NULL", name="publication_schedule"
        ),
        CheckConstraint(
            "(status = 'CANCELLED') = (cancelled_at IS NOT NULL)", name="publication_cancelled"
        ),
        UniqueConstraint(
            "created_by_actor_id", "idempotency_key", name="uq_publications_actor_operation"
        ),
        CheckConstraint(
            "(publish_now_idempotency_key IS NULL AND publish_now_request_hash IS NULL "
            "AND publish_now_actor_id IS NULL) OR "
            "(publish_now_idempotency_key IS NOT NULL AND publish_now_request_hash IS NOT NULL "
            "AND publish_now_actor_id IS NOT NULL)",
            name="publication_publish_now_identity",
        ),
        CheckConstraint(
            "publish_now_request_hash IS NULL OR length(publish_now_request_hash) = 64",
            name="publication_publish_now_hash",
        ),
        Index("ix_publications_due", "status", "scheduled_at"),
        Index(
            "uq_publications_active_candidate",
            "content_variant_id",
            "content_variant_version",
            "social_account_id",
            unique=True,
            postgresql_where=text("status IN ('DRAFT','APPROVED','SCHEDULED')"),
            sqlite_where=text("status IN ('DRAFT','APPROVED','SCHEDULED')"),
        ),
    )
    content_variant_id: Mapped[UUID] = mapped_column(ForeignKey("content_variants.id"), index=True)
    content_variant_version: Mapped[int] = mapped_column(Integer)
    content_draft_id: Mapped[UUID] = mapped_column(ForeignKey("content_drafts.id"))
    fact_sheet_id: Mapped[UUID] = mapped_column(ForeignKey("fact_sheets.id"))
    fact_sheet_version: Mapped[int] = mapped_column(Integer)
    review_decision_id: Mapped[UUID] = mapped_column(ForeignKey("review_decisions.id"))
    quality_check_id: Mapped[UUID] = mapped_column(ForeignKey("content_quality_checks.id"))
    social_account_id: Mapped[UUID] = mapped_column(ForeignKey("social_accounts.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32))
    status: Mapped[PublicationStatus] = mapped_column(
        SAEnum(PublicationStatus, native_enum=False, length=32, validate_strings=True)
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_actor_id: Mapped[UUID] = mapped_column(Uuid())
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    publish_now_idempotency_key: Mapped[str | None] = mapped_column(String(128))
    publish_now_request_hash: Mapped[str | None] = mapped_column(String(64))
    publish_now_actor_id: Mapped[UUID | None] = mapped_column(Uuid())
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid())
    scheduled_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("event_outbox.event_id"), unique=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(String(2000))
    blocking_reason: Mapped[str | None] = mapped_column(String(64))
