"""Foundational SQLAlchemy models for collection, evidence, jobs, and event delivery."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from news_ai_domain import ClaimVerificationStatus, FactCheckLabel, ReviewState, RiskLevel

from .base import Base

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class UUIDPrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Source(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sources"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255))
    base_url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    authority_level: Mapped[int | None] = mapped_column(Integer)
    country: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)


class SourceFeed(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "source_feeds"

    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    feed_url: Mapped[str | None] = mapped_column(Text)
    feed_type: Mapped[str] = mapped_column(String(32), nullable=False)
    poll_interval_seconds: Mapped[int | None] = mapped_column(Integer)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)


class Article(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "articles"
    __table_args__ = (UniqueConstraint("source_id", "canonical_url"),)

    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255))
    language: Mapped[str | None] = mapped_column(String(32))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ArticleVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "article_versions"
    __table_args__ = (UniqueConstraint("article_id", "version_number"),)

    article_id: Mapped[UUID] = mapped_column(ForeignKey("articles.id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)


class Story(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stories"

    canonical_headline: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="DISCOVERED")
    language: Mapped[str | None] = mapped_column(String(32))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    importance_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    evidence_strength: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    controversy_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    risk_level: Mapped[RiskLevel] = mapped_column(
        SAEnum(RiskLevel, native_enum=False, length=16), nullable=False, default=RiskLevel.LOW
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    cluster_key: Mapped[str | None] = mapped_column(String(255), index=True)
    story_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)


class StorySource(Base):
    __tablename__ = "story_sources"

    story_id: Mapped[UUID] = mapped_column(ForeignKey("stories.id"), primary_key=True)
    article_id: Mapped[UUID] = mapped_column(ForeignKey("articles.id"), primary_key=True)
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Claim(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "claims"

    story_id: Mapped[UUID] = mapped_column(ForeignKey("stories.id"), nullable=False, index=True)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_claim: Mapped[str | None] = mapped_column(Text)
    claim_type: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ClaimVerificationStatus] = mapped_column(
        SAEnum(ClaimVerificationStatus, native_enum=False, length=32),
        nullable=False,
        default=ClaimVerificationStatus.UNASSESSED,
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    importance_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    risk_level: Mapped[RiskLevel] = mapped_column(
        SAEnum(RiskLevel, native_enum=False, length=16), nullable=False, default=RiskLevel.LOW
    )
    temporal_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    temporal_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)


class EvidenceItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evidence_items"

    source_id: Mapped[UUID | None] = mapped_column(ForeignKey("sources.id"), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(128))
    excerpt: Mapped[str | None] = mapped_column(Text)
    evidence_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"

    claim_id: Mapped[UUID] = mapped_column(ForeignKey("claims.id"), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("evidence_items.id"), primary_key=True)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    strength_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FactCheck(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "fact_checks"

    story_id: Mapped[UUID] = mapped_column(ForeignKey("stories.id"), nullable=False, index=True)
    claim_id: Mapped[UUID | None] = mapped_column(ForeignKey("claims.id"), index=True)
    label: Mapped[FactCheckLabel] = mapped_column(
        SAEnum(FactCheckLabel, native_enum=False, length=32), nullable=False
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    summary: Mapped[str | None] = mapped_column(Text)
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    review_state: Mapped[ReviewState] = mapped_column(
        SAEnum(ReviewState, native_enum=False, length=32),
        nullable=False,
        default=ReviewState.NOT_READY,
    )


class FactSheet(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "fact_sheets"
    __table_args__ = (UniqueConstraint("story_id", "version"),)

    story_id: Mapped[UUID] = mapped_column(ForeignKey("stories.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    claims: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    entities: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    locations: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list)
    context: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list)
    counterclaims: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    risk_level: Mapped[RiskLevel] = mapped_column(
        SAEnum(RiskLevel, native_enum=False, length=16), nullable=False
    )
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "jobs"

    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


class JobAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "job_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_number"),)

    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


class EventOutbox(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "event_outbox"

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    producer: Mapped[str] = mapped_column(String(64), nullable=False)
    producer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    correlation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    causation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    status: Mapped[OutboxStatus] = mapped_column(
        SAEnum(OutboxStatus, native_enum=False, length=16),
        nullable=False,
        default=OutboxStatus.PENDING,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    publishing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ProcessedEvent(Base):
    """Durable per-consumer event completion marker.

    Redis delivery is at-least-once. Consumers may use this table in addition to domain-level
    idempotency to recognize already-completed event handling.
    """

    __tablename__ = "processed_events"

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    consumer_group: Mapped[str] = mapped_column(String(128), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
