from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from news_ai_collector import CollectedArticle, DiscoveredArticleHandler
from news_ai_database import (
    Article,
    ArticleDiscovery,
    ArticleVersion,
    Base,
    EventOutbox,
    Source,
    SourceFeed,
)
from news_ai_events import EventType, StreamMessage
from news_ai_events.outbox import envelope_from_outbox
from news_ai_processor import NORMALIZER_CONSUMER_GROUP, NormalizerEventWorker
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


@dataclass
class FakeConsumer:
    stream: str = "news:articles"
    group: str = NORMALIZER_CONSUMER_GROUP
    messages: list[StreamMessage] = field(default_factory=list)
    acked: list[str] = field(default_factory=list)

    async def ensure_group(self) -> None: ...

    async def read(self) -> list[StreamMessage]:
        messages, self.messages = self.messages, []
        return messages

    async def claim_stale(self, *, min_idle_ms: int, start_id: str = "0-0"):
        return "0-0", []

    async def ack(self, message: StreamMessage) -> None:
        self.acked.append(message.message_id)


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _seed_registry(factory: sessionmaker[Session]) -> tuple[object, object]:
    with factory() as session, session.begin():
        source = Source(name="Example", source_type="NEWS", domain="example.com")
        session.add(source)
        session.flush()
        feed = SourceFeed(
            source_id=source.id,
            name="Example RSS",
            feed_url="https://example.com/rss.xml",
            feed_type="RSS",
        )
        session.add(feed)
        session.flush()
        return source.id, feed.id


def _discover(
    factory: sessionmaker[Session],
    source_id,
    feed_id,
    *,
    body: str,
    retrieved_at: datetime,
):
    with factory() as session, session.begin():
        return DiscoveredArticleHandler()(
            session,
            CollectedArticle(
                source_id=source_id,
                source_feed_id=feed_id,
                url="https://example.com/story?utm_source=rss",
                title="Discovery headline",
                language="en",
                body=body,
                external_id="story-1",
            ),
            retrieved_at=retrieved_at,
        )


def _event(factory: sessionmaker[Session], event_id):
    with factory() as session:
        row = session.scalar(select(EventOutbox).where(EventOutbox.event_id == event_id))
        assert row is not None
        return envelope_from_outbox(row)


def test_discovery_is_durable_before_normalization_and_causation_is_preserved() -> None:
    factory = _factory()
    source_id, feed_id = _seed_registry(factory)
    discovered = _discover(
        factory,
        source_id,
        feed_id,
        body="First body",
        retrieved_at=datetime(2026, 9, 11, tzinfo=UTC),
    )
    event = _event(factory, discovered.event_id)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ArticleDiscovery)) == 1
        assert session.scalar(select(func.count()).select_from(ArticleVersion)) == 0
        assert event.event_type is EventType.ARTICLE_DISCOVERED

    consumer = FakeConsumer(messages=[StreamMessage("news:articles", "1-0", event)])
    result = asyncio.run(NormalizerEventWorker(consumer, factory).run_once())

    assert result.processed == 1
    assert consumer.acked == ["1-0"]
    with factory() as session:
        normalized = session.scalar(
            select(EventOutbox).where(EventOutbox.event_type == EventType.ARTICLE_NORMALIZED)
        )
        discovery = session.get(ArticleDiscovery, discovered.discovery_id)
        article = session.get(Article, discovered.article_id)
        assert normalized is not None
        assert discovery is not None and discovery.normalized_article_version_id is not None
        assert article is not None and article.title == "Discovery headline"
        assert normalized.causation_id == event.event_id
        assert normalized.correlation_id == event.correlation_id


def test_discovery_replay_is_idempotent_and_changed_content_creates_new_version() -> None:
    factory = _factory()
    source_id, feed_id = _seed_registry(factory)
    now = datetime(2026, 9, 11, tzinfo=UTC)
    first = _discover(factory, source_id, feed_id, body="First body", retrieved_at=now)
    replay = _discover(
        factory,
        source_id,
        feed_id,
        body="First body",
        retrieved_at=now + timedelta(minutes=5),
    )
    assert replay.created_discovery is False
    assert replay.event_id == first.event_id

    first_event = _event(factory, first.event_id)
    consumer = FakeConsumer(messages=[StreamMessage("news:articles", "1-0", first_event)])
    worker = NormalizerEventWorker(consumer, factory)
    assert asyncio.run(worker.run_once()).processed == 1
    consumer.messages = [StreamMessage("news:articles", "1-1", first_event)]
    assert asyncio.run(worker.run_once()).duplicates == 1

    changed = _discover(
        factory,
        source_id,
        feed_id,
        body="Corrected body",
        retrieved_at=now + timedelta(minutes=10),
    )
    assert changed.created_discovery is True
    consumer.messages = [StreamMessage("news:articles", "2-0", _event(factory, changed.event_id))]
    assert asyncio.run(worker.run_once()).processed == 1
    with factory() as session:
        versions = list(
            session.scalars(select(ArticleVersion).order_by(ArticleVersion.version_number))
        )
        normalized_count = session.scalar(
            select(func.count())
            .select_from(EventOutbox)
            .where(EventOutbox.event_type == EventType.ARTICLE_NORMALIZED)
        )
        assert [row.version_number for row in versions] == [1, 2]
        assert normalized_count == 2
