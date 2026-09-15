from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from news_ai_database import (
    Base,
    EventDeadLetter,
    EventProcessingAttempt,
    ProcessedEvent,
)
from news_ai_events import (
    DeferredWorkError,
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
    consumer: str = "worker-1"
    count: int = 10
    acked: list[str] = field(default_factory=list)
    before_ack: object | None = None
    stale_messages: list[StreamMessage] = field(default_factory=list)
    owned_pending: dict[str, StreamMessage] = field(default_factory=dict)
    stale_claims: list[tuple[int, str]] = field(default_factory=list)
    owned_claims: list[tuple[str, ...]] = field(default_factory=list)

    async def claim_stale(
        self, *, min_idle_ms: int, start_id: str = "0-0"
    ) -> tuple[str, list[StreamMessage]]:
        self.stale_claims.append((min_idle_ms, start_id))
        return "0-0", list(self.stale_messages)

    async def claim_owned_pending(self, message_ids) -> list[StreamMessage]:
        ids = tuple(message_ids)
        self.owned_claims.append(ids)
        return [self.owned_pending[message_id] for message_id in ids if message_id in self.owned_pending]

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


def _message(*, message_id: str = "1-0") -> StreamMessage:
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
    return StreamMessage("news:articles", message_id, event)


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


def test_every_configured_retry_delay_is_reachable_before_exhaustion() -> None:
    factory = _factory()
    consumer = FakeConsumer()
    clock = MutableClock()
    policy = WorkerRetryPolicy(jitter_ratio=0)
    runner = ReliableMessageProcessor(
        consumer,
        factory,
        consumer_group=consumer.group,
        handled_event_types=frozenset({EventType.ARTICLE_NORMALIZED}),
        retry_policy=policy,
        clock=clock,
    )
    message = _message()

    def fail(_event):
        raise RuntimeError("temporary failure")

    for attempt_number, delay in enumerate(policy.delays_seconds, start=1):
        result = asyncio.run(runner.process([message], fail))
        assert result.retrying == 1
        assert result.dead_lettered == 0
        with factory() as session:
            attempt = session.scalar(
                select(EventProcessingAttempt).where(
                    EventProcessingAttempt.attempt_number == attempt_number
                )
            )
            assert attempt is not None and attempt.next_retry_at is not None
            assert attempt.next_retry_at.replace(tzinfo=UTC) == clock.value + timedelta(
                seconds=delay
            )
        clock.value += timedelta(seconds=delay + 1)

    exhausted = asyncio.run(runner.process([message], fail))
    assert exhausted.dead_lettered == 1
    assert consumer.acked == [message.message_id]
    with factory() as session:
        dead = session.scalar(select(EventDeadLetter))
        assert dead is not None and dead.attempt_count == len(policy.delays_seconds) + 1


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
        assert attempt.next_retry_at.replace(tzinfo=UTC) == clock.value + timedelta(seconds=10)

    clock.value += timedelta(seconds=11)
    second = asyncio.run(runner.process([message], fail))
    assert second.retrying == 1
    assert second.dead_lettered == 0
    assert consumer.acked == []
    with factory() as session:
        attempts = list(
            session.scalars(
                select(EventProcessingAttempt).order_by(EventProcessingAttempt.attempt_number)
            )
        )
        assert attempts[1].next_retry_at.replace(tzinfo=UTC) == clock.value + timedelta(seconds=20)

    clock.value += timedelta(seconds=21)
    third = asyncio.run(runner.process([message], fail))
    assert third.dead_lettered == 1
    assert consumer.acked == ["1-0"]
    fourth = asyncio.run(runner.process([message], fail))
    assert fourth.dead_lettered == 1
    assert consumer.acked == ["1-0", "1-0"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EventDeadLetter)) == 1
        assert session.scalar(select(func.count()).select_from(EventProcessingAttempt)) == 3


def test_recovery_does_not_redeliver_before_durable_retry_time() -> None:
    factory = _factory()
    consumer = FakeConsumer()
    clock = MutableClock()
    runner = _runner(factory, consumer, clock)
    message = _message()
    consumer.owned_pending[message.message_id] = message

    def fail(_event):
        raise RuntimeError("temporary failure")

    assert asyncio.run(runner.process([message], fail)).retrying == 1
    _, recovered = asyncio.run(
        runner.recover(
            lambda _event: ProcessingOutcome.PROCESSED,
            min_idle_ms=900_000,
        )
    )

    assert recovered.received == 0
    assert consumer.owned_claims == []
    assert consumer.acked == []


