from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from news_ai_database import (
    AuditLog,
    Base,
    ContentDraft,
    ContentQualityCheck,
    ContentVariant,
    EventOutbox,
    FactSheet,
    ReviewDecisionRecord,
    Story,
)
from news_ai_domain import ReviewState
from news_ai_events import EventEnvelope, EventType
from news_ai_events.outbox import build_outbox_record
from news_ai_quality import QUALITY_METHODOLOGY_VERSION
from news_ai_review import (
    ApprovalEligibilityService,
    ReviewConflictError,
    ReviewPreconditionError,
    ReviewService,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.orm import Session, sessionmaker
from unit.quality import test_quality_gate as quality_fixtures
from unit.review.test_review_service import _policy, _principal, decide, seed_reviewable

DATABASE_URL = os.getenv("NEWS_AI_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="PostgreSQL integration dependency is not configured",
)


@pytest.fixture
def postgres_factory() -> sessionmaker[Session]:
    assert DATABASE_URL is not None
    if os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("destructive review isolation is restricted to the test environment")
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as connection:
        tables = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
        connection.exec_driver_sql(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_two_reviewers_cannot_commit_competing_terminal_decisions(
    postgres_factory: sessionmaker[Session],
) -> None:
    variant_id = seed_reviewable(postgres_factory)[0]
    service = ReviewService(postgres_factory, _policy())
    barrier = Barrier(2)

    def act(decision: ReviewState, reason: str | None, key: str):
        barrier.wait()
        try:
            return decide(service, variant_id, _principal(), decision, reason=reason, key=key)
        except ReviewConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        approved = executor.submit(act, ReviewState.APPROVED, None, "approve-race")
        rejected = executor.submit(act, ReviewState.REJECTED, "Competing decision", "reject-race")
        outcomes = (approved.result(timeout=15), rejected.result(timeout=15))

    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, ReviewConflictError) for item in outcomes) == 1
    with postgres_factory() as session:
        decisions = tuple(session.scalars(select(ReviewDecisionRecord)))
        audits = tuple(session.scalars(select(AuditLog)))
        assert len(decisions) == 1
        assert len(audits) == 1
        assert session.get(ContentVariant, variant_id).review_state is decisions[0].decision


def test_concurrent_identical_ui_retries_return_one_decision(
    postgres_factory: sessionmaker[Session],
) -> None:
    variant_id = seed_reviewable(postgres_factory)[0]
    service = ReviewService(postgres_factory, _policy())
    principal = _principal()
    barrier = Barrier(2)

    def approve():
        barrier.wait()
        return decide(service, variant_id, principal, key="same-ui-click")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(approve)
        second = executor.submit(approve)
        results = (first.result(timeout=15), second.result(timeout=15))

    assert results[0].review_decision_id == results[1].review_decision_id
    with postgres_factory() as session:
        assert len(tuple(session.scalars(select(ReviewDecisionRecord)))) == 1
        assert len(tuple(session.scalars(select(AuditLog)))) == 1


def test_fact_sheet_correction_serializes_before_delayed_approval(
    postgres_factory: sessionmaker[Session],
) -> None:
    variant_id = seed_reviewable(postgres_factory)[0]
    service = ReviewService(postgres_factory, _policy())
    started = Event()

    blocker = postgres_factory()
    transaction = blocker.begin()
    variant = blocker.get(ContentVariant, variant_id)
    draft = blocker.get(ContentDraft, variant.content_draft_id)
    story = blocker.scalar(select(Story).where(Story.id == draft.story_id).with_for_update())
    old_sheet = blocker.get(FactSheet, draft.fact_sheet_id)
    blocker.add(
        FactSheet(
            story_id=story.id,
            version=2,
            headline="Corrected factual record",
            summary="A later correction supersedes the reviewed basis.",
            risk_level=old_sheet.risk_level,
            sensitive_topics=old_sheet.sensitive_topics,
            semantic_key=f"correction:{uuid4()}",
        )
    )

    def approve_after_start():
        started.set()
        try:
            return decide(service, variant_id, _principal(), key="fact-sheet-race")
        except ReviewPreconditionError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(approve_after_start)
        assert started.wait(timeout=5)
        transaction.commit()
        blocker.close()
        outcome = future.result(timeout=15)

    assert isinstance(outcome, ReviewPreconditionError)
    with postgres_factory() as session:
        assert session.scalar(select(ReviewDecisionRecord)) is None
        assert session.get(ContentVariant, variant_id).review_state is ReviewState.READY_FOR_REVIEW


