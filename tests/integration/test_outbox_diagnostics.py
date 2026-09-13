"""Real PostgreSQL round-trip of bounded dispatcher diagnostics and retry state."""

import asyncio
import os

import pytest
from news_ai_database.models import EventOutbox, OutboxStatus
from news_ai_events.dispatcher import OutboxDispatcher, RetryPolicy
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker
from unit.events.test_dispatcher import SECRET, FakePublisher, RaisingPublisher, _insert_event


@pytest.mark.skipif(
    not os.getenv("NEWS_AI_DATABASE_URL"), reason="PostgreSQL dependency is not configured"
)
def test_postgres_safe_failure_reload_and_success(caplog):
    engine = create_engine(os.environ["NEWS_AI_DATABASE_URL"])
    assert engine.dialect.name == "postgresql"
    factory = sessionmaker(engine, expire_on_commit=False)
    record = _insert_event(factory)
    dispatcher = OutboxDispatcher(
        factory,
        RaisingPublisher(ConnectionError(f"Authorization: Bearer {SECRET}")),
        retry_policy=RetryPolicy(delays_seconds=(30,), jitter_ratio=0),
    )
    try:
        assert asyncio.run(dispatcher.dispatch_once()).retried >= 1
        with factory() as session, session.begin():
            row = session.get(EventOutbox, record.id)
            assert row.last_error == "OUTBOX_TRANSPORT_FAILED:CONNECTION"
            assert SECRET not in row.last_error
            assert row.status == OutboxStatus.PENDING
            assert row.attempt_count == 1
            assert row.next_attempt_at is not None
            row.next_attempt_at = None
        dispatcher.publisher = FakePublisher()
        assert asyncio.run(dispatcher.dispatch_once()).published >= 1
        with factory() as session:
            row = session.get(EventOutbox, record.id)
            assert row.status == OutboxStatus.PUBLISHED
            assert row.attempt_count == 2
            assert row.last_error is None
        assert SECRET not in caplog.text
    finally:
        with factory() as session, session.begin():
            session.execute(delete(EventOutbox).where(EventOutbox.id == record.id))
        engine.dispose()
