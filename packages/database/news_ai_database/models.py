"""Foundational SQLAlchemy models for collection, evidence, jobs, AI, and event delivery."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from news_ai_domain import ClaimVerificationStatus, FactCheckLabel, ReviewState, RiskLevel
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class EventFailureClass(StrEnum):
    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    STALE = "STALE"
    EXHAUSTED = "EXHAUSTED"


class EventAttemptStatus(StrEnum):
    RETRY_PENDING = "RETRY_PENDING"
    DEAD_LETTERED = "DEAD_LETTERED"
    STALE = "STALE"


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
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "canonical_url",
            name="uq_articles_source_id_canonical_url",
        ),
    )

    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255))
    language: Mapped[str | None] = mapped_column(String(32))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ArticleVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "article_versions"
    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "version_number",
            name="uq_article_versions_article_id_version_number",
        ),
    )

    article_id: Mapped[UUID] = mapped_column(ForeignKey("articles.id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)


class ArticleDiscovery(UUIDPrimaryKeyMixin, Base):
    """Durable raw handoff from collection to asynchronous normalization."""

    __tablename__ = "article_discoveries"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_article_discoveries_event_id"),
        UniqueConstraint(
            "article_id",
            "raw_hash",
            name="uq_article_discoveries_article_id_raw_hash",
        ),
    )

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    article_id: Mapped[UUID] = mapped_column(ForeignKey("articles.id"), nullable=False, index=True)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    normalized_article_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "article_versions.id",
            name="fk_article_discoveries_normalized_version",
        ),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Story(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stories"
    __table_args__ = (
        CheckConstraint(
            "risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_stories_risk_level",
        ),
    )

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
        SAEnum(RiskLevel, native_enum=False, length=16, validate_strings=True),
        nullable=False,
        default=RiskLevel.LOW,
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    cluster_key: Mapped[str | None] = mapped_column(String(255), index=True)
    story_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    verification_semantic_key: Mapped[str | None] = mapped_column(String(128), index=True)


class StorySource(Base):
    __tablename__ = "story_sources"

    story_id: Mapped[UUID] = mapped_column(ForeignKey("stories.id"), primary_key=True)
    article_id: Mapped[UUID] = mapped_column(ForeignKey("articles.id"), primary_key=True)
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_models"
    __table_args__ = (
        UniqueConstraint("provider", "model_name", name="uq_ai_models_provider_model_name"),
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    locality: Mapped[str] = mapped_column(String(16), nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    model_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)


class AIRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_runs"

    ai_model_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_models.id"), nullable=False, index=True
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    prompt_id: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    prompt_checksum: Mapped[str | None] = mapped_column(String(128))
    input_artifact_ids: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list)
    input_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    routing_attempts: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)


class Claim(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(
            "status IN ('UNASSESSED', 'SUPPORTED', 'PARTIALLY_SUPPORTED', "
            "'DISPUTED', 'UNVERIFIED', 'REFUTED')",
            name="ck_claims_status",
        ),
        CheckConstraint(
            "risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_claims_risk_level",
        ),
        CheckConstraint("research_generation >= 0", name="ck_claims_research_generation"),
    )

    story_id: Mapped[UUID] = mapped_column(ForeignKey("stories.id"), nullable=False, index=True)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_claim: Mapped[str | None] = mapped_column(Text)
    claim_type: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ClaimVerificationStatus] = mapped_column(
        SAEnum(
            ClaimVerificationStatus,
            native_enum=False,
            length=32,
            validate_strings=True,
        ),
        nullable=False,
        default=ClaimVerificationStatus.UNASSESSED,
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    importance_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    risk_level: Mapped[RiskLevel] = mapped_column(
        SAEnum(RiskLevel, native_enum=False, length=16, validate_strings=True),
        nullable=False,
        default=RiskLevel.LOW,
    )
    temporal_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    temporal_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_ai_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_runs.id"), nullable=True, index=True
    )
    research_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_research_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id"), nullable=True, index=True
    )
    current_fact_check_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("fact_checks.id", use_alter=True), nullable=True, index=True
    )
    claim_metadata: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)


class ResearchRunClaim(Base):
    """Generation assigned to one Claim within a potentially multi-Claim research run."""

    __tablename__ = "research_run_claims"
    __table_args__ = (
        UniqueConstraint(
            "claim_id",
            "research_generation",
            name="uq_research_run_claims_claim_generation",
        ),
        CheckConstraint(
            "research_generation >= 1",
            name="ck_research_run_claims_generation",
        ),
    )

    research_run_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    claim_id: Mapped[UUID] = mapped_column(ForeignKey("claims.id"), primary_key=True)
    research_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    __table_args__ = (
        CheckConstraint(
            "label IN ('TRUE', 'MOSTLY_TRUE', 'PARTIALLY_TRUE', 'MISLEADING', "
            "'OUT_OF_CONTEXT', 'UNVERIFIED', 'FALSE', 'FABRICATED', 'SATIRE')",
            name="ck_fact_checks_label",
        ),
        CheckConstraint(
            "review_state IN ('NOT_READY', 'READY_FOR_REVIEW', 'IN_REVIEW', "
            "'APPROVED', 'REJECTED', 'CHANGES_REQUESTED')",
            name="ck_fact_checks_review_state",
        ),
        CheckConstraint(
            "primary_evidence_count >= 0",
            name="ck_fact_checks_primary_evidence_count_nonnegative",
        ),
        CheckConstraint(
            "supporting_count >= 0",
            name="ck_fact_checks_supporting_count_nonnegative",
        ),
        CheckConstraint(
            "contradicting_count >= 0",
            name="ck_fact_checks_contradicting_count_nonnegative",
        ),
        CheckConstraint(
            "(research_run_id IS NULL AND research_generation IS NULL AND "
            "methodology_version IS NULL) OR "
            "(research_run_id IS NOT NULL AND research_generation >= 1 AND "
            "methodology_version IS NOT NULL)",
            name="ck_fact_checks_research_provenance",
        ),
        UniqueConstraint("semantic_key", name="uq_fact_checks_semantic_key"),
    )

    story_id: Mapped[UUID] = mapped_column(ForeignKey("stories.id"), nullable=False, index=True)
    claim_id: Mapped[UUID | None] = mapped_column(ForeignKey("claims.id"), index=True)
    label: Mapped[FactCheckLabel] = mapped_column(
        SAEnum(FactCheckLabel, native_enum=False, length=32, validate_strings=True),
        nullable=False,
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    summary: Mapped[str | None] = mapped_column(Text)
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    primary_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supporting_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contradicting_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    review_state: Mapped[ReviewState] = mapped_column(
        SAEnum(ReviewState, native_enum=False, length=32, validate_strings=True),
        nullable=False,
        default=ReviewState.NOT_READY,
    )
    ai_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_runs.id"), nullable=True, index=True
    )
    research_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id"), nullable=True, index=True
    )
    research_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    methodology_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    semantic_key: Mapped[str | None] = mapped_column(String(128), index=True)


class FactSheet(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "fact_sheets"
    __table_args__ = (
        UniqueConstraint(
            "story_id",
            "version",
            name="uq_fact_sheets_story_id_version",
        ),
        UniqueConstraint("semantic_key", name="uq_fact_sheets_semantic_key"),
        CheckConstraint(
            "risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_fact_sheets_risk_level",
        ),
    )

    story_id: Mapped[UUID] = mapped_column(ForeignKey("stories.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    claims_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    fact_checks_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    evidence_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    sources_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    entities: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    locations: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list)
    context: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list)
    counterclaims: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    unresolved_questions: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    risk_level: Mapped[RiskLevel] = mapped_column(
        SAEnum(RiskLevel, native_enum=False, length=16, validate_strings=True),
        nullable=False,
    )
    sensitive_topics: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list)
    ai_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_runs.id"), nullable=True, index=True
    )
    semantic_key: Mapped[str | None] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("semantic_key", name="uq_jobs_semantic_key"),)

    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    semantic_key: Mapped[str | None] = mapped_column(String(128), index=True)


class JobAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "attempt_number",
            name="uq_job_attempts_job_id_attempt_number",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


class EventOutbox(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "event_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_event_outbox_event_id"),
        CheckConstraint(
            "status IN ('PENDING', 'PUBLISHING', 'PUBLISHED', 'FAILED')",
            name="ck_event_outbox_status",
        ),
        Index(
            "ix_event_outbox_dispatch_ready",
            "status",
            "next_attempt_at",
            "created_at",
        ),
    )

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
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
        SAEnum(OutboxStatus, native_enum=False, length=16, validate_strings=True),
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


class EventProcessingAttempt(UUIDPrimaryKeyMixin, Base):
    """One durable failed processing attempt for a stream delivery."""

    __tablename__ = "event_processing_attempts"
    __table_args__ = (
        UniqueConstraint(
            "delivery_key",
            "attempt_number",
            name="uq_event_processing_attempts_delivery_attempt",
        ),
        CheckConstraint("attempt_number >= 1", name="ck_event_processing_attempts_number"),
        CheckConstraint(
            "failure_class IN ('TRANSIENT', 'PERMANENT', 'STALE', 'EXHAUSTED')",
            name="ck_event_processing_attempts_failure_class",
        ),
        CheckConstraint(
            "status IN ('RETRY_PENDING', 'DEAD_LETTERED', 'STALE')",
            name="ck_event_processing_attempts_status",
        ),
    )

    delivery_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    event_type: Mapped[str | None] = mapped_column(String(128), index=True)
    consumer_group: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_stream: Mapped[str] = mapped_column(String(128), nullable=False)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_class: Mapped[EventFailureClass] = mapped_column(
        SAEnum(EventFailureClass, native_enum=False, length=16, validate_strings=True),
        nullable=False,
    )
    status: Mapped[EventAttemptStatus] = mapped_column(
        SAEnum(EventAttemptStatus, native_enum=False, length=24, validate_strings=True),
        nullable=False,
    )
    error_code: Mapped[str] = mapped_column(String(128), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventDeadLetter(UUIDPrimaryKeyMixin, Base):
    """Idempotent durable dead-letter record created before source-message ACK."""

    __tablename__ = "event_dead_letters"
    __table_args__ = (
        UniqueConstraint("delivery_key", name="uq_event_dead_letters_delivery_key"),
        CheckConstraint("attempt_count >= 1", name="ck_event_dead_letters_attempt_count"),
        CheckConstraint(
            "failure_class IN ('PERMANENT', 'EXHAUSTED')",
            name="ck_event_dead_letters_failure_class",
        ),
        CheckConstraint(
            "length(raw_event_hash) = 64",
            name="ck_event_dead_letters_raw_event_hash",
        ),
    )

    delivery_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    event_type: Mapped[str | None] = mapped_column(String(128), index=True)
    aggregate_type: Mapped[str | None] = mapped_column(String(64))
    aggregate_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    consumer_group: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_stream: Mapped[str] = mapped_column(String(128), nullable=False)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_class: Mapped[EventFailureClass] = mapped_column(
        SAEnum(EventFailureClass, native_enum=False, length=16, validate_strings=True),
        nullable=False,
    )
    error_code: Mapped[str] = mapped_column(String(128), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    raw_event: Mapped[str | None] = mapped_column(Text)
    raw_event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
