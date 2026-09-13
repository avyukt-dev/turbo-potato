"""Add exact-version human review decisions and audit history.

Revision ID: 0009_human_review
Revises: 0008_quality_gate
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_human_review"
down_revision: str | None = "0008_quality_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column(
        "content_quality_checks",
        sa.Column("content_artifact_hash", sa.String(64), nullable=True),
    )
    op.create_check_constraint(
        "ck_quality_content_artifact_hash",
        "content_quality_checks",
        "content_artifact_hash IS NULL OR length(content_artifact_hash) = 64",
    )
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("artifact_type", sa.String(32), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("content_draft_id", sa.Uuid(), nullable=False),
        sa.Column("fact_sheet_id", sa.Uuid(), nullable=False),
        sa.Column("fact_sheet_version", sa.Integer(), nullable=False),
        sa.Column("quality_check_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_hash", sa.String(64), nullable=False),
        sa.Column("artifact_snapshot", JSONB, nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("artifact_type = 'content_variant'", name="ck_review_artifact_type"),
        sa.CheckConstraint("artifact_version >= 1", name="ck_review_artifact_version"),
        sa.CheckConstraint("fact_sheet_version >= 1", name="ck_review_fact_sheet_version"),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED', 'CHANGES_REQUESTED')",
            name="ck_review_terminal_decision",
        ),
        sa.CheckConstraint("length(artifact_hash) = 64", name="ck_review_artifact_hash"),
        sa.CheckConstraint(
            "decision = 'APPROVED' OR length(trim(reason)) > 0",
            name="ck_review_reason_required",
        ),
        sa.ForeignKeyConstraint(["artifact_id"], ["content_variants.id"]),
        sa.ForeignKeyConstraint(["content_draft_id"], ["content_drafts.id"]),
        sa.ForeignKeyConstraint(["fact_sheet_id"], ["fact_sheets.id"]),
        sa.ForeignKeyConstraint(["quality_check_id"], ["content_quality_checks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_type",
            "artifact_id",
            "artifact_version",
            name="uq_review_decisions_artifact_version",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_review_decisions_idempotency_key"),
    )
    for column in (
        "artifact_id",
        "reviewer_id",
        "decided_at",
        "content_draft_id",
        "fact_sheet_id",
        "quality_check_id",
    ):
        op.create_index(f"ix_review_decisions_{column}", "review_decisions", [column])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("artifact_type", sa.String(32), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False),
        sa.Column("review_decision_id", sa.Uuid(), nullable=True),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("metadata", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("artifact_version >= 1", name="ck_audit_artifact_version"),
        sa.CheckConstraint("result IN ('SUCCESS', 'BLOCKED')", name="ck_audit_result"),
        sa.ForeignKeyConstraint(["review_decision_id"], ["review_decisions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "actor_id",
        "action",
        "artifact_id",
        "review_decision_id",
        "request_id",
        "correlation_id",
        "created_at",
    ):
        op.create_index(f"ix_audit_log_{column}", "audit_log", [column])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("review_decisions")
    op.drop_constraint("ck_quality_content_artifact_hash", "content_quality_checks", type_="check")
    op.drop_column("content_quality_checks", "content_artifact_hash")
