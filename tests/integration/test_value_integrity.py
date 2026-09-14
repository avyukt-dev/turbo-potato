"""Real PostgreSQL nullable history, JSON/FK invariants and exact 0015 rollback."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from helpers.claim_semantics import classification_run
from integration.test_claim_semantic_persistence import postgres_factory as _postgres_factory
from integration.test_runtime_control_migration import schema
from news_ai_database import Claim, ContentDraft, ContentQualityCheck
from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from unit.content.test_value_generation import attach_values
from unit.quality.test_quality_gate import QualityAI, _run, _seed, _service


@pytest.fixture
def postgres_factory():
    yield from _postgres_factory.__wrapped__()


def test_postgresql_value_escalation_and_exact_review_binding(postgres_factory):
    factory = postgres_factory
    event, _, _ = _seed(factory)
    claim = attach_values(factory, quality=True)
    finding = {
        "claim_id": claim["claim_id"],
        "anchor_id": claim["values"]["anchors"][0]["anchor_id"],
        "artifact_path": "caption",
        "reason_code": "MAGNITUDE_MISMATCH",
    }
    context, result = _run(
        factory, _service(QualityAI(mutate={"value_escalations": [finding]})), event
    )
    assert not result.passed and context.variants[0].semantic_report.passed
    with factory() as session:
        check = session.scalar(select(ContentQualityCheck))
        assert (
            check.value_escalations == [finding]
            and check.methodology_version == "quality-gate-methodology-v7"
        )


def test_0016_honest_null_array_fk_and_exact_schema_round_trip(monkeypatch):
    url = os.getenv("NEWS_AI_DATABASE_URL")
    if not url or os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("isolated PostgreSQL required")
    base = make_url(url)
    name = f"news_ai_values_migration_{uuid4().hex}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = base.set(database=name)
    engine = create_engine(target)
    monkeypatch.setenv("NEWS_AI_DATABASE_URL", target.render_as_string(hide_password=False))
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "0015_claim_semantics")
        previous = schema(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        legacy_event, legacy_draft_id, legacy_variant_id = _seed(factory)
        legacy_quality_id = uuid4()
        with factory() as session:
            legacy_draft = session.get(ContentDraft, legacy_draft_id)
            legacy_sheet_id = legacy_draft.fact_sheet_id
        old_quality = Table("content_quality_checks", MetaData(), autoload_with=engine)
        with engine.begin() as connection:
            connection.execute(
                old_quality.insert().values(
                    id=legacy_quality_id,
                    content_draft_id=legacy_draft_id,
                    content_variant_id=legacy_variant_id,
                    content_variant_version=1,
                    fact_sheet_id=legacy_sheet_id,
                    fact_sheet_version=1,
                    methodology_version="quality-gate-methodology-v6",
                    semantic_key="legacy-v6",
                    factual_accuracy_passed=False,
                    source_alignment_passed=False,
                    citation_alignment_passed=False,
                    style_passed=False,
                    defamation_risk=False,
                    sensitive_topic_error=False,
                    passed=False,
                    review_required=True,
                    unsupported_claims=[],
                    fabricated_quotes=[],
                    incorrect_names=[],
                    incorrect_dates=[],
                    incorrect_numbers=[],
                    missing_context=[],
                    notes=[],
                )
            )
        story_id, claim_id = uuid4(), uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO stories (id,status,risk_level,story_metadata) "
                    "VALUES (:id,'DISCOVERED','LOW','{}')"
                ),
                {"id": story_id},
            )
            connection.execute(
                text(
                    "INSERT INTO claims (id,story_id,claim_text,status,risk_level,"
                    "research_generation,claim_metadata) "
                    "VALUES (:id,:story,'Historical value','SUPPORTED','LOW',0,'{}')"
                ),
                {"id": claim_id, "story": story_id},
            )
        command.upgrade(config, "head")
        command.check(config)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            legacy = session.get(Claim, claim_id)
            assert (
                legacy.value_anchors
                is legacy.value_policy_version
                is legacy.value_ai_run_id
                is None
            )
            assert legacy.status == "SUPPORTED"
            assert session.get(ContentQualityCheck, legacy_quality_id).value_escalations is None
        _, result = _run(factory, _service(QualityAI()), legacy_event)
        with factory() as session, session.begin():
            quality = session.get(ContentQualityCheck, result.quality_check_ids[0])
            quality.methodology_version = "quality-gate-methodology-v6"
            quality.value_escalations = None
            quality_id = quality.id
            run_id = classification_run(session)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE claims SET value_anchors='[]',"
                    "value_policy_version='value-integrity-policy-v1',"
                    "value_ai_run_id=:run WHERE id=:id"
                ),
                {"id": claim_id, "run": run_id},
            )
            assert (
                connection.scalar(
                    text("SELECT jsonb_typeof(value_anchors) FROM claims WHERE id=:id"),
                    {"id": claim_id},
                )
                == "array"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT value_escalations IS NULL FROM content_quality_checks WHERE id=:id"
                    ),
                    {"id": quality_id},
                )
                is True
            )
        for statement in [
            "UPDATE claims SET value_anchors='{}' WHERE id=:id",
            "UPDATE claims SET value_anchors='null' WHERE id=:id",
            "UPDATE claims SET value_policy_version=NULL WHERE id=:id",
            "UPDATE claims SET value_ai_run_id=:missing WHERE id=:id",
        ]:
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(text(statement), {"id": claim_id, "missing": uuid4()})
        command.downgrade(config, "0015_claim_semantics")
        assert schema(engine) == previous
        command.upgrade(config, "head")
        command.check(config)
        with factory() as session:
            assert session.get(Claim, claim_id).value_anchors is None
            assert session.get(ContentQualityCheck, quality_id).value_escalations is None
            assert (
                session.get(ContentQualityCheck, quality_id).methodology_version
                == "quality-gate-methodology-v6"
            )
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
