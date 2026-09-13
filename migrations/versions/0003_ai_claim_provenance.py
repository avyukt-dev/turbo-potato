"""Add durable AI model/run provenance for claim extraction.

Revision ID: 0003_ai_claim_provenance
Revises: 0002_source_registry_identity
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_ai_claim_provenance"
down_revision: str | None = "0002_source_registry_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "ai_models",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("locality", sa.String(length=16), nullable=False),
        sa.Column("capabilities", JSONB, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("model_metadata", JSONB, nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_ai_models"),
        sa.UniqueConstraint("provider", "model_name", name="uq_ai_models_provider_model_name"),
    )
    op.create_index("ix_ai_models_provider", "ai_models", ["provider"])

    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ai_model_id", sa.Uuid(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("prompt_id", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("prompt_checksum", sa.String(length=128), nullable=True),
        sa.Column("input_artifact_ids", JSONB, nullable=False),
        sa.Column("input_hash", sa.String(length=128), nullable=True),
        sa.Column("output_payload", JSONB, nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("routing_attempts", JSONB, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["ai_model_id"], ["ai_models.id"], name="fk_ai_runs_ai_model_id_ai_models"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_runs"),
    )
    op.create_index("ix_ai_runs_ai_model_id", "ai_runs", ["ai_model_id"])
    op.create_index("ix_ai_runs_correlation_id", "ai_runs", ["correlation_id"])
    op.create_index("ix_ai_runs_input_hash", "ai_runs", ["input_hash"])
    op.create_index("ix_ai_runs_status", "ai_runs", ["status"])
    op.create_index("ix_ai_runs_task_type", "ai_runs", ["task_type"])

    op.add_column("claims", sa.Column("created_by_ai_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_claims_created_by_ai_run_id_ai_runs",
        "claims",
        "ai_runs",
        ["created_by_ai_run_id"],
        ["id"],
    )
    op.create_index("ix_claims_created_by_ai_run_id", "claims", ["created_by_ai_run_id"])


def downgrade() -> None:
    op.drop_index("ix_claims_created_by_ai_run_id", table_name="claims")
    op.drop_constraint("fk_claims_created_by_ai_run_id_ai_runs", "claims", type_="foreignkey")
    op.drop_column("claims", "created_by_ai_run_id")

    op.drop_index("ix_ai_runs_task_type", table_name="ai_runs")
    op.drop_index("ix_ai_runs_status", table_name="ai_runs")
    op.drop_index("ix_ai_runs_input_hash", table_name="ai_runs")
    op.drop_index("ix_ai_runs_correlation_id", table_name="ai_runs")
    op.drop_index("ix_ai_runs_ai_model_id", table_name="ai_runs")
    op.drop_table("ai_runs")

    op.drop_index("ix_ai_models_provider", table_name="ai_models")
    op.drop_table("ai_models")
