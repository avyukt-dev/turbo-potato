from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from news_ai_database import EventDeadLetter, EventProcessingAttempt, ProcessedEvent
from news_ai_events import (
    EventEnvelope,
    EventType,
    ProcessingOutcome,
    RedisStreamConsumer,
    ReliableMessageProcessor,
    WorkerRetryPolicy,
)
from news_ai_events.idempotency import mark_processed
from redis.asyncio import Redis
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("NEWS_AI_DATABASE_URL")
REDIS_URL = os.getenv("NEWS_AI_REDIS_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not REDIS_URL,
    reason="PostgreSQL and Redis integration dependencies are not configured",
)


def _event() -> EventEnvelope:
    article_id = uuid4()
    return EventEnvelope(
        event_type=EventType.ARTICLE_NORMALIZED,
        producer="processor",
        producer_version="0.1.0",
        aggregate_type="article",
        aggregate_id=article_id,
        idempotency_key=f"article.normalized:{article_id}:1",
        payload={
            "article_id": str(article_id),
            "article_version_id": str(uuid4()),
            "content_hash": "a" * 64,
            "language": "en",
            "title": "Reliability integration",
        },
    )


def test_redis_exhaustion_dead_letters_before_ack_and_stale_pending_is_reclaimed() -> None:
    assert DATABASE_URL is not None and REDIS_URL is not None
    asyncio.run(_scenario(DATABASE_URL, REDIS_URL))


async def _scenario(database_url: str, redis_url: str) -> None:
    factory = sessionmaker(create_engine(database_url), expire_on_commit=False)
    client = Redis.from_url(redis_url, decode_responses=True)
    suffix = uuid4().hex
    stream = f"test:reliability:{suffix}"
    group = f"test-group-{suffix}"
    first = RedisStreamConsumer(
        client,
        stream=stream,
        group=group,
        consumer="first",
        block_ms=50,
    )
    second = RedisStreamConsumer(
        client,
        stream=stream,
        group=group,
        consumer="second",
        block_ms=50,
    )
    dead_event = _event()
    pending_event = _event()
    try:
        await first.ensure_group()
        await client.xadd(stream, {"event": dead_event.model_dump_json()})
        messages = await first.read()
        assert len(messages) == 1
        runner = ReliableMessageProcessor(
            first,
            factory,
            consumer_group=group,
            handled_event_types=frozenset({EventType.ARTICLE_NORMALIZED}),
            retry_policy=WorkerRetryPolicy(delays_seconds=(1,), jitter_ratio=0),
            clock=lambda: datetime(2026, 9, 11, tzinfo=UTC),
        )

        def fail(_event):
            raise RuntimeError("transient integration failure")

        result = await runner.process(messages, fail)
        assert result.retrying == 1
        assert result.dead_lettered == 0
        assert (await client.xpending(stream, group))["pending"] == 1
        await asyncio.sleep(0.01)
        _, exhausted_messages = await second.claim_stale(min_idle_ms=1)
        exhaustion = ReliableMessageProcessor(
            second,
            factory,
            consumer_group=group,
            handled_event_types=frozenset({EventType.ARTICLE_NORMALIZED}),
            retry_policy=WorkerRetryPolicy(delays_seconds=(1,), jitter_ratio=0),
            clock=lambda: datetime(2026, 9, 11, tzinfo=UTC) + timedelta(seconds=2),
        )
        exhausted = await exhaustion.process(exhausted_messages, fail)
        assert exhausted.dead_lettered == 1
        assert (await client.xpending(stream, group))["pending"] == 0
        with factory() as session:
            dead = session.query(EventDeadLetter).filter_by(event_id=dead_event.event_id).one()
            assert dead.message_id == messages[0].message_id
            assert dead.aggregate_id == dead_event.aggregate_id
            assert dead.aggregate_type == "article"

        await client.xadd(stream, {"event": pending_event.model_dump_json()})
        abandoned = await first.read()
        assert len(abandoned) == 1
        await asyncio.sleep(0.01)
        _, reclaimed = await second.claim_stale(min_idle_ms=1)
        assert [message.event.event_id for message in reclaimed if message.event] == [
            pending_event.event_id
        ]
        recovery = ReliableMessageProcessor(
            second,
            factory,
            consumer_group=group,
            handled_event_types=frozenset({EventType.ARTICLE_NORMALIZED}),
        )
        recovered = await recovery.process(reclaimed, lambda _event: ProcessingOutcome.PROCESSED)
        assert recovered.processed == 1
        assert (await client.xpending(stream, group))["pending"] == 0
    finally:
        with factory() as session, session.begin():
            session.execute(
                delete(EventProcessingAttempt).where(EventProcessingAttempt.consumer_group == group)
            )
            session.execute(delete(EventDeadLetter).where(EventDeadLetter.consumer_group == group))
            session.execute(delete(ProcessedEvent).where(ProcessedEvent.consumer_group == group))
        await client.delete(stream)
        await client.aclose()


