"""Real PostgreSQL report persistence and conservative historical migration."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from integration.test_human_review import postgres_factory as _postgres_factory
from integration.test_runtime_control_migration import schema
from news_ai_database import ContentQualityCheck
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from unit.quality.test_quality_gate import QualityAI, _run, _seed, _service
from unit.quality.test_semantic_integration import check_semantic_error
from unit.review.test_semantic_detail import check_warning_detail


@pytest.fixture
def postgres_factory():
    if not os.getenv("NEWS_AI_DATABASE_URL"):
        pytest.skip("PostgreSQL dependency is not configured")
    yield from _postgres_factory.__wrapped__()


def test_postgresql_semantic_failure_and_replay(postgres_factory):
    check_semantic_error(postgres_factory)


def test_postgresql_review_detail_exposes_durable_semantic_warning(postgres_factory, monkeypatch):
    check_warning_detail(postgres_factory, monkeypatch)


def test_0013_preserves_legacy_semantics_and_exact_0012_schema(monkeypatch):
    url = os.getenv("NEWS_AI_DATABASE_URL")
    if not url or os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("isolated PostgreSQL required")
    base = make_url(url)
    name = f"news_ai_semantic_migration_{uuid4().hex}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = base.set(database=name)
    engine = create_engine(target)
    monkeypatch.setenv("NEWS_AI_DATABASE_URL", target.render_as_string(hide_password=False))
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "0012_runtime_controls")
        previous_schema = schema(engine)
        command.upgrade(config, "head")
        from sqlalchemy.orm import sessionmaker

        factory = sessionmaker(engine, expire_on_commit=False)
        event, _, _ = _seed(factory)
        _run(factory, _service(QualityAI()), event)
        with factory() as session, session.begin():
            check = session.scalar(select(ContentQualityCheck))
            check.methodology_version = "quality-gate-methodology-v3"
            old_id = check.id
        command.downgrade(config, "0012_runtime_controls")
        assert schema(engine) == previous_schema
        command.upgrade(config, "head")
        command.check(config)
        with factory() as session:
            legacy = session.get(ContentQualityCheck, old_id)
            assert legacy.methodology_version == "quality-gate-methodology-v3"
            assert legacy.passed is True
            assert legacy.semantic_validation_passed is None
            assert legacy.semantic_methodology_version is None
            assert legacy.semantic_findings is None
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
