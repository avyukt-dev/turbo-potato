"""Align Fact Sheet persistence with the canonical snapshot contract.

Revision ID: 0004_fact_sheet_contract
Revises: 0003_ai_claim_provenance
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_fact_sheet_contract"
down_revision: str | None = "0003_ai_claim_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.alter_column("fact_sheets", "claims", new_column_name="claims_snapshot")
    op.alter_column("fact_sheets", "evidence", new_column_name="evidence_snapshot")
    op.alter_column("fact_sheets", "source_snapshot", new_column_name="sources_snapshot")

    op.add_column(
        "fact_sheets",
        sa.Column(
            "fact_checks_snapshot",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "fact_sheets",
        sa.Column(
            "unresolved_questions",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "fact_sheets",
        sa.Column(
            "sensitive_topics",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("fact_sheets", sa.Column("ai_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_fact_sheets_ai_run_id_ai_runs",
        "fact_sheets",
        "ai_runs",
        ["ai_run_id"],
        ["id"],
    )
    op.create_index("ix_fact_sheets_ai_run_id", "fact_sheets", ["ai_run_id"])

    op.execute(
        """
        UPDATE fact_sheets
        SET sources_snapshot = CASE
            WHEN jsonb_typeof(sources_snapshot) = 'array' THEN sources_snapshot
            WHEN sources_snapshot = '{}'::jsonb THEN '[]'::jsonb
            ELSE jsonb_build_array(sources_snapshot)
        END
        """
    )

    op.alter_column("fact_sheets", "fact_checks_snapshot", server_default=None)
    op.alter_column("fact_sheets", "unresolved_questions", server_default=None)
    op.alter_column("fact_sheets", "sensitive_topics", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_fact_sheets_ai_run_id", table_name="fact_sheets")
    op.drop_constraint("fk_fact_sheets_ai_run_id_ai_runs", "fact_sheets", type_="foreignkey")
    op.drop_column("fact_sheets", "ai_run_id")
    op.drop_column("fact_sheets", "sensitive_topics")
    op.drop_column("fact_sheets", "unresolved_questions")
    op.drop_column("fact_sheets", "fact_checks_snapshot")

    op.alter_column("fact_sheets", "sources_snapshot", new_column_name="source_snapshot")
    op.alter_column("fact_sheets", "evidence_snapshot", new_column_name="evidence")
    op.alter_column("fact_sheets", "claims_snapshot", new_column_name="claims")
