"""Add stable identities for config-managed collection sources and feeds.

Revision ID: 0002_source_registry_identity
Revises: 0001_foundation
Create Date: 2026-09-10

The general sources tables remain usable by research/evidence records that are not managed by
collection configuration. Mapping tables isolate configuration ownership from general source data.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_source_registry_identity"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_registry_entries",
        sa.Column("source_key", sa.String(length=128), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_source_registry_entries_source_id_sources",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_key", name="pk_source_registry_entries"),
        sa.UniqueConstraint("source_id", name="uq_source_registry_entries_source_id"),
    )

    op.create_table(
        "source_feed_registry_entries",
        sa.Column("feed_key", sa.String(length=128), nullable=False),
        sa.Column("source_feed_id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["source_feed_id"],
            ["source_feeds.id"],
            name="fk_source_feed_registry_entries_source_feed_id_source_feeds",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("feed_key", name="pk_source_feed_registry_entries"),
        sa.UniqueConstraint(
            "source_feed_id",
            name="uq_source_feed_registry_entries_source_feed_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("source_feed_registry_entries")
    op.drop_table("source_registry_entries")
