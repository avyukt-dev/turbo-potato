from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from article_fixtures import offline_acquirer
from news_ai_collector import CollectedArticle, DiscoveredArticleHandler
from news_ai_database import (
    Article,
    ArticleDiscovery,
    ArticleVersion,
    Base,
    EventDeadLetter,
    EventOutbox,
    ProcessedEvent,
    Source,
    SourceFeed,
)
from news_ai_events import (
    EventEnvelope,
    EventType,
    PermanentEventError,
    StreamMessage,
    TransientEventError,
)
from news_ai_events.outbox import build_outbox_record, envelope_from_outbox
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


def test_acquisition_retry_then_success_and_duplicate_skips_fetch():
    factory = _factory()
    source_id, feed_id = _seed_registry(factory)
    discovered = _discover(
        factory, source_id, feed_id, body="Reviewed explicit body", retrieved_at=datetime.now(UTC)
    )
    event = _event(factory, discovered.event_id)
    calls = []

    class Acquirer:
        async def acquire(self, task):
            calls.append(task)
            if len(calls) == 1:
                raise TransientEventError("article content request timed out")
            return await offline_acquirer().acquire(task)

    consumer = FakeConsumer(messages=[StreamMessage("news:articles", "1-0", event)])
    worker = NormalizerEventWorker(consumer, factory, content_acquirer=Acquirer())
    assert asyncio.run(worker.run_once()).retrying == 1
    assert consumer.acked == []
    with factory() as session:
        assert session.get(ProcessedEvent, (event.event_id, NORMALIZER_CONSUMER_GROUP)) is None
        assert not session.scalar(select(ArticleVersion))
    worker.reliability.clock = lambda: datetime.now(UTC) + timedelta(minutes=5)
    consumer.messages = [StreamMessage("news:articles", "1-0", event)]
    assert asyncio.run(worker.run_once()).processed == 1
    consumer.messages = [StreamMessage("news:articles", "2-0", event)]
    assert asyncio.run(worker.run_once()).duplicates == 1
    assert len(calls) == 2
    with factory() as session:
        version = session.scalar(select(ArticleVersion))
        assert version.version_metadata["content_acquisition"]["origin"] == "FEED_CONTENT"
        assert session.scalar(select(func.count()).select_from(ArticleVersion)) == 1


def test_permanent_acquisition_failure_goes_to_existing_dlq_without_normalization():
    factory = _factory()
    source_id, feed_id = _seed_registry(factory)
    discovered = _discover(
        factory, source_id, feed_id, body="Reviewed body", retrieved_at=datetime.now(UTC)
    )
    event = _event(factory, discovered.event_id)

    class Acquirer:
        async def acquire(self, task):
            raise PermanentEventError("article content host is not permitted")

    consumer = FakeConsumer(messages=[StreamMessage("news:articles", "1-0", event)])
    worker = NormalizerEventWorker(consumer, factory, content_acquirer=Acquirer())
    result = asyncio.run(worker.run_once())
    assert result.dead_lettered == 1 and consumer.acked == ["1-0"]
    with factory() as session:
        assert session.scalar(select(EventDeadLetter))
        assert not session.scalar(select(ArticleVersion))
        assert session.get(ProcessedEvent, (event.event_id, NORMALIZER_CONSUMER_GROUP)) is None


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
    result = asyncio.run(
        NormalizerEventWorker(consumer, factory, content_acquirer=offline_acquirer()).run_once()
    )

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
    worker = NormalizerEventWorker(consumer, factory, content_acquirer=offline_acquirer())
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


def test_legacy_discovery_with_causal_normalized_outbox_is_safely_completed() -> None:
    factory = _factory()
    source_id, feed_id = _seed_registry(factory)
    now = datetime(2026, 9, 11, tzinfo=UTC)
    with factory() as session, session.begin():
        article = Article(
            source_id=source_id,
            canonical_url="https://example.com/legacy",
            title="Legacy headline",
            language="en",
        )
        session.add(article)
        session.flush()
        version = ArticleVersion(
            article_id=article.id,
            version_number=1,
            content_hash="a" * 64,
            body="Already normalized before migration 0006.",
            retrieved_at=now,
            version_metadata={"source_feed_id": str(feed_id)},
        )
        session.add(version)
        session.flush()
        discovered = EventEnvelope(
            event_type=EventType.ARTICLE_DISCOVERED,
            producer="collector",
            producer_version="0.1.0",
            aggregate_type="article",
            aggregate_id=article.id,
            idempotency_key=f"article.discovered:legacy:{article.id}",
            payload={
                "article_id": str(article.id),
                "source_id": str(source_id),
                "source_feed_id": str(feed_id),
                "canonical_url": article.canonical_url,
                "title": article.title,
                "published_at": None,
            },
        )
        normalized = EventEnvelope(
            event_type=EventType.ARTICLE_NORMALIZED,
            producer="collector",
            producer_version="0.1.0",
            aggregate_type="article",
            aggregate_id=article.id,
            correlation_id=discovered.correlation_id,
            causation_id=discovered.event_id,
            idempotency_key=f"article.normalized:legacy:{version.id}",
            payload={
                "article_id": str(article.id),
                "article_version_id": str(version.id),
                "content_hash": version.content_hash,
                "language": article.language,
                "title": article.title,
            },
        )
        session.add_all([build_outbox_record(discovered), build_outbox_record(normalized)])

    consumer = FakeConsumer(messages=[StreamMessage("news:articles", "legacy-1", discovered)])
    result = asyncio.run(
        NormalizerEventWorker(consumer, factory, content_acquirer=offline_acquirer()).run_once()
    )

    assert result.duplicates == 1
    assert result.dead_lettered == 0
    assert consumer.acked == ["legacy-1"]
    with factory() as session:
        processed = session.get(ProcessedEvent, (discovered.event_id, NORMALIZER_CONSUMER_GROUP))
        assert processed is not None
        assert processed.result["legacy_completed"] is True
        assert session.scalar(select(func.count()).select_from(EventDeadLetter)) == 0
