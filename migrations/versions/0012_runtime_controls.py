"""Durable publication kill switch and audited local operator controls."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0012_runtime_controls"
down_revision = "0011_publication_execution"
branch_labels = None
depends_on = None

STAGE_26_ACTIONS = (
    "'PUBLICATION_SCHEDULED','PUBLICATION_BLOCKED','PUBLICATION_EXECUTION_STARTED',"
    "'PUBLICATION_EXECUTED','PUBLICATION_FAILED','PUBLICATION_RETRY_SCHEDULED','PUBLICATION_RECOVERY'"
)


def upgrade():
    table = op.create_table(
        "runtime_controls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("control_key", sa.String(64), nullable=False),
        sa.Column("boolean_value", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("control_key", name="uq_runtime_controls_key"),
        sa.CheckConstraint("control_key = 'PUBLISHING_PAUSED'", name="runtime_control_key"),
        sa.CheckConstraint("revision >= 1", name="runtime_control_revision"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 512", name="runtime_control_reason"),
    )
    # Never grant publication permission implicitly during deployment.
    op.bulk_insert(
        table,
        [
            {
                "id": uuid4(),
                "control_key": "PUBLISHING_PAUSED",
                "boolean_value": True,
                "revision": 1,
                "reason": "Deployment safety pause; explicit operator resume required",
            }
        ],
    )
    op.drop_constraint(op.f("ck_audit_log_audit_system_actor"), "audit_log", type_="check")
    op.create_check_constraint(
        "audit_system_actor",
        "audit_log",
        f"actor_id IS NOT NULL OR action IN ({STAGE_26_ACTIONS},"
        "'RUNTIME_PUBLISHING_PAUSED','RUNTIME_PUBLISHING_RESUMED')",
    )


def downgrade():
    op.execute(
        sa.text(
            "DELETE FROM audit_log WHERE artifact_type = 'runtime_control' AND "
            "action IN ('RUNTIME_PUBLISHING_PAUSED','RUNTIME_PUBLISHING_RESUMED')"
        )
    )
    op.drop_constraint(op.f("ck_audit_log_audit_system_actor"), "audit_log", type_="check")
    op.create_check_constraint(
        "audit_system_actor", "audit_log", f"actor_id IS NOT NULL OR action IN ({STAGE_26_ACTIONS})"
    )
    op.drop_table("runtime_controls")
