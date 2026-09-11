from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from news_ai_database import (
    Claim,
    EventOutbox,
    FactCheck,
    FactSheet,
    Job,
    ResearchRunClaim,
    Story,
)
from news_ai_domain import ClaimVerificationStatus, FactCheckLabel, ReviewState, RiskLevel
from news_ai_events import EventEnvelope, EventType
from news_ai_events.outbox import build_outbox_record
from news_ai_evidence import (
    EvidenceEngine,
    SearchBudgets,
    SearchPolicy,
    SearchProviderRegistry,
    SearchRules,
)
from sqlalchemy import create_engine, delete, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.getenv("NEWS_AI_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="PostgreSQL integration dependency is not configured",
)


def _factory() -> sessionmaker[Session]:
    assert DATABASE_URL is not None
    return sessionmaker(create_engine(DATABASE_URL, pool_pre_ping=True), expire_on_commit=False)


def _engine() -> EvidenceEngine:
    policy = SearchPolicy(
        schema_version=1,
        rules=SearchRules(),
        budgets=SearchBudgets(
            default_timeout_seconds=90,
            breaking_news_timeout_seconds=45,
        ),
    )
    return EvidenceEngine(
        SearchProviderRegistry([]),
        policy,
        lambda _request, compatible: compatible[0],
        object(),  # Collection is outside this planning-only concurrency test.
    )


def _claims_event(story_id, claim_id) -> EventEnvelope:
    return EventEnvelope(
        event_type=EventType.CLAIMS_EXTRACTED,
        producer="ai-worker",
        producer_version="0.1.0",
        aggregate_type="story",
        aggregate_id=story_id,
        idempotency_key=f"claims.extracted:{uuid4()}",
        payload={
            "story_id": str(story_id),
            "claim_ids": [str(claim_id)],
            "ai_run_id": str(uuid4()),
            "model_id": str(uuid4()),
        },
    )


def _delete_rows(factory: sessionmaker[Session], *, story_id) -> None:
    with factory() as session, session.begin():
        session.execute(
            text(
                "UPDATE claims SET current_research_run_id=NULL, "
                "current_fact_check_id=NULL WHERE story_id=:story_id"
            ),
            {"story_id": story_id},
        )
        job_ids = tuple(
            session.scalars(
                select(Job.id).where(Job.payload["plan"]["story_id"].as_string() == str(story_id))
            )
        )
        if job_ids:
            session.execute(delete(EventOutbox).where(EventOutbox.aggregate_id.in_(job_ids)))
            session.execute(
                delete(ResearchRunClaim).where(ResearchRunClaim.research_run_id.in_(job_ids))
            )
            session.execute(delete(Job).where(Job.id.in_(job_ids)))
        session.execute(delete(EventOutbox).where(EventOutbox.aggregate_id == story_id))
        session.execute(delete(FactSheet).where(FactSheet.story_id == story_id))
        session.execute(delete(FactCheck).where(FactCheck.story_id == story_id))
        session.execute(delete(Claim).where(Claim.story_id == story_id))
        session.execute(delete(Story).where(Story.id == story_id))


