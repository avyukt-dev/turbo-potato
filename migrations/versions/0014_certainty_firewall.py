"""Nullable prose certainty audit; historical rows were not evaluated."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_certainty_firewall"
down_revision = "0013_semantic_validation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_quality_checks",
        sa.Column("certainty_escalations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("content_quality_checks", "certainty_escalations")
