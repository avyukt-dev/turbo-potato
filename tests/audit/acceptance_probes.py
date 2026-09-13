"""Production acceptance probes in explicitly disposable local test infrastructure."""

import asyncio
import json
import os
import time
import tracemalloc
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import psycopg
from news_ai_collector import CollectedArticle, DiscoveredArticleHandler
from news_ai_database import (
    Article,
    ArticleVersion,
    Base,
    EventOutbox,
    ProcessedEvent,
    Source,
    SourceFeed,
)
from news_ai_events import (
    EventEnvelope,
    EventReconciliationService,
    EventType,
    OutboxDispatcher,
    ReconciliationMode,
    RedisStreamConsumer,
    RedisStreamPublisher,
)
from news_ai_events.outbox import build_outbox_record
from news_ai_evidence import (
    PostgresArticleSearchProvider,
    SearchCapability,
    SearchQueryFamily,
    SearchRequest,
)
from news_ai_processor import NORMALIZER_CONSUMER_GROUP, NormalizerEventWorker
from news_ai_processor.acquisition import ArticleContentPolicy, HttpArticleContentAcquirer
from news_ai_runtime import DatabasePublishingControl
from psycopg import sql
from redis.asyncio import Redis
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


def event():
    article_id = uuid4()
    return EventEnvelope(
        event_type=EventType.ARTICLE_DISCOVERED,
        occurred_at=datetime.now(UTC),
        producer="acceptance-probe",
        producer_version="1",
        aggregate_type="article",
        aggregate_id=article_id,
        idempotency_key=f"acceptance:{article_id}",
        payload={
            "article_id": str(article_id),
            "source_id": str(uuid4()),
            "source_feed_id": str(uuid4()),
            "canonical_url": "https://example.org/synthetic",
            "title": "Synthetic acceptance material",
            "published_at": None,
        },
    )


async def probe(factory, redis_url):
    # main() permits only explicit local test infrastructure; this canonical key is
    # deleted so the probe represents total loss of its stream and consumer groups.
    stream = "news:articles"
    client = Redis.from_url(redis_url, decode_responses=True)
    acquisition = HttpArticleContentAcquirer(ArticleContentPolicy(article_min_body_chars=1))
    try:
        await client.delete(stream)
        with factory() as session, session.begin():
            source = Source(name="Reconciliation acceptance", source_type="NEWS")
            session.add(source)
            session.flush()
            feed = SourceFeed(
                source_id=source.id,
                name="Reconciliation acceptance",
                feed_url="https://example.org/reconciliation.xml",
                feed_type="RSS",
            )
            session.add(feed)
            session.flush()
            discovered = DiscoveredArticleHandler()(
                session,
                CollectedArticle(
                    source_id=source.id,
                    source_feed_id=feed.id,
                    url="https://example.org/reconciliation",
                    title="Durable transport recovery",
                    language="en",
                    body="PostgreSQL survives Redis transport loss.",
                    external_id="acceptance-reconciliation",
                ),
                retrieved_at=datetime.now(UTC),
            )
        dispatcher = OutboxDispatcher(factory, RedisStreamPublisher(client))
        first = await dispatcher.dispatch_once()
        before = await client.xlen(stream)
        await client.delete(stream)
        second = await dispatcher.dispatch_once()
        with factory() as session:
            durable = session.scalar(
                select(EventOutbox).where(EventOutbox.event_id == discovered.event_id)
            )
            retained = durable.status.value == "PUBLISHED"
        control = DatabasePublishingControl(factory)
        reconciliation = EventReconciliationService(
            factory,
            RedisStreamPublisher(client),
            client,
            control,
        )
        dry = await reconciliation.reconcile(
            mode=ReconciliationMode.DRY_RUN,
            event_type=EventType.ARTICLE_DISCOVERED,
        )
        control.set_paused(True, reason="acceptance probe Redis recovery")
        applied = await reconciliation.reconcile(
            mode=ReconciliationMode.APPLY,
            reason="verified disposable Redis transport loss",
            event_type=EventType.ARTICLE_DISCOVERED,
        )
        worker = NormalizerEventWorker(
            RedisStreamConsumer(
                client,
                stream=stream,
                group=NORMALIZER_CONSUMER_GROUP,
                consumer="acceptance-reconciliation",
                block_ms=1,
            ),
            factory,
            content_acquirer=acquisition,
        )
        consumed = await worker.run_once()
        converged = await reconciliation.reconcile(
            mode=ReconciliationMode.DRY_RUN,
            event_type=EventType.ARTICLE_DISCOVERED,
        )
        with factory() as session:
            processed = session.get(
                ProcessedEvent,
                (discovered.event_id, NORMALIZER_CONSUMER_GROUP),
            )
            normalized_count = session.scalar(
                select(func.count())
                .select_from(EventOutbox)
                .where(EventOutbox.event_type == EventType.ARTICLE_NORMALIZED.value)
            )
        # Clear the worker's downstream pending outbox intent before isolating the
        # dispatcher diagnostic probe below.
        await dispatcher.dispatch_once()
        loss = {
            "initial_published": first.published,
            "initial_stream_length": before,
            "durable_outbox_retained": retained,
            "ordinary_dispatcher_claimed_after_loss": second.claimed,
            "dry_run_replay_required": dry.replay_required,
            "apply_replayed": applied.replayed,
            "original_event_id_preserved": any(
                item.event_id == discovered.event_id and item.replayed for item in applied.items
            ),
            "consumer_processed": consumed.processed,
            "processed_event_recorded": processed is not None,
            "normalized_effect_count": normalized_count,
            "second_reconciliation_replayed": converged.replayed,
            "second_reconciliation_already_processed": converged.already_processed,
        }
        sentinel = "SYNTHETIC_ACCEPTANCE_SECRET_DO_NOT_LOG"

        class UnsafeFailure:
            async def publish(self, envelope):
                raise RuntimeError(f"authorization=Bearer {sentinel}")

        failure_row = build_outbox_record(event())
        with factory() as session, session.begin():
            session.add(failure_row)
        failed = await OutboxDispatcher(factory, UnsafeFailure()).dispatch_once()
        with factory() as session:
            error = session.get(EventOutbox, failure_row.id).last_error or ""
        assert error == "OUTBOX_TRANSPORT_FAILED:INTERNAL"
        assert sentinel not in error
        return {
            "redis_transport_loss": loss,
            "outbox_error_sanitization": {
                "retry_recorded": failed.retried == 1,
                "synthetic_secret_retained_in_db": sentinel in error,
                "diagnostic_length": len(error),
            },
            "corpus_search_samples": await corpus_samples(factory),
        }
    finally:
        await acquisition.close()
        await client.delete(stream)
        await client.aclose()


