"""Add honest nullable claim classification and prose semantic assessment provenance."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_claim_semantics"
down_revision = "0014_certainty_firewall"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No historical classification or assessment is fabricated.
    op.add_column("claims", sa.Column("semantic_type", sa.String(32), nullable=True))
    op.add_column("claims", sa.Column("semantic_state", sa.String(16), nullable=True))
    op.add_column("claims", sa.Column("semantic_policy_version", sa.String(64), nullable=True))
    op.add_column("claims", sa.Column("semantic_ai_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_claims_semantic_ai_run_id_ai_runs", "claims", "ai_runs", ["semantic_ai_run_id"], ["id"]
    )
    op.create_index("ix_claims_semantic_ai_run_id", "claims", ["semantic_ai_run_id"])
    op.create_check_constraint(
        "ck_claims_semantic_type",
        "claims",
        "semantic_type IN ('GENERAL_FACT', 'EVENT', 'QUANTITATIVE', 'ATTRIBUTION', "
        "'LEGAL_PROCEDURAL', 'CAUSAL', 'PREDICTION_FORECAST', 'POLICY_COMMITMENT')",
    )
    op.create_check_constraint(
        "ck_claims_semantic_state",
        "claims",
        "semantic_state IN ('OBSERVED', 'ANNOUNCED', 'PLANNED', 'EXPECTED', 'PREDICTED')",
    )
    op.create_check_constraint(
        "ck_claims_semantics_complete",
        "claims",
        "(semantic_type IS NULL AND semantic_state IS NULL AND "
        "semantic_policy_version IS NULL AND semantic_ai_run_id IS NULL) OR "
        "(semantic_type IS NOT NULL AND semantic_state IS NOT NULL AND "
        "semantic_policy_version IS NOT NULL AND semantic_ai_run_id IS NOT NULL)",
    )
    op.add_column(
        "content_quality_checks",
        sa.Column(
            "claim_semantic_escalations", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("content_quality_checks", "claim_semantic_escalations")
    for name in (
        "ck_claims_semantics_complete",
        "ck_claims_semantic_state",
        "ck_claims_semantic_type",
    ):
        op.drop_constraint(name, "claims", type_="check")
    op.drop_index("ix_claims_semantic_ai_run_id", table_name="claims")
    op.drop_constraint("fk_claims_semantic_ai_run_id_ai_runs", "claims", type_="foreignkey")
    for name in (
        "semantic_ai_run_id",
        "semantic_policy_version",
        "semantic_state",
        "semantic_type",
    ):
        op.drop_column("claims", name)
