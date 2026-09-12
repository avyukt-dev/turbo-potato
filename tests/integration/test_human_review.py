from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from news_ai_database import (
    AuditLog,
    Base,
    ContentDraft,
    ContentVariant,
    FactSheet,
    ReviewDecisionRecord,
    Story,
)
from news_ai_domain import ReviewState
from news_ai_review import ReviewConflictError, ReviewPreconditionError, ReviewService
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
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