def test_postgresql_rejects_invalid_canonical_enum_values() -> None:
    factory = _factory()
    with factory() as session, session.begin():
        story = Story(
            canonical_headline="Enum constraint test",
            status="DISCOVERED",
            risk_level=RiskLevel.LOW,
            story_metadata={},
        )
        session.add(story)
        session.flush()
        claim = Claim(
            story_id=story.id,
            claim_text="Constraint claim",
            status=ClaimVerificationStatus.UNASSESSED,
            risk_level=RiskLevel.LOW,
            claim_metadata={},
        )
        session.add(claim)
        session.flush()
        check = FactCheck(
            story_id=story.id,
            claim_id=claim.id,
            label=FactCheckLabel.UNVERIFIED,
            review_required=True,
            review_state=ReviewState.NOT_READY,
        )
        sheet = FactSheet(
            story_id=story.id,
            version=1,
            headline="Snapshot",
            summary="Snapshot",
            risk_level=RiskLevel.LOW,
        )
        session.add_all([check, sheet])
        session.flush()
        event = EventEnvelope(
            event_type=EventType.FACT_CHECK_COMPLETED,
            producer="research-worker",
            producer_version="0.1.0",
            aggregate_type="story",
            aggregate_id=story.id,
            idempotency_key=f"fact_check.completed:{check.id}",
            payload={
                "story_id": str(story.id),
                "fact_check_id": str(check.id),
                "label": "UNVERIFIED",
                "confidence_score": None,
                "review_required": True,
            },
        )
        outbox = build_outbox_record(event)
        session.add(outbox)
        session.flush()
        story_id = story.id
        claim_id = claim.id
        check_id = check.id
        sheet_id = sheet.id
        outbox_id = outbox.id

    with factory() as session, session.begin():
        for value in RiskLevel:
            session.execute(
                text("UPDATE stories SET risk_level=:value WHERE id=:id"),
                {"value": value.value, "id": story_id},
            )
            session.execute(
                text("UPDATE claims SET risk_level=:value WHERE id=:id"),
                {"value": value.value, "id": claim_id},
            )
            session.execute(
                text("UPDATE fact_sheets SET risk_level=:value WHERE id=:id"),
                {"value": value.value, "id": sheet_id},
            )
        for value in ClaimVerificationStatus:
            session.execute(
                text("UPDATE claims SET status=:value WHERE id=:id"),
                {"value": value.value, "id": claim_id},
            )
        for value in FactCheckLabel:
            session.execute(
                text("UPDATE fact_checks SET label=:value WHERE id=:id"),
                {"value": value.value, "id": check_id},
            )
        for value in ReviewState:
            session.execute(
                text("UPDATE fact_checks SET review_state=:value WHERE id=:id"),
                {"value": value.value, "id": check_id},
            )
        for value in ("PENDING", "PUBLISHING", "PUBLISHED", "FAILED"):
            session.execute(
                text("UPDATE event_outbox SET status=:value WHERE id=:id"),
                {"value": value, "id": outbox_id},
            )

    statements = (
        ("UPDATE stories SET risk_level='INVALID' WHERE id=:id", story_id),
        ("UPDATE claims SET status='INVALID' WHERE id=:id", claim_id),
        ("UPDATE claims SET risk_level='INVALID' WHERE id=:id", claim_id),
        ("UPDATE fact_checks SET label='INVALID' WHERE id=:id", check_id),
        ("UPDATE fact_checks SET review_state='INVALID' WHERE id=:id", check_id),
        ("UPDATE fact_sheets SET risk_level='INVALID' WHERE id=:id", sheet_id),
        ("UPDATE event_outbox SET status='INVALID' WHERE id=:id", outbox_id),
    )
    try:
        for statement, row_id in statements:
            with factory() as session, pytest.raises(IntegrityError), session.begin():
                session.execute(text(statement), {"id": row_id})
    finally:
        _delete_rows(factory, story_id=story_id)