def test_due_retry_uses_durable_schedule_without_lowering_stale_reclaim_threshold() -> None:
    assert DATABASE_URL is not None and REDIS_URL is not None
    asyncio.run(_scheduled_retry_scenario(DATABASE_URL, REDIS_URL))


async def _scheduled_retry_scenario(database_url: str, redis_url: str) -> None:
    factory = sessionmaker(create_engine(database_url), expire_on_commit=False)
    client = Redis.from_url(redis_url, decode_responses=True)
    suffix = uuid4().hex
    stream = f"test:scheduled-retry:{suffix}"
    group = f"scheduled-retry-{suffix}"
    consumer = RedisStreamConsumer(
        client,
        stream=stream,
        group=group,
        consumer="owner",
        block_ms=50,
    )
    event = _event()
    now = datetime(2026, 9, 15, tzinfo=UTC)

    def clock():
        return now_holder[0]

    now_holder = [now]
    runner = ReliableMessageProcessor(
        consumer,
        factory,
        consumer_group=group,
        handled_event_types=frozenset({EventType.ARTICLE_NORMALIZED}),
        retry_policy=WorkerRetryPolicy(delays_seconds=(1,), jitter_ratio=0),
        clock=clock,
    )
    try:
        await consumer.ensure_group()
        await client.xadd(stream, {"event": event.model_dump_json()})
        messages = await consumer.read()
        assert len(messages) == 1
        message_id = messages[0].message_id

        def fail(_event):
            raise RuntimeError("transient integration failure")

        first = await runner.process(messages, fail)
        assert first.retrying == 1 and first.dead_lettered == 0
        before = await client.xpending_range(
            stream, group, message_id, message_id, 1, consumername="owner"
        )
        assert len(before) == 1 and before[0]["times_delivered"] == 1

        _, early = await runner.recover(
            lambda _event: ProcessingOutcome.PROCESSED,
            min_idle_ms=900_000,
        )
        assert early.received == 0
        unchanged = await client.xpending_range(
            stream, group, message_id, message_id, 1, consumername="owner"
        )
        assert len(unchanged) == 1 and unchanged[0]["times_delivered"] == 1

        now_holder[0] += timedelta(seconds=2)
        await asyncio.sleep(0.01)

        def succeed(current_event):
            with factory() as session, session.begin():
                mark_processed(
                    session,
                    event_id=current_event.event_id,
                    consumer_group=group,
                    result={"recovered": True},
                )
            return ProcessingOutcome.PROCESSED

        _, recovered = await runner.recover(succeed, min_idle_ms=900_000)
        assert recovered.received == 1
        assert recovered.processed == 1
        assert recovered.failed == 0
        assert (await client.xpending(stream, group))["pending"] == 0
        with factory() as session:
            assert session.get(ProcessedEvent, (event.event_id, group)) is not None
            attempts = session.query(EventProcessingAttempt).filter_by(consumer_group=group).all()
            assert len(attempts) == 1
    finally:
        with factory() as session, session.begin():
            session.execute(
                delete(EventProcessingAttempt).where(EventProcessingAttempt.consumer_group == group)
            )
            session.execute(delete(EventDeadLetter).where(EventDeadLetter.consumer_group == group))
            session.execute(delete(ProcessedEvent).where(ProcessedEvent.consumer_group == group))
        await client.delete(stream)
        await client.aclose()
