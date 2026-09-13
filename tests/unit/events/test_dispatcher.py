import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from news_ai_database import Base
from news_ai_database.models import EventOutbox, OutboxStatus
from news_ai_events import EventEnvelope, EventType
from news_ai_events.dispatcher import OutboxDispatcher, RetryPolicy
from news_ai_events.outbox import build_outbox_record
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker


class FakePublisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[Any] = []

    async def publish(self, event: object) -> str:
        self.events.append(event)
        if self.fail:
            raise RuntimeError("redis unavailable")
        return "1-0"


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _insert_event(factory: sessionmaker[Session]) -> EventOutbox:
    occurred_at = datetime(2026, 9, 10, 1, 2, tzinfo=UTC)
    article_id = uuid4()
    event = EventEnvelope(
        event_type=EventType.ARTICLE_DISCOVERED,
        occurred_at=occurred_at,
        producer="collector",
        producer_version="0.1.0",
        aggregate_type="article",
        aggregate_id=article_id,
        idempotency_key="article:test",
        payload={
            "article_id": str(article_id),
            "source_id": str(uuid4()),
            "source_feed_id": str(uuid4()),
            "canonical_url": "https://example.com/article",
            "title": "Example",
            "published_at": None,
        },
    )
    record = build_outbox_record(event)
    with factory() as session, session.begin():
        session.add(record)
    return record


def test_retry_policy_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="positive"):
        RetryPolicy(delays_seconds=())
    with pytest.raises(ValueError, match="positive"):
        RetryPolicy(delays_seconds=(30, 0))
    with pytest.raises(ValueError, match="jitter_ratio"):
        RetryPolicy(jitter_ratio=1.1)


def test_retry_policy_applies_bounded_jitter() -> None:
    now = datetime(2026, 9, 10, 2, 0, tzinfo=UTC)
    policy = RetryPolicy(delays_seconds=(100,), jitter_ratio=0.2)

    retry_at = policy.next_attempt_at(1, now=now)

    assert now + timedelta(seconds=80) <= retry_at <= now + timedelta(seconds=120)


def test_dispatch_preserves_original_event_provenance() -> None:
    factory = _factory()
    record = _insert_event(factory)
    publisher = FakePublisher()
    dispatcher = OutboxDispatcher(factory, publisher)

    stats = asyncio.run(dispatcher.dispatch_once())

    assert stats.claimed == 1
    assert stats.published == 1
    emitted = publisher.events[0]
    assert isinstance(emitted, EventEnvelope)
    assert emitted.producer == "collector"
    assert emitted.producer_version == "0.1.0"
    assert emitted.occurred_at == datetime(2026, 9, 10, 1, 2, tzinfo=UTC)
    with factory() as session:
        persisted = session.get(EventOutbox, record.id)
        assert persisted is not None
        assert persisted.status == OutboxStatus.PUBLISHED
        assert persisted.published_at is not None


def test_failed_publish_is_retried_with_backoff() -> None:
    factory = _factory()
    record = _insert_event(factory)
    now = datetime(2026, 9, 10, 2, 0, tzinfo=UTC)
    dispatcher = OutboxDispatcher(
        factory,
        FakePublisher(fail=True),
        retry_policy=RetryPolicy(jitter_ratio=0),
    )

    stats = asyncio.run(dispatcher.dispatch_once(now=now))

    assert stats.retried == 1
    with factory() as session:
        persisted = session.get(EventOutbox, record.id)
        assert persisted is not None
        assert persisted.status == OutboxStatus.PENDING
        assert persisted.attempt_count == 1
        assert persisted.next_attempt_at is not None
        persisted_retry_at = persisted.next_attempt_at
        if persisted_retry_at.tzinfo is None or persisted_retry_at.utcoffset() is None:
            persisted_retry_at = persisted_retry_at.replace(tzinfo=UTC)
        assert persisted_retry_at >= now + timedelta(seconds=30)
        assert persisted.last_error == "OUTBOX_TRANSPORT_FAILED:INTERNAL"