def test_concurrent_research_planning_persists_one_semantic_operation() -> None:
    factory = _factory()
    engine = _engine()
    with factory() as session, session.begin():
        story = Story(
            canonical_headline="Concurrent planning",
            status="DISCOVERED",
            risk_level=RiskLevel.LOW,
            story_metadata={},
        )
        session.add(story)
        session.flush()
        claim = Claim(
            story_id=story.id,
            claim_text="One logical claim",
            normalized_claim="one logical claim",
            status=ClaimVerificationStatus.UNASSESSED,
            risk_level=RiskLevel.LOW,
            confidence_score=Decimal("0.8"),
            claim_metadata={},
        )
        session.add(claim)
        session.flush()
        story_id = story.id
        claim_id = claim.id

    ai_run_id = uuid4()
    model_id = uuid4()

    def run_once() -> tuple[bool, object, object]:
        event = _claims_event(story_id, claim_id)
        event.payload["ai_run_id"] = str(ai_run_id)
        event.payload["model_id"] = str(model_id)
        with factory() as session, session.begin():
            result = engine.request_research(session, event)
        return result.created, result.research_run_id, result.event_id

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _index: run_once(), range(2)))

        with factory() as session:
            jobs = list(session.scalars(select(Job).where(Job.semantic_key.is_not(None))))
            job_ids = tuple(job.id for job in jobs)
            outbox_count = len(
                list(
                    session.scalars(
                        select(EventOutbox).where(EventOutbox.aggregate_id.in_(job_ids))
                    )
                )
            )
        assert sorted(result[0] for result in results) == [False, True]
        assert len({result[1] for result in results}) == 1
        assert len({result[2] for result in results}) == 1
        assert len(jobs) == 1
        assert outbox_count == 1
    finally:
        _delete_rows(factory, story_id=story_id)


def test_concurrent_distinct_research_operations_assign_monotonic_generations() -> None:
    factory = _factory()
    engine = _engine()
    with factory() as session, session.begin():
        story = Story(
            canonical_headline="Concurrent generations",
            status="DISCOVERED",
            risk_level=RiskLevel.LOW,
            story_metadata={},
        )
        session.add(story)
        session.flush()
        claim = Claim(
            story_id=story.id,
            claim_text="One evolving claim",
            status=ClaimVerificationStatus.UNASSESSED,
            risk_level=RiskLevel.LOW,
            claim_metadata={},
        )
        session.add(claim)
        session.flush()
        story_id, claim_id = story.id, claim.id

    def run_once(_index: int):
        event = _claims_event(story_id, claim_id)
        with factory() as session, session.begin():
            return engine.request_research(session, event)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(run_once, range(2)))
        with factory() as session:
            stored = session.get(Claim, claim_id)
            mappings = list(
                session.scalars(
                    select(ResearchRunClaim)
                    .where(ResearchRunClaim.claim_id == claim_id)
                    .order_by(ResearchRunClaim.research_generation)
                )
            )
            assert stored is not None and stored.research_generation == 2
            assert [row.research_generation for row in mappings] == [1, 2]
            assert stored.current_research_run_id == mappings[-1].research_run_id
            assert {row.research_run_id for row in mappings} == {
                result.research_run_id for result in results
            }
    finally:
        _delete_rows(factory, story_id=story_id)


def test_migration_upgrade_downgrade_reupgrade_round_trip() -> None:
    assert DATABASE_URL is not None
    base_url = make_url(DATABASE_URL)
    database_name = f"news_ai_migration_{uuid4().hex}"
    test_url = base_url.set(database=database_name)
    admin_engine = create_engine(
        base_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))

    previous_url = os.environ.get("NEWS_AI_DATABASE_URL")
    os.environ["NEWS_AI_DATABASE_URL"] = test_url.render_as_string(hide_password=False)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        command.check(config)
        migrated_engine = create_engine(test_url)
        try:
            assert {
                "research_run_id",
                "research_generation",
                "methodology_version",
                "semantic_key",
            } <= {column["name"] for column in inspect(migrated_engine).get_columns("fact_checks")}
            assert {
                "article_discoveries",
                "research_run_claims",
                "event_processing_attempts",
                "event_dead_letters",
            } <= set(inspect(migrated_engine).get_table_names())
        finally:
            migrated_engine.dispose()

        command.downgrade(config, "base")
        command.upgrade(config, "head")
        reupgraded_engine = create_engine(test_url)
        try:
            assert {"primary_evidence_count", "research_generation"} <= {
                column["name"] for column in inspect(reupgraded_engine).get_columns("fact_checks")
            }
        finally:
            reupgraded_engine.dispose()
    finally:
        if previous_url is None:
            os.environ.pop("NEWS_AI_DATABASE_URL", None)
        else:
            os.environ["NEWS_AI_DATABASE_URL"] = previous_url
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        admin_engine.dispose()