def test_recovery_claims_same_owner_retry_when_durable_schedule_is_due() -> None:
    factory = _factory()
    consumer = FakeConsumer()
    clock = MutableClock()
    runner = _runner(factory, consumer, clock)
    message = _message()
    consumer.owned_pending[message.message_id] = message

    def fail(_event):
        raise RuntimeError("temporary failure")

    assert asyncio.run(runner.process([message], fail)).retrying == 1
    clock.value += timedelta(seconds=11)
    cursor, recovered = asyncio.run(
        runner.recover(
            lambda _event: ProcessingOutcome.PROCESSED,
            min_idle_ms=900_000,
        )
    )

    assert cursor == "0-0"
    assert recovered.received == 1
    assert recovered.processed == 1
    assert consumer.stale_claims == [(900_000, "0-0")]
    assert consumer.owned_claims == [(message.message_id,)]
    assert consumer.acked == [message.message_id]


def test_recovery_uses_only_latest_attempt_schedule() -> None:
    factory = _factory()
    consumer = FakeConsumer()
    clock = MutableClock()
    runner = _runner(factory, consumer, clock, delays=(10, 20))
    message = _message()
    consumer.owned_pending[message.message_id] = message

    def fail(_event):
        raise RuntimeError("temporary failure")

    assert asyncio.run(runner.process([message], fail)).retrying == 1
    clock.value += timedelta(seconds=11)
    assert asyncio.run(runner.process([message], fail)).retrying == 1
    clock.value += timedelta(seconds=10)

    _, recovered = asyncio.run(
        runner.recover(
            lambda _event: ProcessingOutcome.PROCESSED,
            min_idle_ms=900_000,
        )
    )

    assert recovered.received == 0
    assert consumer.owned_claims == []


def test_recovery_excludes_completed_events_even_if_retry_attempt_remains() -> None:
    factory = _factory()
    consumer = FakeConsumer()
    clock = MutableClock()
    runner = _runner(factory, consumer, clock)
    message = _message()
    consumer.owned_pending[message.message_id] = message

    def fail(_event):
        raise RuntimeError("temporary failure")

    assert asyncio.run(runner.process([message], fail)).retrying == 1
    with factory() as session, session.begin():
        session.add(
            ProcessedEvent(
                event_id=message.event.event_id,
                consumer_group=consumer.group,
                result={"completed": True},
            )
        )
    clock.value += timedelta(seconds=11)

    _, recovered = asyncio.run(
        runner.recover(
            lambda _event: ProcessingOutcome.PROCESSED,
            min_idle_ms=900_000,
        )
    )

    assert recovered.received == 0
    assert consumer.owned_claims == []


def test_recovery_does_not_process_same_message_twice_in_one_cycle() -> None:
    factory = _factory()
    consumer = FakeConsumer()
    clock = MutableClock()
    runner = _runner(factory, consumer, clock)
    message = _message()

    def fail(_event):
        raise RuntimeError("temporary failure")

    assert asyncio.run(runner.process([message], fail)).retrying == 1
    clock.value += timedelta(seconds=11)
    consumer.stale_messages = [message]
    consumer.owned_pending[message.message_id] = message
    calls = 0

    def defer(_event):
        nonlocal calls
        calls += 1
        raise DeferredWorkError("not yet executable")

    _, recovered = asyncio.run(runner.recover(defer, min_idle_ms=900_000))

    assert recovered.received == 1
    assert recovered.retrying == 1
    assert calls == 1
    assert consumer.owned_claims == []


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
        raw_event=(
            '{"event_id":"76edbfab-6b2e-47eb-b16f-091734035728",'
            '"event_type":"article.normalized",'
            '"authorization":"Bearer top-secret",'
            '"nested":{"api_key":"sk_abcdefghijklmnop",'
            '"note":"token=another-secret"}}'
        ),
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
        assert str(dead.event_id) == "76edbfab-6b2e-47eb-b16f-091734035728"
        assert dead.event_type == "article.normalized"
        assert dead.raw_event_hash is not None and len(dead.raw_event_hash) == 64
        assert "top-secret" not in dead.raw_event
        assert "abcdefghijklmnop" not in dead.raw_event
        assert "another-secret" not in dead.raw_event
        assert dead.event_payload is not None
        assert dead.event_payload["authorization"] == "[REDACTED]"
        assert dead.event_payload["nested"]["api_key"] == "[REDACTED]"


def test_malformed_dead_letter_diagnostics_redact_embedded_secrets_and_are_bounded() -> None:
    factory = _factory()
    consumer = FakeConsumer()
    runner = _runner(factory, consumer, MutableClock())
    raw = "malformed authorization=Bearer-secret password=hunter2 " + ("x" * 20_000)
    message = StreamMessage(
        "news:articles", "bad-2", None, raw_event=raw, decode_error="invalid envelope"
    )

    result = asyncio.run(runner.process([message], lambda _event: ProcessingOutcome.PROCESSED))

    assert result.dead_lettered == 1
    with factory() as session:
        dead = session.scalar(select(EventDeadLetter))
        assert dead is not None
        assert "Bearer-secret" not in dead.raw_event
        assert "hunter2" not in dead.raw_event
        assert len(dead.raw_event) <= 16_384
        assert dead.raw_event_hash == hashlib.sha256(raw.encode()).hexdigest()


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
