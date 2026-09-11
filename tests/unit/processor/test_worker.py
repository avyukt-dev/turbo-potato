from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from news_ai_database import Article, ArticleVersion, Base, ProcessedEvent, Source
from news_ai_events import EventEnvelope, EventType, StreamMessage
from news_ai_events.streams import stream_for_event
from news_ai_processor import ArticleNormalizedWorkItem, ProcessorEventWorker
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session


class FakeConsumer:
    def __init__(self, messages: list[StreamMessage] | None = None) -> None:
        self.stream = stream_for_event(EventType.ARTICLE_NORMALIZED)
        self.group = "processor"
        self.consumer = "processor-1"
        self.messages = messages or []
        self.stale_messages: list[StreamMessage] = []
        self.acked: list[str] = []
        self.group_ensured = False

    async def ensure_group(self) -> None:
        self.group_ensured = True

    async def read(self) -> list[StreamMessage]:
        return list(self.messages)

    async def claim_stale(
        self,
        *,
        min_idle_ms: int,
        start_id: str = "0-0",
    ) -> tuple[str, list[StreamMessage]]:
        assert min_idle_ms > 0
        return "0-0", list(self.stale_messages)

    async def ack(self, message: StreamMessage) -> None:
        self.acked.append(message.message_id)


@pytest.fixture
def session_factory() -> Callable[[], Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    def factory() -> Session:
        return Session(engine, expire_on_commit=False)

    yield factory
    engine.dispose()


def _event(
    session_factory: Callable[[], Session],
    *,
    event_type: EventType = EventType.ARTICLE_NORMALIZED,
) -> EventEnvelope:
    if event_type is not EventType.ARTICLE_NORMALIZED:
        story_id = uuid4()
        return EventEnvelope(
            event_type=event_type,
            producer="processor",
            producer_version="0.1.0",
            aggregate_type="story",
            aggregate_id=story_id,
            idempotency_key=f"{event_type.value}:{story_id}",
            payload={
                "story_id": str(story_id),
                "article_id": str(uuid4()),
                "cluster_key": "wrong-event",
            },
        )
    article_id = uuid4()
    version_id = uuid4()
    source_id = uuid4()
    with session_factory() as session, session.begin():
        session.add(Source(id=source_id, name="Example", source_type="NEWS"))
        session.add(
            Article(
                id=article_id,
                source_id=source_id,
                canonical_url="https://example.com/news/item",
                title="Example headline",
                language="en",
                published_at=datetime(2026, 9, 10, 3, 0, tzinfo=UTC),
            )
        )
        session.add(
            ArticleVersion(
                id=version_id,
                article_id=article_id,
                version_number=1,
                content_hash="a" * 64,
                retrieved_at=datetime(2026, 9, 10, 3, 5, tzinfo=UTC),
                version_metadata={"source_feed_id": str(uuid4())},
            )
        )
    return EventEnvelope(
        event_type=event_type,
        producer="processor",
        producer_version="0.1.0",
        aggregate_type="article",
        aggregate_id=article_id,
        idempotency_key=f"article.normalized:{article_id}:1",
        payload={
            "article_id": str(article_id),
            "article_version_id": str(version_id),
            "content_hash": "a" * 64,
            "language": "en",
            "title": "Example headline",
        },
    )


def _message(event: EventEnvelope, message_id: str = "1-0") -> StreamMessage:
    return StreamMessage(stream="news:articles", message_id=message_id, event=event)


def _processed_count(session_factory: Callable[[], Session]) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(ProcessedEvent)) or 0


def test_worker_rejects_wrong_stream(session_factory: Callable[[], Session]) -> None:
    consumer = FakeConsumer()
    consumer.stream = "news:stories"

    with pytest.raises(ValueError, match="must read"):
        ProcessorEventWorker(consumer, session_factory, lambda session, event, item: None)


def test_worker_rejects_wrong_group(session_factory: Callable[[], Session]) -> None:
    consumer = FakeConsumer()
    consumer.group = "other"

    with pytest.raises(ValueError, match="consumer group"):
        ProcessorEventWorker(consumer, session_factory, lambda session, event, item: None)


def test_ensure_ready_creates_consumer_group(session_factory: Callable[[], Session]) -> None:
    consumer = FakeConsumer()
    worker = ProcessorEventWorker(
        consumer,
        session_factory,
        lambda session, event, item: None,
    )

    asyncio.run(worker.ensure_ready())

    assert consumer.group_ensured is True


def test_success_commits_marker_before_ack(session_factory: Callable[[], Session]) -> None:
    event = _event(session_factory)
    consumer = FakeConsumer([_message(event)])
    seen: list[tuple[EventEnvelope, ArticleNormalizedWorkItem]] = []

    def handler(
        session: Session,
        triggering_event: EventEnvelope,
        item: ArticleNormalizedWorkItem,
    ) -> dict[str, Any]:
        assert session.in_transaction()
        seen.append((triggering_event, item))
        return {"handoff": "story-clustering"}

    worker = ProcessorEventWorker(consumer, session_factory, handler)
    result = asyncio.run(worker.run_once())

    assert result.received == 1
    assert result.processed == 1
    assert result.duplicates == 0
    assert result.failed == 0
    assert consumer.acked == ["1-0"]
    assert len(seen) == 1
    assert seen[0][0].event_id == event.event_id
    assert seen[0][1].article_id == event.aggregate_id

    with session_factory() as session:
        marker = session.get(ProcessedEvent, (event.event_id, "processor"))
        assert marker is not None
        assert marker.result == {"handoff": "story-clustering"}


