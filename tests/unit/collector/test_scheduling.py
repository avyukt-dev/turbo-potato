from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from news_ai_collector import (
    CollectedArticle,
    CollectionConfig,
    CollectionDefaults,
    CollectorScheduler,
    FeedConfig,
    FeedFetchResult,
    FeedRegistryConfig,
    NormalizedArticleHandler,
    SourceConfig,
    SourceConfigSnapshot,
    SourceRegistryConfig,
)
from news_ai_database import Article, ArticleVersion, Base, EventOutbox, SourceFeed
from news_ai_events import EventType
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


class StaticSnapshotLoader:
    def __init__(self, snapshot: SourceConfigSnapshot) -> None:
        self.snapshot = snapshot

    def load(self) -> SourceConfigSnapshot:
        return self.snapshot


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeCollector:
    def __init__(self, *, not_modified: bool = False, fail_names: set[str] | None = None) -> None:
        self.not_modified = not_modified
        self.fail_names = fail_names or set()
        self.calls: list[str] = []

    async def collect(self, feed):
        self.calls.append(feed.name)
        if feed.name in self.fail_names:
            raise RuntimeError(f"failed {feed.name}")
        articles = []
        if not self.not_modified:
            articles = [
                CollectedArticle(
                    source_id=feed.source_id,
                    source_feed_id=feed.source_feed_id,
                    url=f"https://example.com/{feed.name.lower().replace(' ', '-')}",
                    title=f"{feed.name} headline",
                    published_at=datetime(2026, 9, 10, 5, 0, tzinfo=UTC),
                    summary="Feed summary",
                    external_id=f"{feed.name}-1",
                )
            ]
        return FeedFetchResult(
            source_feed_id=feed.source_feed_id,
            articles=articles,
            etag='"etag-1"',
            last_modified="Thu, 10 Sep 2026 05:00:00 GMT",
            not_modified=self.not_modified,
        )


class BlockingCollector(FakeCollector):
    def __init__(self) -> None:
        super().__init__(not_modified=True)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def collect(self, feed):
        self.calls.append(feed.name)
        self.started.set()
        await self.release.wait()
        return FeedFetchResult(source_feed_id=feed.source_feed_id, not_modified=True)


def _snapshot(*, two_feeds: bool = False, poll_interval_seconds: int = 600) -> SourceConfigSnapshot:
    feeds = [
        FeedConfig(
            key="example.main",
            source_key="example",
            name="Main RSS",
            url="https://example.com/rss.xml",
            feed_type="RSS",
            poll_interval_seconds=poll_interval_seconds,
        )
    ]
    if two_feeds:
        feeds.append(
            FeedConfig(
                key="example.second",
                source_key="example",
                name="Second RSS",
                url="https://example.com/second.xml",
                feed_type="RSS",
                poll_interval_seconds=poll_interval_seconds,
            )
        )
    return SourceConfigSnapshot(
        registry=SourceRegistryConfig(
            schema_version=1,
            sources=[
                SourceConfig(
                    key="example",
                    name="Example News",
                    source_type="NEWS",
                    domain="example.com",
                    base_url="https://example.com",
                    enabled=True,
                )
            ],
        ),
        feeds=FeedRegistryConfig(schema_version=1, feeds=feeds),
        collection=CollectionConfig(
            schema_version=1,
            defaults=CollectionDefaults(max_concurrency=2),
        ),
    )


def _session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory


def test_cycle_syncs_registry_collects_persists_and_advances_poll_state() -> None:
    engine, factory = _session_factory()
    clock = MutableClock(datetime(2026, 9, 10, 5, 10, tzinfo=UTC))
    collector = FakeCollector()
    scheduler = CollectorScheduler(
        session_factory=factory,
        snapshot_loader=StaticSnapshotLoader(_snapshot()),
        collector=collector,
        article_handler=NormalizedArticleHandler(),
        clock=clock,
    )

    result = asyncio.run(scheduler.run_cycle())

    assert result.active_feed_count == 1
    assert result.due_feed_count == 1
    assert result.claimed_feed_count == 1
    assert result.fetched_feed_count == 1
    assert result.fetched_article_count == 1
    assert result.processed_article_count == 1
    assert result.failures == []
    with Session(engine) as session:
        feed = session.scalar(select(SourceFeed))
        article = session.scalar(select(Article))
        version = session.scalar(select(ArticleVersion))
        outboxes = {row.event_type: row for row in session.scalars(select(EventOutbox))}
        assert feed is not None
        assert article is not None
        assert version is not None
        assert set(outboxes) == {
            EventType.ARTICLE_DISCOVERED,
            EventType.ARTICLE_NORMALIZED,
        }
        assert feed.last_polled_at == clock.value.replace(tzinfo=None)
        assert feed.etag == '"etag-1"'
        assert article.title == "Main RSS headline"
        assert version.version_number == 1
        discovered = outboxes[EventType.ARTICLE_DISCOVERED]
        normalized = outboxes[EventType.ARTICLE_NORMALIZED]
        assert discovered.producer == "collector"
        assert normalized.producer == "processor"
        assert discovered.correlation_id == normalized.correlation_id
        assert normalized.causation_id == discovered.event_id
        assert discovered.payload["article_id"] == str(article.id)
    engine.dispose()