def test_retry_budget_exhaustion_marks_failed() -> None:
    factory = _factory()
    record = _insert_event(factory)
    dispatcher = OutboxDispatcher(
        factory,
        FakePublisher(fail=True),
        retry_policy=RetryPolicy(delays_seconds=(1,), jitter_ratio=0),
    )

    first = asyncio.run(dispatcher.dispatch_once())
    assert first.retried == 1
    with factory() as session, session.begin():
        persisted = session.get(EventOutbox, record.id)
        assert persisted is not None
        persisted.next_attempt_at = None

    stats = asyncio.run(dispatcher.dispatch_once())

    assert stats.failed == 1
    with factory() as session:
        persisted = session.get(EventOutbox, record.id)
        assert persisted is not None
        assert persisted.status == OutboxStatus.FAILED


def test_stale_publishing_lease_is_recovered() -> None:
    factory = _factory()
    record = _insert_event(factory)
    with factory() as session, session.begin():
        persisted = session.get(EventOutbox, record.id)
        assert persisted is not None
        persisted.status = OutboxStatus.PUBLISHING
        persisted.attempt_count = 1
        persisted.publishing_started_at = datetime.now(UTC) - timedelta(minutes=10)

    publisher = FakePublisher()
    dispatcher = OutboxDispatcher(factory, publisher, publishing_lease_seconds=60)
    stats = asyncio.run(dispatcher.dispatch_once())

    assert stats.recovered == 1
    assert stats.published == 1


SECRET = "SUPER_SECRET_OUTBOX_TOKEN_123"


class RaisingPublisher:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.events: list[object] = []

    async def publish(self, event: object) -> str:
        self.events.append(event)
        raise self.error


@pytest.mark.parametrize(
    "message",
    [
        f"Authorization: Bearer {SECRET}",
        f"authorization=Bearer {SECRET}",
        f"access_token={SECRET}",
        f"api_key={SECRET}",
        f"password={SECRET}",
        f"client_secret={SECRET}",
        f"https://user:{SECRET}@example.invalid/path",
        "A" * 100_000,
        "secret-one",
        "secret-two",
    ],
    ids=[
        "header",
        "authorization",
        "token",
        "key",
        "password",
        "client",
        "url",
        "oversize",
        "message-one",
        "message-two",
    ],
)
def test_transport_diagnostics_never_render_messages(message, caplog) -> None:
    factory = _factory()
    record = _insert_event(factory)
    publisher = RaisingPublisher(RuntimeError(message))
    dispatcher = OutboxDispatcher(
        factory, publisher, retry_policy=RetryPolicy(delays_seconds=(1,), jitter_ratio=0)
    )
    first = asyncio.run(dispatcher.dispatch_once())
    assert first.retried == 1
    with factory() as session, session.begin():
        row = session.get(EventOutbox, record.id)
        assert row.last_error == "OUTBOX_TRANSPORT_FAILED:INTERNAL"
        assert row.attempt_count == 1
        assert row.status == OutboxStatus.PENDING
        row.next_attempt_at = None
    exhausted = asyncio.run(dispatcher.dispatch_once())
    assert exhausted.failed == 1
    with factory() as session:
        row = session.get(EventOutbox, record.id)
        assert row.status == OutboxStatus.FAILED
        assert row.attempt_count == 2
        assert row.last_error == "OUTBOX_TRANSPORT_FAILED:INTERNAL"
        assert session.scalar(select(func.count()).select_from(EventOutbox)) == 1
        # The dispatcher has no DLQ/audit writes; inspect all other mapped tables.
        for table in Base.metadata.sorted_tables:
            if table.name != "event_outbox":
                assert session.scalar(select(func.count()).select_from(table)) == 0
    assert SECRET not in caplog.text + repr(first) + repr(exhausted)
    assert message not in caplog.text
    assert len(caplog.text) < 1000


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (TimeoutError(SECRET), "TIMEOUT"),
        (ConnectionError(SECRET), "CONNECTION"),
        (SQLAlchemyError(SECRET), "DATABASE"),
        (RuntimeError(SECRET), "INTERNAL"),
    ],
)
def test_type_mapping_is_reliability_neutral(error, category, caplog) -> None:
    factory = _factory()
    record = _insert_event(factory)
    stats = asyncio.run(OutboxDispatcher(factory, RaisingPublisher(error)).dispatch_once())
    assert stats.retried == 1
    with factory() as session:
        row = session.get(EventOutbox, record.id)
        assert row.last_error == f"OUTBOX_TRANSPORT_FAILED:{category}"
        assert row.status == OutboxStatus.PENDING
        assert row.attempt_count == 1
    assert SECRET not in caplog.text


