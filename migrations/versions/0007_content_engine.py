"""Add Stage-21 content drafts and variants.

Revision ID: 0007_content_engine
Revises: 0006_event_reliability
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_content_engine"
down_revision: str | None = "0006_event_reliability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "content_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("story_id", sa.Uuid(), nullable=False),
        sa.Column("fact_sheet_id", sa.Uuid(), nullable=False),
        sa.Column("fact_sheet_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("methodology_version", sa.String(length=64), nullable=False),
        sa.Column("editorial_brief_snapshot", JSONB, nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("sensitive_topics", JSONB, nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("created_by_ai_run_id", sa.Uuid(), nullable=False),
        sa.Column("semantic_key", sa.String(length=128), nullable=False),
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
        sa.CheckConstraint("version >= 1", name="ck_content_drafts_version"),
        sa.CheckConstraint("fact_sheet_version >= 1", name="ck_content_drafts_fact_sheet_version"),
        sa.CheckConstraint(
            "risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_content_drafts_risk_level",
        ),
        sa.CheckConstraint(
            "review_state IN ('NOT_READY', 'READY_FOR_REVIEW', 'IN_REVIEW', "
            "'APPROVED', 'REJECTED', 'CHANGES_REQUESTED')",
            name="ck_content_drafts_review_state",
        ),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"]),
        sa.ForeignKeyConstraint(["fact_sheet_id"], ["fact_sheets.id"]),
        sa.ForeignKeyConstraint(["created_by_ai_run_id"], ["ai_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("semantic_key", name="uq_content_drafts_semantic_key"),
        sa.UniqueConstraint("story_id", "version", name="uq_content_drafts_story_version"),
    )
    for column in ("story_id", "fact_sheet_id", "created_by_ai_run_id", "semantic_key"):
        op.create_index(f"ix_content_drafts_{column}", "content_drafts", [column])

    op.create_table(
        "content_variants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("content_draft_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("structured_payload", JSONB, nullable=False),
        sa.Column("claim_ids_used", JSONB, nullable=False),
        sa.Column("source_ids_used", JSONB, nullable=False),
        sa.Column("media_asset_ids", JSONB, nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
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
        sa.CheckConstraint("version >= 1", name="ck_content_variants_version"),
        sa.CheckConstraint(
            "review_state IN ('NOT_READY', 'READY_FOR_REVIEW', 'IN_REVIEW', "
            "'APPROVED', 'REJECTED', 'CHANGES_REQUESTED')",
            name="ck_content_variants_review_state",
        ),
        sa.ForeignKeyConstraint(["content_draft_id"], ["content_drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "content_draft_id",
            "platform",
            "format",
            "language",
            "version",
            name="uq_content_variants_draft_target_version",
        ),
    )
    op.create_index(
        "ix_content_variants_content_draft_id", "content_variants", ["content_draft_id"]
    )


def downgrade() -> None:
    op.drop_table("content_variants")
    op.drop_table("content_drafts")
