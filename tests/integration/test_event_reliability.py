from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from news_ai_database import EventDeadLetter, EventProcessingAttempt
from news_ai_events import (
    EventEnvelope,
    EventType,
    ProcessingOutcome,
    RedisStreamConsumer,
    ReliableMessageProcessor,
    WorkerRetryPolicy,
)
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
        )

        def fail(_event):
            raise RuntimeError("transient integration failure")

        result = await runner.process(messages, fail)
        assert result.dead_lettered == 1
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
        await client.delete(stream)
        await client.aclose()
