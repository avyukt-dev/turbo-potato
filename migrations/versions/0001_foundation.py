"""Initial durable schema for collection, evidence, jobs, and event delivery.

Revision ID: 0001_foundation
Revises: None
Create Date: 2026-09-10

This revision is intentionally explicit. It does not import mutable ORM model metadata and does not
call Base.metadata.create_all(). Future model changes require new revisions.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("authority_level", sa.Integer(), nullable=True),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source_metadata", JSONB, nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_sources"),
    )

    op.create_table(
        "source_feeds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("feed_url", sa.Text(), nullable=True),
        sa.Column("feed_type", sa.String(length=32), nullable=False),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("configuration", JSONB, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="fk_source_feeds_source_id_sources"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_feeds"),
    )
    op.create_index("ix_source_feeds_source_id", "source_feeds", ["source_id"])

    op.create_table(
        "articles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="fk_articles_source_id_sources"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_articles"),
        sa.UniqueConstraint(
            "source_id", "canonical_url", name="uq_articles_source_id_canonical_url"
        ),
    )
    op.create_index("ix_articles_published_at", "articles", ["published_at"])
    op.create_index("ix_articles_source_id", "articles", ["source_id"])

    op.create_table(
        "article_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version_metadata", JSONB, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"], name="fk_article_versions_article_id_articles"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_article_versions"),
        sa.UniqueConstraint(
            "article_id", "version_number", name="uq_article_versions_article_id_version_number"
        ),
    )
    op.create_index("ix_article_versions_article_id", "article_versions", ["article_id"])

    op.create_table(
        "stories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("canonical_headline", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("importance_score", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("evidence_strength", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("controversy_score", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("confidence_score", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("cluster_key", sa.String(length=255), nullable=True),
        sa.Column("story_metadata", JSONB, nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_stories"),
    )
    op.create_index("ix_stories_cluster_key", "stories", ["cluster_key"])

    op.create_table(
        "story_sources",
        sa.Column("story_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("relationship_type", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"], name="fk_story_sources_article_id_articles"
        ),
        sa.ForeignKeyConstraint(
            ["story_id"], ["stories.id"], name="fk_story_sources_story_id_stories"
        ),
        sa.PrimaryKeyConstraint("story_id", "article_id", name="pk_story_sources"),
    )

    op.create_table(
        "claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("story_id", sa.Uuid(), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("normalized_claim", sa.Text(), nullable=True),
        sa.Column("claim_type", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("confidence_score", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("importance_score", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("temporal_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("temporal_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_metadata", JSONB, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], name="fk_claims_story_id_stories"),
        sa.PrimaryKeyConstraint("id", name="pk_claims"),
    )
    op.create_index("ix_claims_story_id", "claims", ["story_id"])

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("evidence_type", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("evidence_metadata", JSONB, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="fk_evidence_items_source_id_sources"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_items"),
    )
    op.create_index("ix_evidence_items_source_id", "evidence_items", ["source_id"])

    op.create_table(
        "claim_evidence",
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("strength_score", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"], name="fk_claim_evidence_claim_id_claims"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_items.id"],
            name="fk_claim_evidence_evidence_id_evidence_items",
        ),
        sa.PrimaryKeyConstraint("claim_id", "evidence_id", name="pk_claim_evidence"),
    )

    op.create_table(
        "fact_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("story_id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=True),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("confidence_score", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("reasoning_summary", sa.Text(), nullable=True),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"], name="fk_fact_checks_claim_id_claims"),
        sa.ForeignKeyConstraint(
            ["story_id"], ["stories.id"], name="fk_fact_checks_story_id_stories"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_fact_checks"),
    )
    op.create_index("ix_fact_checks_claim_id", "fact_checks", ["claim_id"])
    op.create_index("ix_fact_checks_story_id", "fact_checks", ["story_id"])

    op.create_table(
        "fact_sheets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("story_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("claims", JSONB, nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("timeline", JSONB, nullable=False),
        sa.Column("entities", JSONB, nullable=False),
        sa.Column("locations", JSONB, nullable=False),
        sa.Column("context", JSONB, nullable=False),
        sa.Column("counterclaims", JSONB, nullable=False),
        sa.Column("confidence_score", sa.Numeric(precision=6, scale=5), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("source_snapshot", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["story_id"], ["stories.id"], name="fk_fact_sheets_story_id_stories"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_fact_sheets"),
        sa.UniqueConstraint("story_id", "version", name="uq_fact_sheets_story_id_version"),
    )
    op.create_index("ix_fact_sheets_story_id", "fact_sheets", ["story_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
    )
    op.create_index("ix_jobs_job_type", "jobs", ["job_type"])
    op.create_index("ix_jobs_status", "jobs", ["status"])

    op.create_table(
        "job_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name="fk_job_attempts_job_id_jobs"),
        sa.PrimaryKeyConstraint("id", name="pk_job_attempts"),
        sa.UniqueConstraint(
            "job_id", "attempt_number", name="uq_job_attempts_job_id_attempt_number"
        ),
    )
    op.create_index("ix_job_attempts_job_id", "job_attempts", ["job_id"])

    op.create_table(
        "event_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("producer", sa.String(length=64), nullable=False),
        sa.Column("producer_version", sa.String(length=64), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("publishing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_event_outbox"),
        sa.UniqueConstraint("event_id", name="uq_event_outbox_event_id"),
    )
    op.create_index("ix_event_outbox_aggregate_id", "event_outbox", ["aggregate_id"])
    op.create_index("ix_event_outbox_correlation_id", "event_outbox", ["correlation_id"])
    op.create_index("ix_event_outbox_event_id", "event_outbox", ["event_id"])
    op.create_index("ix_event_outbox_event_type", "event_outbox", ["event_type"])
    op.create_index("ix_event_outbox_idempotency_key", "event_outbox", ["idempotency_key"])
    op.create_index("ix_event_outbox_next_attempt_at", "event_outbox", ["next_attempt_at"])
    op.create_index("ix_event_outbox_status", "event_outbox", ["status"])
    op.create_index(
        "ix_event_outbox_dispatch_ready",
        "event_outbox",
        ["status", "next_attempt_at", "created_at"],
    )

    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("consumer_group", sa.String(length=128), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("result", JSONB, nullable=True),
        sa.PrimaryKeyConstraint("event_id", "consumer_group", name="pk_processed_events"),
    )
    op.create_index("ix_processed_events_processed_at", "processed_events", ["processed_at"])


def downgrade() -> None:
    op.drop_index("ix_processed_events_processed_at", table_name="processed_events")
    op.drop_table("processed_events")

    op.drop_index("ix_event_outbox_dispatch_ready", table_name="event_outbox")
    op.drop_index("ix_event_outbox_status", table_name="event_outbox")
    op.drop_index("ix_event_outbox_next_attempt_at", table_name="event_outbox")
    op.drop_index("ix_event_outbox_idempotency_key", table_name="event_outbox")
    op.drop_index("ix_event_outbox_event_type", table_name="event_outbox")
    op.drop_index("ix_event_outbox_event_id", table_name="event_outbox")
    op.drop_index("ix_event_outbox_correlation_id", table_name="event_outbox")
    op.drop_index("ix_event_outbox_aggregate_id", table_name="event_outbox")
    op.drop_table("event_outbox")

    op.drop_index("ix_job_attempts_job_id", table_name="job_attempts")
    op.drop_table("job_attempts")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_job_type", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_fact_sheets_story_id", table_name="fact_sheets")
    op.drop_table("fact_sheets")
    op.drop_index("ix_fact_checks_story_id", table_name="fact_checks")
    op.drop_index("ix_fact_checks_claim_id", table_name="fact_checks")
    op.drop_table("fact_checks")
    op.drop_table("claim_evidence")
    op.drop_index("ix_evidence_items_source_id", table_name="evidence_items")
    op.drop_table("evidence_items")
    op.drop_index("ix_claims_story_id", table_name="claims")
    op.drop_table("claims")
    op.drop_table("story_sources")
    op.drop_index("ix_stories_cluster_key", table_name="stories")
    op.drop_table("stories")
    op.drop_index("ix_article_versions_article_id", table_name="article_versions")
    op.drop_table("article_versions")
    op.drop_index("ix_articles_source_id", table_name="articles")
    op.drop_index("ix_articles_published_at", table_name="articles")
    op.drop_table("articles")
    op.drop_index("ix_source_feeds_source_id", table_name="source_feeds")
    op.drop_table("source_feeds")
    op.drop_table("sources")
