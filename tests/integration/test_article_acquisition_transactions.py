"""Real PostgreSQL short-transaction and race checks at the acquisition boundary."""

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from article_fixtures import offline_acquirer
from news_ai_database import (
    Article,
    ArticleDiscovery,
    ArticleVersion,
    Base,
    EventOutbox,
    ProcessedEvent,
)
from news_ai_events import EventType, StreamMessage
from news_ai_processor import NORMALIZER_CONSUMER_GROUP, NormalizerEventWorker
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from unit.processor.test_normalizer_worker import FakeConsumer, _discover, _event, _seed_registry


@pytest.fixture
def factory():
    url = os.getenv("NEWS_AI_DATABASE_URL")
    if not url or os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("explicit disposable PostgreSQL 16 service required")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as connection:
        tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
        connection.exec_driver_sql(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")
    try:
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()


def seed(factory):
    source, feed = _seed_registry(factory)
    discovered = _discover(factory, source, feed, body=None, retrieved_at=datetime.now(UTC))
    return discovered, _event(factory, discovered.event_id)


@pytest.mark.parametrize("action", ["success", "cancel", "mutate"])
def test_fetch_has_no_database_locks_and_revalidates_before_persistence(factory, action):
    discovered, event = seed(factory)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Acquirer:
            async def acquire(self, task):
                entered.set()
                await release.wait()
                return await offline_acquirer().acquire(task)

        consumer = FakeConsumer(messages=[StreamMessage("news:articles", "1-0", event)])
        worker = NormalizerEventWorker(consumer, factory, content_acquirer=Acquirer())
        running = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(entered.wait(), 5)
        assert factory.kw["bind"].pool.checkedout() == 0
        # Another real connection can lock both rows while acquisition is blocked.
        with factory() as session, session.begin():
            article = session.scalar(
                select(Article)
                .where(Article.id == discovered.article_id)
                .with_for_update(nowait=True)
            )
            discovery = session.scalar(
                select(ArticleDiscovery)
                .where(ArticleDiscovery.id == discovered.discovery_id)
                .with_for_update(nowait=True)
            )
            assert article is not None and discovery is not None
            assert session.scalar(select(func.count()).select_from(ArticleVersion)) == 0
            if action == "mutate":
                discovery.raw_payload = {**discovery.raw_payload, "title": "Changed during fetch"}
        if action == "cancel":
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
            assert consumer.acked == []
        else:
            release.set()
            result = await running
            assert consumer.acked == ["1-0"]
            assert (result.processed if action == "success" else result.stale) == 1

    asyncio.run(scenario())
    with factory() as session:
        versions = session.scalars(select(ArticleVersion)).all()
        normalized = session.scalars(
            select(EventOutbox).where(EventOutbox.event_type == EventType.ARTICLE_NORMALIZED)
        ).all()
        assert len(versions) == len(normalized) == (1 if action == "success" else 0)
        if action == "success":
            assert normalized[0].causation_id == event.event_id
            assert versions[0].version_metadata["content_acquisition"]["origin"] == "ARTICLE_PAGE"
        if action == "cancel":
            assert session.get(ProcessedEvent, (event.event_id, NORMALIZER_CONSUMER_GROUP)) is None


def test_concurrent_acquisition_results_create_one_durable_version_and_event(factory):
    _, event = seed(factory)

    barrier = threading.Barrier(2)

    class Acquirer:
        async def acquire(self, task):
            await asyncio.to_thread(barrier.wait, 5)
            return await offline_acquirer().acquire(task)

    consumers = [
        FakeConsumer(messages=[StreamMessage("news:articles", f"{i}-0", event)]) for i in (1, 2)
    ]
    workers = [NormalizerEventWorker(c, factory, content_acquirer=Acquirer()) for c in consumers]
    # Separate event loops allow the final PostgreSQL transactions to overlap.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(asyncio.run, w.run_once()) for w in workers]
        results = [f.result(timeout=10) for f in futures]
    assert sum(r.processed for r in results) == sum(r.duplicates for r in results) == 1
    assert all(c.acked for c in consumers)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ArticleVersion)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventOutbox)
                .where(EventOutbox.event_type == EventType.ARTICLE_NORMALIZED)
            )
            == 1
        )
