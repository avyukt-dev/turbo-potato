"""Durable Telegram automation recovery on real PostgreSQL and Redis."""

import asyncio
import os
from uuid import UUID

import pytest
from news_ai_api.telegram_review import (
    TELEGRAM_REVIEW_CONSUMER_GROUP,
    TelegramReviewNotificationWorker,
)
from news_ai_database import Base, ContentVariant, ProcessedEvent
from news_ai_database.models import OutboxStatus
from news_ai_events import EventEnvelope, EventType
from news_ai_events.outbox import build_outbox_record
from redis.asyncio import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unit.review.test_review_service import seed_reviewable


def test_published_quality_event_recovers_after_redis_stream_loss() -> None:
    database_url = os.getenv("NEWS_AI_DATABASE_URL")
    redis_url = os.getenv("NEWS_AI_REDIS_URL")
    if not database_url or not redis_url or os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("explicit disposable PostgreSQL 16 / Redis 7 test services required")

    engine = create_engine(database_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    with engine.begin() as connection:
        tables = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
        connection.exec_driver_sql(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")
    variant_id = seed_reviewable(factory)[0]
    with factory() as session, session.begin():
        variant = session.get(ContentVariant, variant_id)
        event = EventEnvelope(
            event_type=EventType.CONTENT_QUALITY_CHECKED,
            producer="ai-worker",
            producer_version="0.1.0",
            aggregate_type="content_draft",
            aggregate_id=variant.content_draft_id,
            idempotency_key=f"content.quality_checked:{variant.content_draft_id}:redis-loss",
            payload={
                "content_draft_id": str(variant.content_draft_id),
                "passed": True,
                "fact_check_passed": True,
                "source_check_passed": True,
                "style_check_passed": True,
                "risk_level": "LOW",
                "review_required": True,
            },
        )
        row = build_outbox_record(event)
        row.status = OutboxStatus.PUBLISHED
        session.add(row)

    class Consumer:
        stream = "news:content"
        group = TELEGRAM_REVIEW_CONSUMER_GROUP

    class Controller:
        sent: list[tuple[UUID, int]] = []

        async def send_review(self, artifact_id, version):
            self.sent.append((artifact_id, version))
            return True

    async def scenario() -> None:
        redis = Redis.from_url(redis_url, decode_responses=True)
        try:
            await redis.delete("news:content")
            controller = Controller()
            worker = TelegramReviewNotificationWorker(Consumer(), factory, controller)  # type: ignore[arg-type]
            assert await worker.recover_durable_once() == 1
            assert await worker.recover_durable_once() == 0
            assert controller.sent == [(variant_id, 1)]
        finally:
            await redis.aclose()

    asyncio.run(scenario())
    with factory() as session:
        assert session.get(ProcessedEvent, (event.event_id, TELEGRAM_REVIEW_CONSUMER_GROUP))
    engine.dispose()
