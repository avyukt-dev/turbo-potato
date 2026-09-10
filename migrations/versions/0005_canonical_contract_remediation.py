"""Restore canonical verification persistence and database enum integrity.

Revision ID: 0005_canonical_remediation
Revises: 0004_fact_sheet_contract
Create Date: 2026-09-10

Existing FactChecks do not identify the exact evidence set evaluated for that historical result,
so their relationship counts are conservatively initialized to zero instead of attributing all
current claim_evidence rows. New FactChecks persist counts from their exact evaluated set. Existing
records intentionally receive no fabricated AI run.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_canonical_remediation"
down_revision: str | None = "0004_fact_sheet_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLAIM_STATUSES = (
    "UNASSESSED",
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "DISPUTED",
    "UNVERIFIED",
    "REFUTED",
)
FACT_CHECK_LABELS = (
    "TRUE",
    "MOSTLY_TRUE",
    "PARTIALLY_TRUE",
    "MISLEADING",
    "OUT_OF_CONTEXT",
    "UNVERIFIED",
    "FALSE",
    "FABRICATED",
    "SATIRE",
)
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
REVIEW_STATES = (
    "NOT_READY",
    "READY_FOR_REVIEW",
    "IN_REVIEW",
    "APPROVED",
    "REJECTED",
    "CHANGES_REQUESTED",
)
OUTBOX_STATUSES = ("PENDING", "PUBLISHING", "PUBLISHED", "FAILED")


def _audit_enum_values(table_name: str, column_name: str, allowed: tuple[str, ...]) -> None:
    """Fail before constraint creation when historical data uses a noncanonical value."""

    bind = op.get_bind()
    table = sa.table(table_name, sa.column(column_name, sa.String()))
    column = table.c[column_name]
    invalid = tuple(
        bind.execute(
            sa.select(column).distinct().where(column.is_not(None), column.not_in(allowed))
        ).scalars()
    )
    if invalid:
        values = ", ".join(sorted(str(value) for value in invalid))
        raise RuntimeError(f"{table_name}.{column_name} contains noncanonical values: {values}")


def upgrade() -> None:
    _audit_enum_values("stories", "risk_level", RISK_LEVELS)
    _audit_enum_values("claims", "status", CLAIM_STATUSES)
    _audit_enum_values("claims", "risk_level", RISK_LEVELS)
    _audit_enum_values("fact_checks", "label", FACT_CHECK_LABELS)
    _audit_enum_values("fact_checks", "review_state", REVIEW_STATES)
    _audit_enum_values("fact_sheets", "risk_level", RISK_LEVELS)
    _audit_enum_values("event_outbox", "status", OUTBOX_STATUSES)

    op.add_column("stories", sa.Column("verification_semantic_key", sa.String(128)))
    op.create_index(
        "ix_stories_verification_semantic_key",
        "stories",
        ["verification_semantic_key"],
    )

    op.add_column(
        "fact_checks",
        sa.Column(
            "primary_evidence_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "fact_checks",
        sa.Column("supporting_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "fact_checks",
        sa.Column("contradicting_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("fact_checks", sa.Column("ai_run_id", sa.Uuid(), nullable=True))
    op.add_column("fact_checks", sa.Column("semantic_key", sa.String(128), nullable=True))

    op.alter_column("fact_checks", "primary_evidence_count", server_default=None)
    op.alter_column("fact_checks", "supporting_count", server_default=None)
    op.alter_column("fact_checks", "contradicting_count", server_default=None)
    op.create_foreign_key(
        "fk_fact_checks_ai_run_id_ai_runs",
        "fact_checks",
        "ai_runs",
        ["ai_run_id"],
        ["id"],
    )
    op.create_index("ix_fact_checks_ai_run_id", "fact_checks", ["ai_run_id"])
    op.create_index("ix_fact_checks_semantic_key", "fact_checks", ["semantic_key"])
    op.create_unique_constraint(
        "uq_fact_checks_semantic_key",
        "fact_checks",
        ["semantic_key"],
    )

    op.add_column("fact_sheets", sa.Column("semantic_key", sa.String(128), nullable=True))
    op.create_index("ix_fact_sheets_semantic_key", "fact_sheets", ["semantic_key"])
    op.create_unique_constraint(
        "uq_fact_sheets_semantic_key",
        "fact_sheets",
        ["semantic_key"],
    )

    op.add_column("jobs", sa.Column("semantic_key", sa.String(128), nullable=True))
    op.create_index("ix_jobs_semantic_key", "jobs", ["semantic_key"])
    op.create_unique_constraint("uq_jobs_semantic_key", "jobs", ["semantic_key"])

    op.create_check_constraint(
        "ck_stories_risk_level",
        "stories",
        "risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
    )
    op.create_check_constraint(
        "ck_claims_status",
        "claims",
        "status IN ('UNASSESSED', 'SUPPORTED', 'PARTIALLY_SUPPORTED', "
        "'DISPUTED', 'UNVERIFIED', 'REFUTED')",
    )
    op.create_check_constraint(
        "ck_claims_risk_level",
        "claims",
        "risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
    )
    op.create_check_constraint(
        "ck_fact_checks_label",
        "fact_checks",
        "label IN ('TRUE', 'MOSTLY_TRUE', 'PARTIALLY_TRUE', 'MISLEADING', "
        "'OUT_OF_CONTEXT', 'UNVERIFIED', 'FALSE', 'FABRICATED', 'SATIRE')",
    )
    op.create_check_constraint(
        "ck_fact_checks_review_state",
        "fact_checks",
        "review_state IN ('NOT_READY', 'READY_FOR_REVIEW', 'IN_REVIEW', "
        "'APPROVED', 'REJECTED', 'CHANGES_REQUESTED')",
    )
    op.create_check_constraint(
        "ck_fact_checks_primary_evidence_count_nonnegative",
        "fact_checks",
        "primary_evidence_count >= 0",
    )
    op.create_check_constraint(
        "ck_fact_checks_supporting_count_nonnegative",
        "fact_checks",
        "supporting_count >= 0",
    )
    op.create_check_constraint(
        "ck_fact_checks_contradicting_count_nonnegative",
        "fact_checks",
        "contradicting_count >= 0",
    )
    op.create_check_constraint(
        "ck_fact_sheets_risk_level",
        "fact_sheets",
        "risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
    )
    op.create_check_constraint(
        "ck_event_outbox_status",
        "event_outbox",
        "status IN ('PENDING', 'PUBLISHING', 'PUBLISHED', 'FAILED')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_event_outbox_status", "event_outbox", type_="check")
    op.drop_constraint("ck_fact_sheets_risk_level", "fact_sheets", type_="check")
    op.drop_constraint(
        "ck_fact_checks_contradicting_count_nonnegative",
        "fact_checks",
        type_="check",
    )
    op.drop_constraint(
        "ck_fact_checks_supporting_count_nonnegative",
        "fact_checks",
        type_="check",
    )
    op.drop_constraint(
        "ck_fact_checks_primary_evidence_count_nonnegative",
        "fact_checks",
        type_="check",
    )
    op.drop_constraint("ck_fact_checks_review_state", "fact_checks", type_="check")
    op.drop_constraint("ck_fact_checks_label", "fact_checks", type_="check")
    op.drop_constraint("ck_claims_risk_level", "claims", type_="check")
    op.drop_constraint("ck_claims_status", "claims", type_="check")
    op.drop_constraint("ck_stories_risk_level", "stories", type_="check")

    op.drop_constraint("uq_jobs_semantic_key", "jobs", type_="unique")
    op.drop_index("ix_jobs_semantic_key", table_name="jobs")
    op.drop_column("jobs", "semantic_key")

    op.drop_constraint("uq_fact_sheets_semantic_key", "fact_sheets", type_="unique")
    op.drop_index("ix_fact_sheets_semantic_key", table_name="fact_sheets")
    op.drop_column("fact_sheets", "semantic_key")

    op.drop_constraint("uq_fact_checks_semantic_key", "fact_checks", type_="unique")
    op.drop_index("ix_fact_checks_semantic_key", table_name="fact_checks")
    op.drop_index("ix_fact_checks_ai_run_id", table_name="fact_checks")
    op.drop_constraint("fk_fact_checks_ai_run_id_ai_runs", "fact_checks", type_="foreignkey")
    op.drop_column("fact_checks", "semantic_key")
    op.drop_column("fact_checks", "ai_run_id")
    op.drop_column("fact_checks", "contradicting_count")
    op.drop_column("fact_checks", "supporting_count")
    op.drop_column("fact_checks", "primary_evidence_count")

    op.drop_index("ix_stories_verification_semantic_key", table_name="stories")
    op.drop_column("stories", "verification_semantic_key")
