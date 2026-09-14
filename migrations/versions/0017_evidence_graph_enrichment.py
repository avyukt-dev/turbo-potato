"""Add honest nullable evidence semantics and typed graph relations."""

import sqlalchemy as sa
from alembic import op

revision = "0017_evidence_graph_enrichment"
down_revision = "0016_value_integrity"
branch_labels = None
depends_on = None


def upgrade():
    for name, length in (
        ("directness", 16),
        ("origin_role", 16),
        ("provenance_state", 32),
        ("temporal_role", 16),
        ("semantics_policy_version", 64),
    ):
        op.add_column("claim_evidence", sa.Column(name, sa.String(length), nullable=True))
    op.create_check_constraint(
        "ck_claim_evidence_semantics_complete",
        "claim_evidence",
        "(directness IS NULL AND origin_role IS NULL AND provenance_state IS NULL "
        "AND temporal_role IS NULL AND semantics_policy_version IS NULL) OR "
        "(directness IS NOT NULL AND origin_role IS NOT NULL AND provenance_state IS NOT NULL "
        "AND temporal_role IS NOT NULL AND semantics_policy_version IS NOT NULL)",
    )
    for name, values in (
        ("directness", "'DIRECT','INDIRECT','UNKNOWN'"),
        ("origin_role", "'ORIGINAL','DERIVATIVE','REFERENCE','UNKNOWN'"),
        ("provenance_state", "'DURABLE_VERSION_PRESERVED','EXTERNAL_REFERENCE_ONLY','UNKNOWN'"),
        ("temporal_role", "'CONTEMPORARY','RETROSPECTIVE','UPDATE','UNKNOWN'"),
    ):
        op.create_check_constraint(
            f"ck_claim_evidence_{name}", "claim_evidence", f"{name} IN ({values})"
        )
    op.create_table(
        "evidence_graph_relations",
        sa.Column("source_evidence_id", sa.Uuid(), nullable=False),
        sa.Column("target_evidence_id", sa.Uuid(), nullable=True),
        sa.Column("external_reference", sa.Text(), nullable=True),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column("basis", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("research_generation", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "relation_type IN ('DERIVED_FROM','REFERENCES','CONTEXT_FOR','VERIFIES','UPDATES')",
            name="ck_evidence_graph_relations_type",
        ),
        sa.CheckConstraint(
            "(target_evidence_id IS NOT NULL AND external_reference IS NULL) OR "
            "(target_evidence_id IS NULL AND external_reference IS NOT NULL)",
            name="ck_evidence_graph_relations_target",
        ),
        sa.CheckConstraint(
            "research_generation >= 1", name="ck_evidence_graph_relations_generation"
        ),
        sa.CheckConstraint(
            "source_evidence_id <> target_evidence_id", name="ck_evidence_graph_relations_no_self"
        ),
        sa.CheckConstraint(
            "length(trim(basis)) > 0 AND (external_reference IS NULL OR "
            "(length(trim(external_reference)) > 0 AND length(external_reference) <= 4096))",
            name="ck_evidence_graph_relations_reference",
        ),
        sa.ForeignKeyConstraint(["research_run_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["source_evidence_id"], ["evidence_items.id"]),
        sa.ForeignKeyConstraint(["target_evidence_id"], ["evidence_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for suffix, target in (("durable", "target_evidence_id"), ("external", "external_reference")):
        op.create_index(
            f"uq_evidence_graph_{suffix}_target",
            "evidence_graph_relations",
            ["source_evidence_id", "relation_type", target, "policy_version"],
            unique=True,
            postgresql_where=sa.text(f"{target} IS NOT NULL"),
            sqlite_where=sa.text(f"{target} IS NOT NULL"),
        )
    op.create_index(
        "ix_evidence_graph_relations_source_evidence_id",
        "evidence_graph_relations",
        ["source_evidence_id"],
    )
    op.create_index(
        "ix_evidence_graph_relations_target_evidence_id",
        "evidence_graph_relations",
        ["target_evidence_id"],
    )
    op.create_index(
        "ix_evidence_graph_relations_research_run_id",
        "evidence_graph_relations",
        ["research_run_id"],
    )


def downgrade():
    op.drop_table("evidence_graph_relations")
    for name in ("directness", "origin_role", "provenance_state", "temporal_role"):
        op.drop_constraint(f"ck_claim_evidence_{name}", "claim_evidence", type_="check")
    op.drop_constraint("ck_claim_evidence_semantics_complete", "claim_evidence", type_="check")
    for name in (
        "semantics_policy_version",
        "temporal_role",
        "provenance_state",
        "origin_role",
        "directness",
    ):
        op.drop_column("claim_evidence", name)
