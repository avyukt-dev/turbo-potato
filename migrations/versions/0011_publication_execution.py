"""Durable publication execution checkpoints and recovery.

Revision ID: 0011_publication_execution
Revises: 0010_publication_scheduler
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_publication_execution"
down_revision = "0010_publication_scheduler"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
SYSTEM_ACTIONS = (
    "'PUBLICATION_SCHEDULED','PUBLICATION_BLOCKED','PUBLICATION_EXECUTION_STARTED',"
    "'PUBLICATION_EXECUTED','PUBLICATION_FAILED','PUBLICATION_RETRY_SCHEDULED',"
    "'PUBLICATION_RECOVERY'"
)


def upgrade():
    # Stage 25 cannot legitimately have executed rows. Fail rather than fabricate
    # provider results if historical data violates the new durable invariants.
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT count(*) FROM publications WHERE status IN "
            "('PUBLISHED','RETRYING','PUBLISHING')"
        )
    ):
        raise RuntimeError("publication execution data requires explicit historical reconciliation")
    for name in ("started_at", "published_at", "next_retry_at"):
        op.add_column("publications", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    for name, kind in (
        ("external_post_id", sa.String(1024)),
        ("external_url", sa.Text()),
        ("failure_reason", sa.String(128)),
    ):
        op.add_column("publications", sa.Column(name, kind, nullable=True))
    op.add_column(
        "publications", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.create_check_constraint("publication_attempt_count", "publications", "attempt_count >= 0")
    op.create_check_constraint(
        "publication_published_result",
        "publications",
        "status != 'PUBLISHED' OR (external_post_id IS NOT NULL AND published_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "publication_retry_schedule",
        "publications",
        "status != 'RETRYING' OR next_retry_at IS NOT NULL",
    )
    op.create_index("ix_publications_retry_due", "publications", ["status", "next_retry_at"])
    op.create_unique_constraint(
        "uq_publications_external_destination",
        "publications",
        ["platform", "social_account_id", "external_post_id"],
    )
    op.create_table(
        "publication_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("publication_id", sa.Uuid(), sa.ForeignKey("publications.id"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("execution_request_hash", sa.String(64), nullable=False),
        sa.Column("trigger_event_id", sa.Uuid(), nullable=True),
        sa.Column("provider_operation_id", sa.String(255), nullable=True),
        sa.Column("external_post_id", sa.String(1024), nullable=True),
        sa.Column("external_url", sa.Text(), nullable=True),
        sa.Column("provider_metadata", JSON, nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_class", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(512), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("ambiguous", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("retry_after_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "publication_id", "attempt_number", name="uq_publication_attempt_number"
        ),
        sa.CheckConstraint("attempt_number >= 1", name="publication_attempt_number"),
        sa.CheckConstraint(
            "length(execution_request_hash) = 64", name="publication_execution_hash"
        ),
        sa.CheckConstraint(
            "status IN ('IN_PROGRESS','SUCCEEDED','RETRYABLE_FAILED',"
            "'TERMINAL_FAILED','AMBIGUOUS','BLOCKED')",
            name="publication_attempt_status",
        ),
        sa.CheckConstraint(
            "phase IN ('PREPARING','PREPARED','PUBLISH_INTENT_RECORDED',"
            "'PUBLISH_RESPONSE_RECEIVED','VERIFYING','COMPLETE')",
            name="publication_attempt_phase",
        ),
    )
    op.create_index(
        "ix_publication_attempts_publication_id", "publication_attempts", ["publication_id"]
    )
    op.create_index(
        "ix_publication_attempts_trigger_event_id", "publication_attempts", ["trigger_event_id"]
    )
    op.create_index(
        "uq_publication_active_attempt",
        "publication_attempts",
        ["publication_id"],
        unique=True,
        postgresql_where=sa.text("status = 'IN_PROGRESS'"),
        sqlite_where=sa.text("status = 'IN_PROGRESS'"),
    )
    op.create_table(
        "publication_retry_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("publication_id", sa.Uuid(), sa.ForeignKey("publications.id"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_publication_retry_actor_key"),
        sa.CheckConstraint("length(request_hash) = 64", name="publication_retry_hash"),
    )
    op.create_index(
        "ix_publication_retry_operations_publication_id",
        "publication_retry_operations",
        ["publication_id"],
    )
    op.drop_constraint(op.f("ck_audit_log_audit_system_actor"), "audit_log", type_="check")
    op.create_check_constraint(
        "audit_system_actor", "audit_log", f"actor_id IS NOT NULL OR action IN ({SYSTEM_ACTIONS})"
    )


def downgrade():
    # The 0010 schema cannot retain execution checkpoints. Preserve the publication
    # as conservatively blocked rather than leave executable work without its fence.
    op.execute(
        sa.text(
            "UPDATE publications SET status = 'BLOCKED', "
            "blocking_reason = 'EXECUTION_HISTORY_REMOVED' "
            "WHERE status IN ('PUBLISHING','RETRYING','PUBLISHED')"
        )
    )
    op.drop_constraint(op.f("ck_audit_log_audit_system_actor"), "audit_log", type_="check")
    op.execute(
        sa.text(
            "DELETE FROM audit_log WHERE actor_id IS NULL AND action IN "
            "('PUBLICATION_EXECUTION_STARTED','PUBLICATION_EXECUTED','PUBLICATION_FAILED',"
            "'PUBLICATION_RETRY_SCHEDULED','PUBLICATION_RECOVERY')"
        )
    )
    op.create_check_constraint(
        "audit_system_actor",
        "audit_log",
        "actor_id IS NOT NULL OR action IN ('PUBLICATION_SCHEDULED','PUBLICATION_BLOCKED')",
    )
    op.drop_table("publication_retry_operations")
    op.drop_table("publication_attempts")
    op.drop_constraint("uq_publications_external_destination", "publications", type_="unique")
    op.drop_index("ix_publications_retry_due", table_name="publications")
    for name in (
        "publication_retry_schedule",
        "publication_published_result",
        "publication_attempt_count",
    ):
        op.drop_constraint(op.f(f"ck_publications_{name}"), "publications", type_="check")
    for name in (
        "next_retry_at",
        "attempt_count",
        "failure_reason",
        "external_url",
        "external_post_id",
        "published_at",
        "started_at",
    ):
        op.drop_column("publications", name)
