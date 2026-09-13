from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from article_fixtures import offline_acquirer
from news_ai_collector import CollectedArticle, DiscoveredArticleHandler
from news_ai_database import (
    ArticleDiscovery,
    ArticleVersion,
    Base,
    EventOutbox,
    ProcessedEvent,
    Publication,
    PublicationAttempt,
    PublicationAttemptPhase,
    PublicationAttemptStatus,
    Source,
    SourceFeed,
)
from news_ai_database.models import OutboxStatus
from news_ai_domain import PublicationStatus
from news_ai_events import (
    EventEnvelope,
    EventReconciliationService,
    EventType,
    OutboxDispatcher,
    ReconciliationDisposition,
    ReconciliationMode,
    RedisStreamConsumer,
    RedisStreamPublisher,
)
from news_ai_events.outbox import build_outbox_record
from news_ai_processor import NORMALIZER_CONSUMER_GROUP, NormalizerEventWorker
from news_ai_publisher import PublisherWorker
from news_ai_runtime import DatabasePublishingControl
from redis.asyncio import Redis
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from unit.publishing.test_execution import setup_execution


@pytest.fixture
def factory():
    url = os.getenv("NEWS_AI_DATABASE_URL")
    if not url or os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("isolated PostgreSQL test service required")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as connection:
        tables = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
        connection.exec_driver_sql(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")
    try:
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()


def seed_discovery(factory, *, suffix="lost"):
    with factory() as session, session.begin():
        source = Source(name=f"Recovery {suffix}", source_type="NEWS")
        session.add(source)
        session.flush()
        feed = SourceFeed(
            source_id=source.id,
            name=f"Recovery {suffix}",
            feed_url=f"https://example.org/{suffix}.xml",
            feed_type="RSS",
        )
        session.add(feed)
        session.flush()
        result = DiscoveredArticleHandler()(
            session,
            CollectedArticle(
                source_id=source.id,
                source_feed_id=feed.id,
                url=f"https://example.org/{suffix}",
                title="Durable reconciliation",
                language="en",
                body="PostgreSQL survives disposable Redis transport loss.",
                external_id=suffix,
            ),
            retrieved_at=datetime(2026, 9, 13, tzinfo=UTC),
        )
        return result


def reconciler(factory, client, control):
    return EventReconciliationService(
        factory,
        RedisStreamPublisher(client),
        client,
        control,
    )


def test_real_postgres_redis_lost_stream_reconciles_original_event_to_normalizer(factory):
    redis_url = os.getenv("NEWS_AI_REDIS_URL")
    if not redis_url:
        pytest.skip("isolated Redis test service required")
    discovered = seed_discovery(factory)

    async def scenario():
        client = Redis.from_url(redis_url, decode_responses=True)
        control = DatabasePublishingControl(factory)
        try:
            await client.delete("news:articles")
            dispatcher = OutboxDispatcher(factory, RedisStreamPublisher(client))
            assert (await dispatcher.dispatch_once()).published == 1
            assert await client.xlen("news:articles") == 1
            with factory() as session:
                row = session.scalar(
                    select(EventOutbox).where(EventOutbox.event_id == discovered.event_id)
                )
                immutable = (
                    row.status,
                    row.attempt_count,
                    row.published_at,
                    row.last_error,
                )
            await client.delete("news:articles")
            assert (await dispatcher.dispatch_once()).claimed == 0

            service = reconciler(factory, client, control)
            dry = await service.reconcile(
                mode=ReconciliationMode.DRY_RUN,
                event_type=EventType.ARTICLE_DISCOVERED,
            )
            assert dry.replay_required == 1 and dry.replayed == 0
            control.set_paused(False, reason="prove active publishing rejects recovery")
            rejected = await service.reconcile(
                mode=ReconciliationMode.APPLY,
                reason="verified isolated Redis loss",
                event_type=EventType.ARTICLE_DISCOVERED,
            )
            assert rejected.error_code == "PUBLISHING_NOT_PAUSED"
            assert not await client.exists("news:articles")

            control.set_paused(True, reason="bounded Redis recovery")
            applied = await service.reconcile(
                mode=ReconciliationMode.APPLY,
                reason="verified isolated Redis loss",
                event_type=EventType.ARTICLE_DISCOVERED,
            )
            assert applied.replayed == 1
            assert applied.groups_created[0].consumer_group == NORMALIZER_CONSUMER_GROUP
            entries = await client.xrange("news:articles")
            assert len(entries) == 1
            assert str(discovered.event_id) in entries[0][1]["event"]

            consumer = RedisStreamConsumer(
                client,
                stream="news:articles",
                group=NORMALIZER_CONSUMER_GROUP,
                consumer="reconciliation-test",
                block_ms=1,
            )
            worker = NormalizerEventWorker(consumer, factory, content_acquirer=offline_acquirer())
            assert (await worker.run_once()).processed == 1
            with factory() as session:
                processed = session.get(
                    ProcessedEvent,
                    (discovered.event_id, NORMALIZER_CONSUMER_GROUP),
                )
                row = session.scalar(
                    select(EventOutbox).where(EventOutbox.event_id == discovered.event_id)
                )
                assert processed is not None
                assert (
                    row.status,
                    row.attempt_count,
                    row.published_at,
                    row.last_error,
                ) == immutable
                assert (
                    session.scalar(
                        select(func.count())
                        .select_from(EventOutbox)
                        .where(EventOutbox.event_type == "article.normalized")
                    )
                    == 1
                )
            repeated = await service.reconcile(
                mode=ReconciliationMode.APPLY,
                reason="repeat must be harmless",
                event_type=EventType.ARTICLE_DISCOVERED,
            )
            assert repeated.already_processed == 1 and repeated.replayed == 0
        finally:
            await client.delete("news:articles")
            await client.aclose()

    asyncio.run(scenario())


def test_real_redis_missing_group_is_restored_without_resetting_existing_offset(factory):
    redis_url = os.getenv("NEWS_AI_REDIS_URL")
    if not redis_url:
        pytest.skip("isolated Redis test service required")
    discovered = seed_discovery(factory, suffix="group-loss")

    async def scenario():
        client = Redis.from_url(redis_url, decode_responses=True)
        control = DatabasePublishingControl(factory)
        try:
            await client.delete("news:articles")
            consumer = RedisStreamConsumer(
                client,
                stream="news:articles",
                group=NORMALIZER_CONSUMER_GROUP,
                consumer="group-recovery",
                block_ms=1,
                count=10,
            )
            await consumer.ensure_group()
            await OutboxDispatcher(factory, RedisStreamPublisher(client)).dispatch_once()
            assert await client.xgroup_destroy("news:articles", NORMALIZER_CONSUMER_GROUP) == 1
            control.set_paused(True, reason="isolated missing-group recovery")
            service = reconciler(factory, client, control)
            concurrent = await asyncio.gather(
                service.reconcile(
                    mode=ReconciliationMode.APPLY,
                    reason="restore canonical group operator one",
                    event_type=EventType.ARTICLE_DISCOVERED,
                ),
                service.reconcile(
                    mode=ReconciliationMode.APPLY,
                    reason="restore canonical group operator two",
                    event_type=EventType.ARTICLE_DISCOVERED,
                ),
            )
            assert sum(item.replayed for item in concurrent) == 2
            assert sum(len(item.groups_created) for item in concurrent) == 1
            groups = await client.xinfo_groups("news:articles")
            before = groups[0]["last-delivered-id"]
            assert before == "0-0"
            repeated = await service.reconcile(
                mode=ReconciliationMode.APPLY,
                reason="existing group must retain offset",
                event_type=EventType.ARTICLE_DISCOVERED,
            )
            groups = await client.xinfo_groups("news:articles")
            assert groups[0]["last-delivered-id"] == before
            assert repeated.groups_created == () and repeated.replayed == 1
            result = await NormalizerEventWorker(
                consumer, factory, content_acquirer=offline_acquirer()
            ).run_once()
            assert result.processed == 1 and result.duplicates >= 2
            with factory() as session:
                assert session.get(ProcessedEvent, (discovered.event_id, NORMALIZER_CONSUMER_GROUP))
        finally:
            await client.delete("news:articles")
            await client.aclose()

    asyncio.run(scenario())


def test_real_postgres_redis_mixed_history_replays_only_incomplete_work(factory):
    redis_url = os.getenv("NEWS_AI_REDIS_URL")
    if not redis_url:
        pytest.skip("isolated Redis test service required")
    incomplete = seed_discovery(factory, suffix="mixed-incomplete")
    processed = seed_discovery(factory, suffix="mixed-processed")
    complete = seed_discovery(factory, suffix="mixed-complete")
    unsupported = EventEnvelope(
        event_type=EventType.ANALYTICS_REQUESTED,
        occurred_at=datetime(2026, 9, 13, 0, 0, 1, tzinfo=UTC),
        producer="acceptance-test",
        producer_version="1",
        aggregate_type="publication",
        aggregate_id=complete.article_id,
        idempotency_key="analytics:unsupported:reconciliation",
        payload={"publication_id": str(complete.article_id), "platform": "INSTAGRAM"},
    )
    with factory() as session, session.begin():
        session.add(
            ProcessedEvent(
                event_id=processed.event_id,
                consumer_group=NORMALIZER_CONSUMER_GROUP,
            )
        )
        discovery = session.scalar(
            select(ArticleDiscovery).where(ArticleDiscovery.event_id == complete.event_id)
        )
        version = ArticleVersion(
            article_id=complete.article_id,
            version_number=1,
            content_hash="c" * 64,
            body="Already normalized durable state.",
            retrieved_at=datetime(2026, 9, 13, tzinfo=UTC),
        )
        session.add(version)
        session.flush()
        discovery.normalized_article_version_id = version.id
        for event in (incomplete, processed, complete, unsupported):
            row = session.scalar(select(EventOutbox).where(EventOutbox.event_id == event.event_id))
            if row is None:
                row = build_outbox_record(event)
                session.add(row)
            row.status = OutboxStatus.PUBLISHED
            row.published_at = getattr(event, "occurred_at", datetime(2026, 9, 13, tzinfo=UTC))

    async def scenario():
        client = Redis.from_url(redis_url, decode_responses=True)
        control = DatabasePublishingControl(factory)
        try:
            await client.delete("news:articles", "news:analytics")
            service = reconciler(factory, client, control)
            dry = await service.reconcile(mode=ReconciliationMode.DRY_RUN, limit=10)
            assert dry.scanned == 4
            assert dry.replay_required == 1
            assert dry.already_processed == 1
            assert dry.domain_complete == 1
            assert dry.unsupported == 1
            control.set_paused(True, reason="mixed durable history recovery")
            applied = await service.reconcile(
                mode=ReconciliationMode.APPLY,
                limit=10,
                reason="restore only incomplete durable work",
            )
            assert applied.replayed == 1
            entries = await client.xrange("news:articles")
            assert len(entries) == 1
            assert str(incomplete.event_id) in entries[0][1]["event"]
            assert str(processed.event_id) not in entries[0][1]["event"]
            assert str(complete.event_id) not in entries[0][1]["event"]
            assert not await client.exists("news:analytics")
        finally:
            await client.delete("news:articles", "news:analytics")
            await client.aclose()

    asyncio.run(scenario())


def test_publication_scheduled_loss_defers_while_paused_then_publishes_once(factory, monkeypatch):
    redis_url = os.getenv("NEWS_AI_REDIS_URL")
    if not redis_url:
        pytest.skip("isolated Redis test service required")
    _, _, _, _, _, row, event, service, adapter, execution = setup_execution(factory)
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")

    async def scenario():
        client = Redis.from_url(redis_url, decode_responses=True)
        control = DatabasePublishingControl(factory)
        try:
            await client.delete("news:publishing")
            dispatcher = OutboxDispatcher(factory, RedisStreamPublisher(client))
            assert (await dispatcher.dispatch_once()).published == 1
            await client.delete("news:publishing")
            assert (await dispatcher.dispatch_once()).claimed == 0
            control.set_paused(True, reason="publication transport recovery")
            recovery = reconciler(factory, client, control)
            applied = await recovery.reconcile(
                mode=ReconciliationMode.APPLY,
                reason="recover scheduled publication transport",
                event_type=EventType.PUBLICATION_SCHEDULED,
            )
            assert applied.replayed == 1
            execution.paused = control.paused
            consumer = RedisStreamConsumer(
                client,
                stream="news:publishing",
                group="publisher",
                consumer="reconciliation-publisher",
                block_ms=1,
            )
            worker = PublisherWorker(consumer, execution)
            deferred = await worker.run_once()
            assert deferred.retrying == 1
            assert adapter.preparations == adapter.publishes == 0
            assert service.get(row.id).status is PublicationStatus.SCHEDULED

            control.set_paused(False, reason="recovery inspected and approved")
            _, resumed, _, _ = await worker.run_batch()
            assert resumed.processed == 1
            assert service.get(row.id).status is PublicationStatus.PUBLISHED
            assert adapter.preparations == adapter.publishes == 1

            control.set_paused(True, reason="prove repeated recovery is fenced")
            repeated = await recovery.reconcile(
                mode=ReconciliationMode.APPLY,
                reason="repeat after durable completion",
                event_type=EventType.PUBLICATION_SCHEDULED,
            )
            assert repeated.already_processed == 1 and repeated.replayed == 0
            with factory() as session, session.begin():
                session.execute(
                    ProcessedEvent.__table__.delete().where(
                        ProcessedEvent.event_id == event.event_id,
                        ProcessedEvent.consumer_group == "publisher",
                    )
                )
            complete = await recovery.reconcile(
                mode=ReconciliationMode.DRY_RUN,
                event_type=EventType.PUBLICATION_SCHEDULED,
            )
            assert complete.domain_complete == 1 and adapter.publishes == 1
        finally:
            await client.delete("news:publishing")
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("checkpoint", ["ambiguous", "known_external_id"])
def test_publication_reconciliation_preserves_post_intent_fences(factory, monkeypatch, checkpoint):
    redis_url = os.getenv("NEWS_AI_REDIS_URL")
    if not redis_url:
        pytest.skip("isolated Redis test service required")
    _, clock, _, _, _, row, event, service, adapter, execution = setup_execution(factory)
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")
    claim = execution.claim(event)
    with factory() as session, session.begin():
        publication = session.get(Publication, row.id)
        attempt = session.get(PublicationAttempt, claim.attempt_id)
        if checkpoint == "ambiguous":
            attempt.phase = PublicationAttemptPhase.PUBLISH_INTENT_RECORDED
            attempt.status = PublicationAttemptStatus.AMBIGUOUS
            attempt.ambiguous = True
            publication.status = PublicationStatus.BLOCKED
        else:
            attempt.phase = PublicationAttemptPhase.VERIFYING
            attempt.external_post_id = "known-id"
            publication.external_post_id = "known-id"
        outbox = session.scalar(select(EventOutbox).where(EventOutbox.event_id == event.event_id))
        outbox.status = OutboxStatus.PUBLISHED
        outbox.published_at = clock.now
    clock.now += timedelta(hours=3)

    async def scenario():
        client = Redis.from_url(redis_url, decode_responses=True)
        control = DatabasePublishingControl(factory, clock=clock)
        try:
            await client.delete("news:publishing")
            control.set_paused(True, reason="post-intent reconciliation fence")
            recovery = reconciler(factory, client, control)
            report = await recovery.reconcile(
                mode=ReconciliationMode.APPLY,
                reason="verified Redis loss",
                event_type=EventType.PUBLICATION_SCHEDULED,
            )
            if checkpoint == "ambiguous":
                assert (
                    report.items[0].disposition is ReconciliationDisposition.MANUAL_REVIEW_REQUIRED
                )
                assert report.replayed == 0 and adapter.publishes == adapter.verifications == 0
                return
            assert report.replayed == 1
            execution.paused = control.paused
            worker = PublisherWorker(
                RedisStreamConsumer(
                    client,
                    stream="news:publishing",
                    group="publisher",
                    consumer="known-id-recovery",
                    block_ms=1,
                ),
                execution,
            )
            assert (await worker.run_once()).processed == 1
            assert service.get(row.id).status is PublicationStatus.PUBLISHED
            assert adapter.publishes == adapter.preparations == 0
            assert adapter.verifications == 1
        finally:
            await client.delete("news:publishing")
            await client.aclose()

    asyncio.run(scenario())
