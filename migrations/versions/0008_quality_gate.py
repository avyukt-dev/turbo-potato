"""Add exact-version Stage-22 quality assessments.

Revision ID: 0008_quality_gate
Revises: 0007_content_engine
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_quality_gate"
down_revision: str | None = "0007_content_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "content_quality_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("content_draft_id", sa.Uuid(), nullable=False),
        sa.Column("content_variant_id", sa.Uuid(), nullable=False),
        sa.Column("content_variant_version", sa.Integer(), nullable=False),
        sa.Column("fact_sheet_id", sa.Uuid(), nullable=False),
        sa.Column("fact_sheet_version", sa.Integer(), nullable=False),
        sa.Column("methodology_version", sa.String(64), nullable=False),
        sa.Column("factual_accuracy_passed", sa.Boolean(), nullable=False),
        sa.Column("source_alignment_passed", sa.Boolean(), nullable=False),
        sa.Column("citation_alignment_passed", sa.Boolean(), nullable=False),
        sa.Column("style_passed", sa.Boolean(), nullable=False),
        sa.Column("unsupported_claims", JSONB, nullable=False),
        sa.Column("fabricated_quotes", JSONB, nullable=False),
        sa.Column("incorrect_names", JSONB, nullable=False),
        sa.Column("incorrect_dates", JSONB, nullable=False),
        sa.Column("incorrect_numbers", JSONB, nullable=False),
        sa.Column("missing_context", JSONB, nullable=False),
        sa.Column("defamation_risk", sa.Boolean(), nullable=False),
        sa.Column("sensitive_topic_error", sa.Boolean(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column("notes", JSONB, nullable=False),
        sa.Column("ai_run_id", sa.Uuid(), nullable=True),
        sa.Column("semantic_key", sa.String(128), nullable=False),
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
        sa.CheckConstraint("content_variant_version >= 1", name="ck_quality_variant_version"),
        sa.CheckConstraint("fact_sheet_version >= 1", name="ck_quality_fact_sheet_version"),
        sa.CheckConstraint("review_required", name="ck_quality_review_required"),
        sa.ForeignKeyConstraint(["content_draft_id"], ["content_drafts.id"]),
        sa.ForeignKeyConstraint(["content_variant_id"], ["content_variants.id"]),
        sa.ForeignKeyConstraint(["fact_sheet_id"], ["fact_sheets.id"]),
        sa.ForeignKeyConstraint(["ai_run_id"], ["ai_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("semantic_key", name="uq_content_quality_checks_semantic_key"),
    )
    for column in (
        "content_draft_id",
        "content_variant_id",
        "fact_sheet_id",
        "ai_run_id",
        "semantic_key",
    ):
        op.create_index(f"ix_content_quality_checks_{column}", "content_quality_checks", [column])


def downgrade() -> None:
    op.drop_table("content_quality_checks")
