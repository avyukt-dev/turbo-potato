"""Add secret-free durable per-key AI credential state."""

import sqlalchemy as sa
from alembic import op

revision = "0019_ai_credential_routing"
down_revision = "0018_ai_failure_provenance"
branch_labels = None
depends_on = None

SYSTEM_ACTIONS_BEFORE_0019 = (
    "'PUBLICATION_SCHEDULED','PUBLICATION_BLOCKED','PUBLICATION_EXECUTION_STARTED',"
    "'PUBLICATION_EXECUTED','PUBLICATION_FAILED','PUBLICATION_RETRY_SCHEDULED',"
    "'PUBLICATION_RECOVERY','RUNTIME_PUBLISHING_PAUSED','RUNTIME_PUBLISHING_RESUMED'"
)


def upgrade() -> None:
    op.create_table(
        "ai_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pool_id", sa.String(length=64), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("secret_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("slot >= 1 AND slot <= 128", name="ck_ai_credentials_slot"),
        sa.CheckConstraint(
            "state IN ('HEALTHY','COOLDOWN','AUTH_FAILED','UNKNOWN')",
            name="ck_ai_credentials_state",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code IN "
            "('RATE_LIMIT','AUTHENTICATION','UNKNOWN_CREDENTIAL_FAILURE',"
            "'OPERATOR_RESET','SECRET_REPLACED')",
            name="ck_ai_credentials_reason_code",
        ),
        sa.CheckConstraint("consecutive_failures >= 0", name="ck_ai_credentials_failures"),
        sa.CheckConstraint("revision >= 1", name="ck_ai_credentials_revision"),
        sa.CheckConstraint(
            "(state = 'COOLDOWN' AND cooldown_until IS NOT NULL) OR "
            "(state <> 'COOLDOWN' AND cooldown_until IS NULL)",
            name="ck_ai_credentials_cooldown_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pool_id", "slot", name="uq_ai_credentials_pool_slot"),
    )
    op.create_index(
        "ix_ai_credentials_pool_active_state",
        "ai_credentials",
        ["pool_id", "is_active", "state"],
    )
    op.drop_constraint(op.f("ck_audit_log_audit_system_actor"), "audit_log", type_="check")
    op.create_check_constraint(
        "audit_system_actor",
        "audit_log",
        f"actor_id IS NOT NULL OR action IN ({SYSTEM_ACTIONS_BEFORE_0019},'AI_CREDENTIAL_RESET')",
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM audit_log WHERE action = 'AI_CREDENTIAL_RESET'"))
    op.drop_constraint(op.f("ck_audit_log_audit_system_actor"), "audit_log", type_="check")
    op.create_check_constraint(
        "audit_system_actor",
        "audit_log",
        f"actor_id IS NOT NULL OR action IN ({SYSTEM_ACTIONS_BEFORE_0019})",
    )
    op.drop_index("ix_ai_credentials_pool_active_state", table_name="ai_credentials")
    op.drop_table("ai_credentials")
