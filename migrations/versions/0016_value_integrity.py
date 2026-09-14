"""Nullable value annotations and prose audit, without invented historical evaluation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_value_integrity"
down_revision = "0015_claim_semantics"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("claims", sa.Column("value_anchors", postgresql.JSONB(), nullable=True))
    op.add_column("claims", sa.Column("value_policy_version", sa.String(64), nullable=True))
    op.add_column("claims", sa.Column("value_ai_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_claims_value_ai_run_id_ai_runs", "claims", "ai_runs", ["value_ai_run_id"], ["id"]
    )
    op.create_index("ix_claims_value_ai_run_id", "claims", ["value_ai_run_id"])
    op.create_check_constraint(
        "ck_claims_values_complete",
        "claims",
        "(value_anchors IS NULL AND value_policy_version IS NULL AND value_ai_run_id IS NULL) "
        "OR (value_anchors IS NOT NULL AND value_policy_version IS NOT NULL "
        "AND value_ai_run_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_claims_values_array", "claims", "jsonb_typeof(value_anchors) = 'array'"
    )
    op.add_column(
        "content_quality_checks", sa.Column("value_escalations", postgresql.JSONB(), nullable=True)
    )


def downgrade():
    op.drop_column("content_quality_checks", "value_escalations")
    op.drop_constraint("ck_claims_values_array", "claims", type_="check")
    op.drop_constraint("ck_claims_values_complete", "claims", type_="check")
    op.drop_index("ix_claims_value_ai_run_id", table_name="claims")
    op.drop_constraint("fk_claims_value_ai_run_id_ai_runs", "claims", type_="foreignkey")
    for field in ("value_ai_run_id", "value_policy_version", "value_anchors"):
        op.drop_column("claims", field)