def test_hashless_v1_quality_is_recovered_by_idempotent_v2_reassessment(
    postgres_factory: sessionmaker[Session],
) -> None:
    event, draft_id, variant_id = quality_fixtures._seed(postgres_factory)
    with postgres_factory() as session, session.begin():
        draft = session.get(ContentDraft, draft_id)
        variant = session.get(ContentVariant, variant_id)
        draft.review_state = ReviewState.READY_FOR_REVIEW
        variant.review_state = ReviewState.READY_FOR_REVIEW
        legacy = ContentQualityCheck(
            content_draft_id=draft_id,
            content_variant_id=variant_id,
            content_variant_version=1,
            fact_sheet_id=draft.fact_sheet_id,
            fact_sheet_version=draft.fact_sheet_version,
            methodology_version="quality-gate-methodology-v1",
            content_artifact_hash=None,
            factual_accuracy_passed=True,
            source_alignment_passed=True,
            citation_alignment_passed=True,
            style_passed=True,
            unsupported_claims=[],
            fabricated_quotes=[],
            incorrect_names=[],
            incorrect_dates=[],
            incorrect_numbers=[],
            missing_context=[],
            defamation_risk=False,
            sensitive_topic_error=False,
            passed=True,
            review_required=True,
            notes=[],
            ai_run_id=None,
            semantic_key=f"legacy-quality-v1:{variant_id}",
        )
        session.add(legacy)
        session.add(
            build_outbox_record(
                EventEnvelope(
                    event_type=EventType.CONTENT_QUALITY_CHECKED,
                    producer="ai-worker",
                    producer_version="0.1.0",
                    aggregate_type="content_draft",
                    aggregate_id=draft_id,
                    correlation_id=event.correlation_id,
                    causation_id=event.event_id,
                    idempotency_key=(
                        f"content.quality_checked:{draft_id}:1:quality-gate-methodology-v1:legacy"
                    ),
                    payload={
                        "content_draft_id": str(draft_id),
                        "passed": True,
                        "fact_check_passed": True,
                        "source_check_passed": True,
                        "style_check_passed": True,
                        "risk_level": draft.risk_level.value,
                        "review_required": True,
                    },
                )
            )
        )
        session.flush()
        legacy_id = legacy.id

    review = ReviewService(postgres_factory, _policy())
    with pytest.raises(ReviewPreconditionError):
        decide(review, variant_id, _principal(), key="legacy-cannot-approve")

    ai = quality_fixtures.QualityAI()
    service = quality_fixtures._service(ai)
    context, first = quality_fixtures._run(postgres_factory, service, event)
    replay_event = event.model_copy(
        update={"event_id": uuid4(), "idempotency_key": f"quality-replay:{uuid4()}"}
    )
    with postgres_factory() as session:
        replay_context = service.load_context(session, replay_event)
        replay = service.existing_result(session, replay_context)
        checks = tuple(
            session.scalars(select(ContentQualityCheck).order_by(ContentQualityCheck.created_at))
        )
        quality_events = tuple(
            session.scalars(
                select(EventOutbox).where(
                    EventOutbox.event_type == EventType.CONTENT_QUALITY_CHECKED.value
                )
            )
        )
        assert session.get(ContentQualityCheck, legacy_id).content_artifact_hash is None
        assert [item.methodology_version for item in checks] == [
            "quality-gate-methodology-v1",
            QUALITY_METHODOLOGY_VERSION,
        ]
        assert checks[1].content_artifact_hash == context.variants[0].artifact_hash
        assert len(quality_events) == 2
        v2_events = [
            item for item in quality_events if QUALITY_METHODOLOGY_VERSION in item.idempotency_key
        ]
        assert len(v2_events) == 1
        assert replay is not None and replay.event_id == first.event_id
        v2_check_id = checks[1].id
    assert ai.calls == 1

    decide(review, variant_id, _principal(), key="approve-after-v2")
    assert ApprovalEligibilityService(postgres_factory, _policy()).is_exact_version_approved(
        variant_id
    )
    with postgres_factory() as session:
        assert session.scalar(select(ReviewDecisionRecord)).quality_check_id == v2_check_id


def test_review_reloads_variant_after_waiting_for_story_lock(
    postgres_factory: sessionmaker[Session],
) -> None:
    variant_id = seed_reviewable(postgres_factory)[0]
    service = ReviewService(postgres_factory, _policy())
    engine = postgres_factory.kw["bind"]
    routing_read = Event()
    review_thread: dict[str, int] = {}

    blocker = postgres_factory()
    transaction = blocker.begin()
    variant = blocker.get(ContentVariant, variant_id)
    draft = blocker.get(ContentDraft, variant.content_draft_id)
    blocker.scalar(select(Story).where(Story.id == draft.story_id).with_for_update())
    variant.version = 2
    variant.review_state = ReviewState.NOT_READY
    blocker.flush()

    def observe_routing(_conn, _cursor, statement, _parameters, _context, _many):
        if (
            threading.get_ident() == review_thread.get("id")
            and "content_variants.content_draft_id" in statement
        ):
            routing_read.set()

    def approve_old_version():
        review_thread["id"] = threading.get_ident()
        try:
            return decide(service, variant_id, _principal(), key="post-lock-freshness")
        except ReviewConflictError as exc:
            return exc

    sqlalchemy_event.listen(engine, "before_cursor_execute", observe_routing)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(approve_old_version)
            assert routing_read.wait(timeout=5)
            transaction.commit()
            blocker.close()
            outcome = future.result(timeout=15)
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", observe_routing)
        if blocker.is_active:
            blocker.close()

    assert isinstance(outcome, ReviewConflictError)
    with postgres_factory() as session:
        assert session.scalar(select(func.count()).select_from(ReviewDecisionRecord)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0
        current = session.get(ContentVariant, variant_id)
        assert current.version == 2
        assert current.review_state is ReviewState.NOT_READY