def test_cycle_respects_persisted_poll_interval_and_repolls_when_due() -> None:
    engine, factory = _session_factory()
    clock = MutableClock(datetime(2026, 9, 10, 5, 10, tzinfo=UTC))
    collector = FakeCollector(not_modified=True)
    scheduler = CollectorScheduler(
        session_factory=factory,
        snapshot_loader=StaticSnapshotLoader(_snapshot(poll_interval_seconds=600)),
        collector=collector,
        article_handler=lambda *_args, **_kwargs: None,
        clock=clock,
    )

    first = asyncio.run(scheduler.run_cycle())
    second = asyncio.run(scheduler.run_cycle())
    clock.value += timedelta(seconds=600)
    third = asyncio.run(scheduler.run_cycle())

    assert first.claimed_feed_count == 1
    assert first.not_modified_count == 1
    assert second.due_feed_count == 0
    assert second.claimed_feed_count == 0
    assert third.claimed_feed_count == 1
    assert collector.calls == ["Main RSS", "Main RSS"]
    engine.dispose()


def test_failure_isolated_and_failed_feed_poll_state_is_not_advanced() -> None:
    engine, factory = _session_factory()
    clock = MutableClock(datetime(2026, 9, 10, 5, 10, tzinfo=UTC))
    collector = FakeCollector(fail_names={"Main RSS"})
    scheduler = CollectorScheduler(
        session_factory=factory,
        snapshot_loader=StaticSnapshotLoader(_snapshot(two_feeds=True)),
        collector=collector,
        article_handler=NormalizedArticleHandler(),
        clock=clock,
    )

    result = asyncio.run(scheduler.run_cycle())

    assert result.claimed_feed_count == 2
    assert result.fetched_feed_count == 1
    assert len(result.failures) == 1
    assert result.failures[0].error_type == "RuntimeError"
    with Session(engine) as session:
        feeds = {feed.name: feed for feed in session.scalars(select(SourceFeed))}
        assert feeds["Main RSS"].last_polled_at is None
        assert feeds["Second RSS"].last_polled_at == clock.value.replace(tzinfo=None)
    engine.dispose()


def test_persistence_failure_rolls_back_article_and_poll_state() -> None:
    engine, factory = _session_factory()
    clock = MutableClock(datetime(2026, 9, 10, 5, 10, tzinfo=UTC))

    def failing_handler(*_args, **_kwargs):
        raise RuntimeError("database pipeline failure")

    scheduler = CollectorScheduler(
        session_factory=factory,
        snapshot_loader=StaticSnapshotLoader(_snapshot()),
        collector=FakeCollector(),
        article_handler=failing_handler,
        clock=clock,
    )

    result = asyncio.run(scheduler.run_cycle())

    assert result.fetched_feed_count == 0
    assert result.processed_article_count == 0
    assert len(result.failures) == 1
    assert result.failures[0].message == "database pipeline failure"
    with Session(engine) as session:
        feed = session.scalar(select(SourceFeed))
        assert feed is not None
        assert feed.last_polled_at is None
        assert session.scalar(select(Article)) is None
    engine.dispose()


def test_concurrent_cycles_do_not_poll_same_feed_twice() -> None:
    async def scenario() -> tuple[object, object, list[str]]:
        engine, factory = _session_factory()
        collector = BlockingCollector()
        scheduler = CollectorScheduler(
            session_factory=factory,
            snapshot_loader=StaticSnapshotLoader(_snapshot()),
            collector=collector,
            article_handler=lambda *_args, **_kwargs: None,
            clock=MutableClock(datetime(2026, 9, 10, 5, 10, tzinfo=UTC)),
        )
        first_task = asyncio.create_task(scheduler.run_cycle())
        await collector.started.wait()
        second = await scheduler.run_cycle()
        collector.release.set()
        first = await first_task
        engine.dispose()
        return first, second, collector.calls

    first, second, calls = asyncio.run(scenario())

    assert first.claimed_feed_count == 1
    assert second.due_feed_count == 1
    assert second.claimed_feed_count == 0
    assert second.skipped_inflight_count == 1
    assert calls == ["Main RSS"]


def test_scheduler_requires_timezone_aware_clock() -> None:
    engine, factory = _session_factory()
    scheduler = CollectorScheduler(
        session_factory=factory,
        snapshot_loader=StaticSnapshotLoader(_snapshot()),
        collector=FakeCollector(),
        article_handler=lambda *_args, **_kwargs: None,
        clock=lambda: datetime(2026, 9, 10, 5, 10),
    )

    try:
        result = asyncio.run(scheduler.run_cycle())
    except ValueError as exc:
        assert str(exc) == "collector clock must return a timezone-aware datetime"
    else:
        raise AssertionError(f"expected ValueError, got {SimpleNamespace(result=result)}")
    finally:
        engine.dispose()