def test_success_after_safe_failure_clears_diagnostic() -> None:
    factory = _factory()
    record = _insert_event(factory)
    failing = RaisingPublisher(RuntimeError(SECRET))
    dispatcher = OutboxDispatcher(factory, failing)
    assert asyncio.run(dispatcher.dispatch_once()).retried == 1
    with factory() as session, session.begin():
        row = session.get(EventOutbox, record.id)
        assert row.last_error == "OUTBOX_TRANSPORT_FAILED:INTERNAL"
        row.next_attempt_at = None
    succeeding = FakePublisher()
    dispatcher.publisher = succeeding
    assert asyncio.run(dispatcher.dispatch_once()).published == 1
    assert failing.events[0] == succeeding.events[0]
    with factory() as session:
        row = session.get(EventOutbox, record.id)
        assert row.status == OutboxStatus.PUBLISHED
        assert row.attempt_count == 2
        assert row.last_error is None


def test_result_commit_failure_is_safe_and_allows_duplicate_delivery(monkeypatch, caplog) -> None:
    factory = _factory()
    record = _insert_event(factory)
    publisher = FakePublisher()
    dispatcher = OutboxDispatcher(factory, publisher)
    original_commit = Session.commit

    # Fail the transaction's commit after mark_published, not transport publication.
    from sqlalchemy import event

    def fail_result_commit(session):
        if any(
            isinstance(row, EventOutbox) and row.status == OutboxStatus.PUBLISHED
            for row in session.identity_map.values()
        ):
            raise SQLAlchemyError(f"SQL parameters password={SECRET}")

    event.listen(Session, "before_commit", fail_result_commit)
    try:
        assert asyncio.run(dispatcher.dispatch_once()).retried == 1
    finally:
        event.remove(Session, "before_commit", fail_result_commit)
    assert Session.commit is original_commit
    with factory() as session, session.begin():
        row = session.get(EventOutbox, record.id)
        assert row.last_error == "OUTBOX_STATE_UPDATE_FAILED:DATABASE"
        assert row.status == OutboxStatus.PENDING
        assert row.attempt_count == 1
        row.next_attempt_at = None
    assert asyncio.run(dispatcher.dispatch_once()).published == 1
    assert len(publisher.events) == 2
    assert publisher.events[0] == publisher.events[1]
    assert SECRET not in caplog.text


def test_exception_rendering_and_cancellation_are_not_invoked() -> None:
    class UnrenderableError(Exception):
        def __str__(self):
            raise AssertionError("exception must not be rendered")

        def __repr__(self):
            raise AssertionError("exception must not be rendered")

    factory = _factory()
    _insert_event(factory)
    assert (
        asyncio.run(
            OutboxDispatcher(factory, RaisingPublisher(UnrenderableError())).dispatch_once()
        ).retried
        == 1
    )
    with factory() as session, session.begin():
        session.query(EventOutbox).delete()
    record = _insert_event(factory)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            OutboxDispatcher(factory, RaisingPublisher(asyncio.CancelledError())).dispatch_once()
        )
    with factory() as session:
        row = session.get(EventOutbox, record.id)
        assert row.status == OutboxStatus.PUBLISHING
        assert row.last_error is None
