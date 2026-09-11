"""Add research currency, raw discovery handoff, and durable event failures.

Revision ID: 0006_event_reliability
Revises: 0005_canonical_remediation
Create Date: 2026-09-11

Historical Claims and FactChecks cannot be assigned trustworthy research provenance. Claims start
at generation zero with no current run/check, and historical FactChecks retain NULL provenance.
Only newly accepted research operations establish current generation state.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_event_reliability"
down_revision: str | None = "0005_canonical_remediation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "article_discoveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("raw_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("normalized_article_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="fk_article_discoveries_article_id_articles",
        ),
        sa.ForeignKeyConstraint(
            ["normalized_article_version_id"],
            ["article_versions.id"],
            name="fk_article_discoveries_normalized_version",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_article_discoveries"),
        sa.UniqueConstraint("event_id", name="uq_article_discoveries_event_id"),
        sa.UniqueConstraint(
            "article_id",
            "raw_hash",
            name="uq_article_discoveries_article_id_raw_hash",
        ),
    )
    op.create_index("ix_article_discoveries_article_id", "article_discoveries", ["article_id"])
    op.create_index("ix_article_discoveries_event_id", "article_discoveries", ["event_id"])
    op.create_index(
        "ix_article_discoveries_normalized_article_version_id",
        "article_discoveries",
        ["normalized_article_version_id"],
    )

    op.add_column(
        "claims",
        sa.Column("research_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("claims", sa.Column("current_research_run_id", sa.Uuid()))
    op.add_column("claims", sa.Column("current_fact_check_id", sa.Uuid()))
    op.alter_column("claims", "research_generation", server_default=None)
    op.create_check_constraint(
        "ck_claims_research_generation", "claims", "research_generation >= 0"
    )
    op.create_foreign_key(
        "fk_claims_current_research_run_id_jobs",
        "claims",
        "jobs",
        ["current_research_run_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_claims_current_fact_check_id_fact_checks",
        "claims",
        "fact_checks",
        ["current_fact_check_id"],
        ["id"],
    )
    op.create_index("ix_claims_current_research_run_id", "claims", ["current_research_run_id"])
    op.create_index("ix_claims_current_fact_check_id", "claims", ["current_fact_check_id"])

    op.add_column("fact_checks", sa.Column("research_run_id", sa.Uuid()))
    op.add_column("fact_checks", sa.Column("research_generation", sa.Integer()))
    op.add_column("fact_checks", sa.Column("methodology_version", sa.String(length=64)))
    op.create_foreign_key(
        "fk_fact_checks_research_run_id_jobs",
        "fact_checks",
        "jobs",
        ["research_run_id"],
        ["id"],
    )
    op.create_index("ix_fact_checks_research_run_id", "fact_checks", ["research_run_id"])
    op.create_check_constraint(
        "ck_fact_checks_research_provenance",
        "fact_checks",
        "(research_run_id IS NULL AND research_generation IS NULL AND "
        "methodology_version IS NULL) OR "
        "(research_run_id IS NOT NULL AND research_generation >= 1 AND "
        "methodology_version IS NOT NULL)",
    )

    op.create_table(
        "research_run_claims",
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("research_generation", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("research_generation >= 1", name="ck_research_run_claims_generation"),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"], name="fk_research_run_claims_claim_id_claims"
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["jobs.id"],
            name="fk_research_run_claims_research_run_id_jobs",
        ),
        sa.PrimaryKeyConstraint("research_run_id", "claim_id", name="pk_research_run_claims"),
        sa.UniqueConstraint(
            "claim_id",
            "research_generation",
            name="uq_research_run_claims_claim_generation",
        ),
    )

    op.create_table(
        "event_processing_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("delivery_key", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=True),
        sa.Column("consumer_group", sa.String(length=128), nullable=False),
        sa.Column("source_stream", sa.String(length=128), nullable=False),
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("failure_class", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_event_processing_attempts_number"),
        sa.CheckConstraint(
            "failure_class IN ('TRANSIENT', 'PERMANENT', 'STALE', 'EXHAUSTED')",
            name="ck_event_processing_attempts_failure_class",
        ),
        sa.CheckConstraint(
            "status IN ('RETRY_PENDING', 'DEAD_LETTERED', 'STALE')",
            name="ck_event_processing_attempts_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_event_processing_attempts"),
        sa.UniqueConstraint(
            "delivery_key",
            "attempt_number",
            name="uq_event_processing_attempts_delivery_attempt",
        ),
    )
    for column in (
        "delivery_key",
        "event_id",
        "event_type",
        "consumer_group",
        "next_retry_at",
    ):
        op.create_index(
            f"ix_event_processing_attempts_{column}",
            "event_processing_attempts",
            [column],
        )

    op.create_table(
        "event_dead_letters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("delivery_key", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=True),
        sa.Column("aggregate_type", sa.String(length=64), nullable=True),
        sa.Column("aggregate_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("consumer_group", sa.String(length=128), nullable=False),
        sa.Column("source_stream", sa.String(length=128), nullable=False),
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("failure_class", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("raw_event", sa.Text(), nullable=True),
        sa.Column("event_payload", JSONB, nullable=True),
        sa.Column(
            "failed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("attempt_count >= 1", name="ck_event_dead_letters_attempt_count"),
        sa.CheckConstraint(
            "failure_class IN ('PERMANENT', 'EXHAUSTED')",
            name="ck_event_dead_letters_failure_class",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name="fk_event_dead_letters_job_id_jobs"),
        sa.PrimaryKeyConstraint("id", name="pk_event_dead_letters"),
        sa.UniqueConstraint("delivery_key", name="uq_event_dead_letters_delivery_key"),
    )
    for column in (
        "delivery_key",
        "event_id",
        "event_type",
        "aggregate_id",
        "job_id",
        "consumer_group",
        "failed_at",
    ):
        op.create_index(f"ix_event_dead_letters_{column}", "event_dead_letters", [column])


def downgrade() -> None:
    op.drop_table("event_dead_letters")
    op.drop_table("event_processing_attempts")
    op.drop_table("research_run_claims")

    op.drop_constraint("ck_fact_checks_research_provenance", "fact_checks", type_="check")
    op.drop_index("ix_fact_checks_research_run_id", table_name="fact_checks")
    op.drop_constraint("fk_fact_checks_research_run_id_jobs", "fact_checks", type_="foreignkey")
    op.drop_column("fact_checks", "methodology_version")
    op.drop_column("fact_checks", "research_generation")
    op.drop_column("fact_checks", "research_run_id")

    op.drop_index("ix_claims_current_fact_check_id", table_name="claims")
    op.drop_index("ix_claims_current_research_run_id", table_name="claims")
    op.drop_constraint("fk_claims_current_fact_check_id_fact_checks", "claims", type_="foreignkey")
    op.drop_constraint("fk_claims_current_research_run_id_jobs", "claims", type_="foreignkey")
    op.drop_constraint("ck_claims_research_generation", "claims", type_="check")
    op.drop_column("claims", "current_fact_check_id")
    op.drop_column("claims", "current_research_run_id")
    op.drop_column("claims", "research_generation")

    op.drop_table("article_discoveries")
