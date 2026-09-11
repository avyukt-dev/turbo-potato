from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from news_ai_database import (
    Base,
    EventDeadLetter,
    EventProcessingAttempt,
)
from news_ai_events import (
    EventEnvelope,
    EventType,
    ProcessingOutcome,
    ReliableMessageProcessor,
    StaleWorkError,
    StreamMessage,
    WorkerRetryPolicy,
    load_worker_retry_policy,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


@dataclass
class FakeConsumer:
    stream: str = "news:articles"
    group: str = "reliability-test"
    acked: list[str] = field(default_factory=list)
    before_ack: object | None = None

    async def ack(self, message: StreamMessage) -> None:
        if callable(self.before_ack):
            self.before_ack()
        self.acked.append(message.message_id)


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 11, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _message() -> StreamMessage:
    article_id = "76edbfab-6b2e-47eb-b16f-091734035728"
    event = EventEnvelope(
        event_type=EventType.ARTICLE_NORMALIZED,
        producer="processor",
        producer_version="0.1.0",
        aggregate_type="article",
        aggregate_id=article_id,
        idempotency_key="article.normalized:test",
        payload={
            "article_id": article_id,
            "article_version_id": "70874bad-5f92-4776-b97a-fb51c275f2cb",
            "content_hash": "a" * 64,
            "language": "en",
            "title": "Headline",
        },
    )
    return StreamMessage("news:articles", "1-0", event)


def _runner(factory, consumer, clock, *, delays=(10, 20)):
    return ReliableMessageProcessor(
        consumer,
        factory,
        consumer_group=consumer.group,
        handled_event_types=frozenset({EventType.ARTICLE_NORMALIZED}),
        retry_policy=WorkerRetryPolicy(delays_seconds=delays, jitter_ratio=0),
        clock=clock,
    )


def test_retry_policy_loads_from_typed_runtime_configuration() -> None:
    policy = load_worker_retry_policy()
    assert policy.delays_seconds == (30, 120, 600, 1800, 7200)
    assert policy.jitter_ratio == 0.2


def test_transient_failure_remains_pending_below_budget_then_dead_letters_once() -> None:
    factory = _factory()
    consumer = FakeConsumer()
    clock = MutableClock()
    runner = _runner(factory, consumer, clock)
    message = _message()

    def fail(_event):
        raise RuntimeError("provider temporarily unavailable; token=secret-value")

    first = asyncio.run(runner.process([message], fail))
    assert first.retrying == 1
    assert first.dead_lettered == 0
    assert consumer.acked == []
    with factory() as session:
        attempt = session.scalar(select(EventProcessingAttempt))
        assert attempt is not None
        assert attempt.error_message.endswith("token=[REDACTED]")

    clock.value += timedelta(seconds=11)
    second = asyncio.run(runner.process([message], fail))
    assert second.dead_lettered == 1
    assert consumer.acked == ["1-0"]
    third = asyncio.run(runner.process([message], fail))
    assert third.dead_lettered == 1
    assert consumer.acked == ["1-0", "1-0"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EventDeadLetter)) == 1
        assert session.scalar(select(func.count()).select_from(EventProcessingAttempt)) == 2


def test_permanent_invalid_event_is_persisted_before_ack() -> None:
    factory = _factory()

    def assert_dead_letter_exists() -> None:
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(EventDeadLetter)) == 1

    consumer = FakeConsumer(before_ack=assert_dead_letter_exists)
    runner = _runner(factory, consumer, MutableClock())
    invalid = StreamMessage(
        "news:articles",
        "bad-1",
        None,
        raw_event='{"event_id":"not-a-uuid","event_type":"article.normalized"}',
        decode_error="invalid envelope",
    )
    result = asyncio.run(runner.process([invalid], lambda _event: ProcessingOutcome.PROCESSED))

    assert result.dead_lettered == 1
    assert result.retrying == 0
    assert consumer.acked == ["bad-1"]
    with factory() as session:
        dead = session.scalar(select(EventDeadLetter))
        assert dead is not None
        assert dead.message_id == "bad-1"
        assert dead.raw_event == invalid.raw_event


def test_stale_work_is_classified_once_and_acked_without_retry() -> None:
    factory = _factory()
    consumer = FakeConsumer()
    runner = _runner(factory, consumer, MutableClock())
    message = _message()

    def stale(_event):
        raise StaleWorkError("superseded generation")

    first = asyncio.run(runner.process([message], stale))
    second = asyncio.run(runner.process([message], stale))
    assert first.stale == 1
    assert second.stale == 1
    assert consumer.acked == ["1-0", "1-0"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EventProcessingAttempt)) == 1
        assert session.scalar(select(func.count()).select_from(EventDeadLetter)) == 0


def test_database_failure_before_durable_failure_handling_does_not_ack() -> None:
    consumer = FakeConsumer()

    def broken_factory():
        raise RuntimeError("database unavailable")

    runner = _runner(broken_factory, consumer, MutableClock())
    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(runner.process([_message()], lambda _event: ProcessingOutcome.PROCESSED))
    assert consumer.acked == []