def test_duplicate_event_is_acked_without_reinvoking_handler(
    session_factory: Callable[[], Session],
) -> None:
    event = _event(session_factory)
    calls = 0

    def handler(
        session: Session,
        triggering_event: EventEnvelope,
        item: ArticleNormalizedWorkItem,
    ) -> None:
        nonlocal calls
        calls += 1

    first_consumer = FakeConsumer([_message(event, "1-0")])
    first_worker = ProcessorEventWorker(first_consumer, session_factory, handler)
    first = asyncio.run(first_worker.run_once())

    second_consumer = FakeConsumer([_message(event, "2-0")])
    second_worker = ProcessorEventWorker(second_consumer, session_factory, handler)
    second = asyncio.run(second_worker.run_once())

    assert first.processed == 1
    assert second.duplicates == 1
    assert calls == 1
    assert second_consumer.acked == ["2-0"]
    assert _processed_count(session_factory) == 1


def test_handler_failure_rolls_back_marker_and_leaves_message_pending(
    session_factory: Callable[[], Session],
) -> None:
    event = _event(session_factory)
    consumer = FakeConsumer([_message(event)])

    def handler(
        session: Session,
        triggering_event: EventEnvelope,
        item: ArticleNormalizedWorkItem,
    ) -> None:
        raise RuntimeError("clustering unavailable")

    worker = ProcessorEventWorker(consumer, session_factory, handler)
    result = asyncio.run(worker.run_once())

    assert result.received == 1
    assert result.processed == 0
    assert result.failed == 1
    assert result.failed_message_ids == ("1-0",)
    assert result.retrying == 1
    assert result.dead_lettered == 0
    assert consumer.acked == []
    assert _processed_count(session_factory) == 0


def test_invalid_payload_remains_pending(session_factory: Callable[[], Session]) -> None:
    event = _event(session_factory)
    event.payload["content_hash"] = "too-short"
    consumer = FakeConsumer([_message(event)])
    called = False

    def handler(
        session: Session,
        triggering_event: EventEnvelope,
        item: ArticleNormalizedWorkItem,
    ) -> None:
        nonlocal called
        called = True

    worker = ProcessorEventWorker(consumer, session_factory, handler)
    result = asyncio.run(worker.run_once())

    assert result.failed == 1
    assert called is False
    assert result.dead_lettered == 1
    assert consumer.acked == ["1-0"]


def test_payload_article_id_must_match_event_aggregate(
    session_factory: Callable[[], Session],
) -> None:
    event = _event(session_factory)
    event.payload["article_id"] = str(uuid4())
    consumer = FakeConsumer([_message(event)])
    worker = ProcessorEventWorker(
        consumer,
        session_factory,
        lambda session, triggering_event, item: None,
    )

    result = asyncio.run(worker.run_once())

    assert result.failed == 1
    assert result.dead_lettered == 1
    assert consumer.acked == ["1-0"]
    assert _processed_count(session_factory) == 0


def test_legacy_extra_event_field_is_dead_lettered(
    session_factory: Callable[[], Session],
) -> None:
    event = _event(session_factory)
    event.payload["retrieved_at"] = "2026-09-10T03:05:00+00:00"
    consumer = FakeConsumer([_message(event)])
    worker = ProcessorEventWorker(
        consumer,
        session_factory,
        lambda session, triggering_event, item: None,
    )

    result = asyncio.run(worker.run_once())

    assert result.failed == 1
    assert result.dead_lettered == 1
    assert consumer.acked == ["1-0"]


def test_wrong_event_type_is_ignored_and_acked(session_factory: Callable[[], Session]) -> None:
    event = _event(session_factory, event_type=EventType.STORY_CREATED)
    consumer = FakeConsumer([_message(event)])
    worker = ProcessorEventWorker(
        consumer,
        session_factory,
        lambda session, triggering_event, item: None,
    )

    result = asyncio.run(worker.run_once())

    assert result.ignored == 1
    assert result.failed == 0
    assert consumer.acked == ["1-0"]
    assert _processed_count(session_factory) == 0


def test_stale_pending_message_can_be_recovered(session_factory: Callable[[], Session]) -> None:
    event = _event(session_factory)
    consumer = FakeConsumer()
    consumer.stale_messages = [_message(event, "9-0")]
    seen: list[datetime] = []

    def handler(
        session: Session,
        triggering_event: EventEnvelope,
        item: ArticleNormalizedWorkItem,
    ) -> None:
        seen.append(item.retrieved_at)

    worker = ProcessorEventWorker(consumer, session_factory, handler)
    cursor, result = asyncio.run(worker.recover_once(min_idle_ms=60_000))

    assert cursor == "0-0"
    assert result.processed == 1
    assert consumer.acked == ["9-0"]
    assert seen == [datetime(2026, 9, 10, 3, 5, tzinfo=UTC)]