async def corpus_samples(factory):
    with factory() as session, session.begin():
        source = Source(name="Synthetic corpus", source_type="NEWS", authority_level=4)
        session.add(source)
        session.flush()
        source_id = source.id
    previous = 0
    samples = []
    provider = PostgresArticleSearchProvider(factory)
    request = SearchRequest(
        query="synthetic gauge reading",
        query_family=SearchQueryFamily.ENTITY_EVENT,
        capability=SearchCapability.NEWS,
        max_results=10,
    )
    for total in (100, 1000, 10000):
        with factory() as session, session.begin():
            for index in range(previous, total):
                article_id = uuid4()
                body = f"Synthetic gauge reading record {index}. " + "Context only. " * 40
                session.add(
                    Article(
                        id=article_id,
                        source_id=source_id,
                        canonical_url=f"https://example.org/acceptance/{index}",
                        title=f"Synthetic gauge {index}",
                        language="en",
                    )
                )
                session.add(
                    ArticleVersion(
                        article_id=article_id,
                        version_number=1,
                        body=body,
                        content_hash=sha256(body.encode()).hexdigest(),
                        retrieved_at=datetime.now(UTC),
                    )
                )
        tracemalloc.start()
        started = time.perf_counter()
        response = await provider.search(request)
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        samples.append(
            {
                "eligible_articles": total,
                "body_bytes_approx": 600,
                "returned": len(response.results),
                "elapsed_seconds": round(elapsed, 3),
                "peak_python_allocated_mib": round(peak / 1024**2, 2),
            }
        )
        previous = total
    return samples


def main():
    database_url = make_url(os.environ["NEWS_AI_DATABASE_URL"])
    redis_url = make_url(os.environ["NEWS_AI_REDIS_URL"])
    if (
        os.environ.get("NEWS_AI_ENVIRONMENT") != "test"
        or database_url.host not in {"127.0.0.1", "localhost"}
        or redis_url.host not in {"127.0.0.1", "localhost"}
        or database_url.get_backend_name() != "postgresql"
    ):
        raise RuntimeError("explicit local PostgreSQL/Redis test services required")
    name = f"acceptance_probe_{uuid4().hex}"
    admin_url = database_url.set(drivername="postgresql", database="postgres")
    with psycopg.connect(admin_url.render_as_string(hide_password=False), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        engine = create_engine(database_url.set(database=name))
        try:
            Base.metadata.create_all(engine)
            factory = sessionmaker(engine, expire_on_commit=False)
            print(json.dumps(asyncio.run(probe(factory, str(redis_url))), indent=2))
        finally:
            engine.dispose()
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


if __name__ == "__main__":
    main()
