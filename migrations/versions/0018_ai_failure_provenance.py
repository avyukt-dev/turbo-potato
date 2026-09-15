"""Persist sanitized AI routing failure provenance on worker failures."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0018_ai_failure_provenance"
down_revision = "0017_evidence_graph_enrichment"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column(
        "event_processing_attempts",
        sa.Column("ai_failure_provenance", JSONB, nullable=True),
    )
    op.add_column(
        "event_dead_letters",
        sa.Column("ai_failure_provenance", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("event_dead_letters", "ai_failure_provenance")
    op.drop_column("event_processing_attempts", "ai_failure_provenance")
