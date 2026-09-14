"""PostgreSQL evidence semantics: honest history, constraints and exact rollback."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from integration.test_runtime_control_migration import schema
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


def test_0017_nullable_history_constraints_and_exact_round_trip(monkeypatch):
    url = os.getenv("NEWS_AI_DATABASE_URL")
    if not url or os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("isolated PostgreSQL required")
    base = make_url(url)
    name = f"news_ai_graph_migration_{uuid4().hex}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = base.set(database=name)
    engine = create_engine(target)
    monkeypatch.setenv("NEWS_AI_DATABASE_URL", target.render_as_string(hide_password=False))
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "0016_value_integrity")
        previous = schema(engine)
        story, claim, evidence, other, run = (uuid4() for _ in range(5))
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO stories (id,status,risk_level,story_metadata) "
                    "VALUES (:id,'DISCOVERED','LOW','{}')"
                ),
                {"id": story},
            )
            connection.execute(
                text(
                    "INSERT INTO claims (id,story_id,claim_text,status,risk_level,"
                    "research_generation,claim_metadata) "
                    "VALUES (:id,:story,'Historical evidence','UNASSESSED','LOW',0,'{}')"
                ),
                {"id": claim, "story": story},
            )
            for item in (evidence, other):
                connection.execute(
                    text(
                        "INSERT INTO evidence_items (id,evidence_type,evidence_metadata) "
                        "VALUES (:id,'ARTICLE','{}')"
                    ),
                    {"id": item},
                )
            connection.execute(
                text(
                    "INSERT INTO claim_evidence (claim_id,evidence_id,relation) "
                    "VALUES (:claim,:evidence,'CONTEXT')"
                ),
                {"claim": claim, "evidence": evidence},
            )
            connection.execute(
                text(
                    "INSERT INTO jobs (id,job_type,status,priority,attempts,payload,result) "
                    "VALUES (:id,'RESEARCH','COMPLETED',2,0,'{}','{}')"
                ),
                {"id": run},
            )
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT directness,origin_role,provenance_state,temporal_role,"
                    "semantics_policy_version FROM claim_evidence"
                )
            ).one()
            assert tuple(row) == (None,) * 5
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE claim_evidence SET directness='UNKNOWN',origin_role='UNKNOWN',"
                    "provenance_state='UNKNOWN',temporal_role='UNKNOWN',"
                    "semantics_policy_version='evidence-graph-policy-v1'"
                )
            )
        for assignment in (
            "directness='GUESS'",
            "origin_role='TRUSTED'",
            "provenance_state='VERIFIED_TRUE'",
            "temporal_role='FUTURE'",
            "directness=NULL",
        ):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(text(f"UPDATE claim_evidence SET {assignment}"))
        insert = text(
            "INSERT INTO evidence_graph_relations (id,source_evidence_id,target_evidence_id,"
            "external_reference,relation_type,basis,policy_version,research_run_id,"
            "research_generation) VALUES (:id,:source,:target,:external,:relation,"
            "'explicit-reference','evidence-graph-policy-v1',:run,1)"
        )
        values = {
            "source": evidence,
            "target": None,
            "external": "document:origin",
            "relation": "REFERENCES",
            "run": run,
        }
        for target_id, external in ((None, "document:origin"), (other, None)):
            with engine.begin() as connection:
                connection.execute(
                    insert, {**values, "id": uuid4(), "target": target_id, "external": external}
                )
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(
                    insert, {**values, "id": uuid4(), "target": target_id, "external": external}
                )
        for overrides in (
            {"relation": "INDEPENDENT"},
            {"target": uuid4(), "external": None},
            {"target": evidence, "external": None},
            {"external": " "},
            {"run": uuid4()},
        ):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(insert, {**values, **overrides, "id": uuid4()})
        command.downgrade(config, "0016_value_integrity")
        assert schema(engine) == previous
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT semantics_policy_version FROM claim_evidence"))
                is None
            )
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
