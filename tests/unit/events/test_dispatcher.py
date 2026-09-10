import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from news_ai_database import Base
from news_ai_database.models import EventOutbox, OutboxStatus
from news_ai_events import EventEnvelope, EventType
from news_ai_events.dispatcher import OutboxDispatcher, RetryPolicy
from news_ai_events.outbox import build_outbox_record
from sqlalchemy import create_engine
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
    event = EventEnvelope(
        event_type=EventType.ARTICLE_DISCOVERED,
        occurred_at=occurred_at,
        producer="collector",
        producer_version="0.1.0",
        aggregate_type="article",
        aggregate_id=uuid4(),
        idempotency_key="article:test",
    )
    record = build_outbox_record(event)
    with factory() as session, session.begin():
        session.add(record)
    return record


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
    dispatcher = OutboxDispatcher(factory, FakePublisher(fail=True))

    stats = asyncio.run(dispatcher.dispatch_once(now=datetime(2026, 9, 10, 2, 0, tzinfo=UTC)))

    assert stats.retried == 1
    with factory() as session:
        persisted = session.get(EventOutbox, record.id)
        assert persisted is not None
        assert persisted.status == OutboxStatus.PENDING
        assert persisted.attempt_count == 1
        assert persisted.next_attempt_at is not None
        assert "redis unavailable" in (persisted.last_error or "")


def test_retry_budget_exhaustion_marks_failed() -> None:
    factory = _factory()
    record = _insert_event(factory)
    dispatcher = OutboxDispatcher(
        factory,
        FakePublisher(fail=True),
        retry_policy=RetryPolicy(delays_seconds=(1,)),
    )

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
