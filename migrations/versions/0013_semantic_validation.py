"""Persist deterministic quality reports without fabricating historical evaluation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_semantic_validation"
down_revision = "0012_runtime_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_quality_checks",
        sa.Column("semantic_validation_passed", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "content_quality_checks",
        sa.Column("semantic_methodology_version", sa.String(64), nullable=True),
    )
    op.add_column(
        "content_quality_checks",
        sa.Column("semantic_findings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("content_quality_checks", "semantic_findings")
    op.drop_column("content_quality_checks", "semantic_methodology_version")
    op.drop_column("content_quality_checks", "semantic_validation_passed")
