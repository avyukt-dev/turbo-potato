"""Real PostgreSQL certainty persistence, replay, and historical migration truth."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from integration.test_human_review import postgres_factory as _postgres_factory
from integration.test_runtime_control_migration import schema
from news_ai_database import ContentQualityCheck, ContentVariant
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from unit.quality.test_quality_gate import QualityAI, _run, _seed, _service


@pytest.fixture
def postgres_factory():
    if not os.getenv("NEWS_AI_DATABASE_URL"):
        pytest.skip("PostgreSQL dependency is not configured")
    yield from _postgres_factory.__wrapped__()


def test_postgresql_certainty_error_persists_and_replay_is_safe(postgres_factory):
    event, _, variant_id = _seed(postgres_factory)
    with postgres_factory() as session, session.begin():
        variant = session.get(ContentVariant, variant_id)
        payload = dict(variant.structured_payload)
        payload["claim_presentations"] = [
            {**payload["claim_presentations"][0], "assertion_strength": "HIGH", "frame": "DIRECT"}
        ]
        variant.structured_payload = payload
    ai = QualityAI()
    service = _service(ai)
    context, result = _run(postgres_factory, service, event)
    assert not result.passed and ai.calls == 1
    with postgres_factory() as session:
        check = session.scalar(select(ContentQualityCheck))
        assert not check.semantic_validation_passed
        assert any(f["category"] == "CERTAINTY" for f in check.semantic_findings["findings"])
        assert check.certainty_escalations == []
        assert (
            service.existing_result(session, context).quality_check_ids == result.quality_check_ids
        )


def test_postgresql_prose_escalation_persists(postgres_factory):
    event, _, variant_id = _seed(postgres_factory)
    with postgres_factory() as session:
        claim_id = session.get(ContentVariant, variant_id).claim_ids_used[0]
    escalation = {
        "claim_id": claim_id,
        "artifact_path": "caption",
        "reason_code": "QUALIFICATION_OMITTED",
    }
    _, result = _run(
        postgres_factory, _service(QualityAI(mutate={"certainty_escalations": [escalation]})), event
    )
    assert not result.passed
    with postgres_factory() as session:
        assert session.scalar(select(ContentQualityCheck)).certainty_escalations == [escalation]


def test_0014_honest_null_and_exact_0013_schema_round_trip(monkeypatch):
    url = os.getenv("NEWS_AI_DATABASE_URL")
    if not url or os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("isolated PostgreSQL required")
    base = make_url(url)
    name = f"news_ai_certainty_migration_{uuid4().hex}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = base.set(database=name)
    engine = create_engine(target)
    monkeypatch.setenv("NEWS_AI_DATABASE_URL", target.render_as_string(hide_password=False))
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "0013_semantic_validation")
        previous_schema = schema(engine)
        command.upgrade(config, "head")
        from sqlalchemy.orm import sessionmaker

        factory = sessionmaker(engine, expire_on_commit=False)
        event, _, _ = _seed(factory)
        _run(factory, _service(QualityAI()), event)
        with factory() as session, session.begin():
            check = session.scalar(select(ContentQualityCheck))
            check.methodology_version = "quality-gate-methodology-v4"
            old_id = check.id
        command.downgrade(config, "0013_semantic_validation")
        assert schema(engine) == previous_schema
        command.upgrade(config, "head")
        command.check(config)
        with factory() as session:
            legacy = session.get(ContentQualityCheck, old_id)
            assert legacy.certainty_escalations is None
            assert legacy.methodology_version == "quality-gate-methodology-v4"
            assert legacy.passed is True
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
