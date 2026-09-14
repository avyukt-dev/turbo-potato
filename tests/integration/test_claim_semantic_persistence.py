"""Real PostgreSQL classification provenance, serialized reuse and honest migration history."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from helpers.claim_semantics import classification_run
from integration.test_human_review import postgres_factory as _postgres_factory
from integration.test_runtime_control_migration import schema
from news_ai_database import AIRun, Claim, ContentQualityCheck, ContentVariant, EventOutbox
from news_ai_domain import ClaimSemanticState, ClaimSemanticType
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from unit.ai.test_claim_extraction import _claim, _response, _seed_story, _story_event
from unit.ai.test_extraction_semantics import service_for
from unit.quality.test_quality_gate import QualityAI, _run, _seed, _service


@pytest.fixture
def postgres_factory():
    if not os.getenv("NEWS_AI_DATABASE_URL"):
        pytest.skip("PostgreSQL dependency is not configured")
    yield from _postgres_factory.__wrapped__()


def test_concurrent_extraction_completion_has_one_classification(postgres_factory, tmp_path):
    factory = postgres_factory
    story, _, _ = _seed_story(factory)
    service = service_for(tmp_path)
    with factory() as session:
        context = service.load_context(session, story.id)
    events = (_story_event(story.id), _story_event(story.id))
    service.router.registry.get("local").outcomes.extend(
        [_response("local", [_claim()]) for _ in events]
    )
    executions = [asyncio.run(service.generate(context, event)) for event in events]
    barrier = Barrier(2)

    def persist(index):
        barrier.wait()
        with factory() as session, session.begin():
            return service.persist(
                session,
                context=context,
                triggering_event=events[index],
                execution=executions[index],
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(persist, range(2)))
    assert results[0].ai_run_id == results[1].ai_run_id
    with factory() as session:
        claim = session.scalar(select(Claim))
        assert claim.semantic_ai_run_id == results[0].ai_run_id
        assert claim.semantic_state == "ANNOUNCED" and claim.status == "UNASSESSED"
        for model in (Claim, AIRun, EventOutbox):
            assert session.scalar(select(func.count()).select_from(model)) == 1


def test_postgresql_semantic_error_report_and_prose_findings_persist(postgres_factory):
    event, _, variant_id = _seed(postgres_factory)
    with postgres_factory() as session, session.begin():
        variant = session.get(ContentVariant, variant_id)
        claim_id = variant.claim_ids_used[0]
        payload = dict(variant.structured_payload)
        payload["claim_semantic_presentations"] = [
            {**payload["claim_semantic_presentations"][0], "presented_semantic_state": "PLANNED"}
        ]
        variant.structured_payload = payload
    finding = {
        "claim_id": claim_id,
        "artifact_path": "caption",
        "reason_code": "SEMANTIC_TYPE_RECAST",
    }
    context, result = _run(
        postgres_factory,
        _service(QualityAI(mutate={"claim_semantic_escalations": [finding]})),
        event,
    )
    assert not result.passed and not context.variants[0].semantic_report.passed
    with postgres_factory() as session:
        check = session.scalar(select(ContentQualityCheck))
        assert check.claim_semantic_escalations == [finding]
        assert any(f["category"] == "CLAIM_SEMANTICS" for f in check.semantic_findings["findings"])


def test_0015_exact_round_trip_historical_null_and_constraints(monkeypatch):
    url = os.getenv("NEWS_AI_DATABASE_URL")
    if not url or os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("isolated PostgreSQL required")
    base = make_url(url)
    name = f"news_ai_claim_semantics_migration_{uuid4().hex}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = base.set(database=name)
    engine = create_engine(target)
    monkeypatch.setenv("NEWS_AI_DATABASE_URL", target.render_as_string(hide_password=False))
    try:
        config = Config("alembic.ini")
        command.upgrade(config, "0014_certainty_firewall")
        previous = schema(engine)
        story_id, claim_id = uuid4(), uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO stories (id, status, risk_level, story_metadata) "
                    "VALUES (:id, 'DISCOVERED', 'LOW', '{}')"
                ),
                {"id": story_id},
            )
            connection.execute(
                text(
                    "INSERT INTO claims (id, story_id, claim_text, claim_type, status, "
                    "risk_level, research_generation, claim_metadata) "
                    "VALUES (:id, :story, 'Legacy plan', "
                    "'POLICY_ACTION', 'SUPPORTED', 'LOW', 0, '{}')"
                ),
                {"id": claim_id, "story": story_id},
            )
        command.upgrade(config, "head")
        command.check(config)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            legacy = session.get(Claim, claim_id)
            assert all(
                getattr(legacy, key) is None
                for key in (
                    "semantic_type",
                    "semantic_state",
                    "semantic_policy_version",
                    "semantic_ai_run_id",
                )
            )
            assert legacy.claim_type == "POLICY_ACTION" and legacy.status == "SUPPORTED"
        event, _, _ = _seed(factory)
        _, result = _run(factory, _service(QualityAI()), event)
        with factory() as session, session.begin():
            quality = session.get(ContentQualityCheck, result.quality_check_ids[0])
            quality.methodology_version = "quality-gate-methodology-v5"
            quality.claim_semantic_escalations = None
            quality_id = quality.id
            run_id = classification_run(session)
        for semantic_type in ClaimSemanticType:
            for state in ClaimSemanticState:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE claims SET semantic_type=:kind, semantic_state=:state, "
                            "semantic_policy_version='claim-semantics-policy-v1', "
                            "semantic_ai_run_id=:run WHERE id=:id"
                        ),
                        {
                            "id": claim_id,
                            "kind": semantic_type.value,
                            "state": state.value,
                            "run": run_id,
                        },
                    )
        for field, value in (
            ("semantic_type", "INVALID"),
            ("semantic_state", "TRUE"),
            ("semantic_ai_run_id", uuid4()),
        ):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(
                    text(f"UPDATE claims SET {field}=:value WHERE id=:id"),
                    {"value": value, "id": claim_id},
                )
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text("UPDATE claims SET semantic_state=NULL WHERE id=:id"), {"id": claim_id}
            )
        command.downgrade(config, "0014_certainty_firewall")
        assert schema(engine) == previous
        command.upgrade(config, "head")
        command.check(config)
        with factory() as session:
            legacy = session.get(Claim, claim_id)
            assert legacy.semantic_state is None and legacy.claim_type == "POLICY_ACTION"
            quality = session.get(ContentQualityCheck, quality_id)
            assert quality.claim_semantic_escalations is None
            assert quality.methodology_version == "quality-gate-methodology-v5"
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
