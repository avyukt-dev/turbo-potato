import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest
from news_ai_database import Base, EventOutbox, Publication, Story
from news_ai_domain import PublicationStatus
from news_ai_events import EventEnvelope, OutboxDispatcher, RedisStreamPublisher
from redis.asyncio import Redis
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from unit.publishing.test_scheduler import Clock, request, seed_candidate, stack


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


def test_two_scanners_and_creation_replays_are_transactionally_unique(factory):
    clock = Clock()
    variant, account, actor = seed_candidate(factory)
    service, scheduler = stack(factory, clock)
    body = request(variant, account, clock)
    barrier = Barrier(2)

    def create():
        barrier.wait()
        return service.create(body, actor)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create) for _ in range(2)]
        rows = [future.result(timeout=15) for future in futures]
    assert rows[0].id == rows[1].id
    clock.now += timedelta(hours=1)
    barrier = Barrier(2)

    def scan():
        barrier.wait()
        return scheduler.scan()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(scan) for _ in range(2)]
        assert sum(future.result(timeout=15) for future in futures) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Publication)) == 1
        assert session.scalar(select(func.count()).select_from(EventOutbox)) == 1


def test_real_postgres_redis_scheduling_handoff_stops_before_execution(factory):
    redis_url = os.getenv("NEWS_AI_REDIS_URL")
    if not redis_url:
        pytest.skip("Redis test service required")
    clock = Clock()
    variant, account, actor = seed_candidate(factory)
    service, scheduler = stack(factory, clock)
    correlation = uuid4()
    row = service.create(request(variant, account, clock), actor, correlation_id=correlation)
    assert scheduler.scan() == 0
    clock.now += timedelta(hours=1)
    assert scheduler.scan() == 1
    assert scheduler.scan() == 0

    async def dispatch():
        client = Redis.from_url(redis_url, decode_responses=True)
        group = f"scheduler-proof-{uuid4()}"
        try:
            await client.xgroup_create("news:publishing", group, id="$", mkstream=True)
            dispatcher = OutboxDispatcher(factory, RedisStreamPublisher(client))
            stats = await dispatcher.dispatch_once()
            assert stats.published == 1
            messages = await client.xreadgroup(group, "test", {"news:publishing": ">"}, count=10)
            assert len(messages[0][1]) == 1
            message_id, fields = messages[0][1][0]
            envelope = EventEnvelope.model_validate_json(fields["event"])
            assert envelope.event_type.value == "publication.scheduled"
            assert envelope.aggregate_id == row.id and envelope.correlation_id == correlation
            assert envelope.payload["publication_id"] == str(row.id)
            # Durable intent and state exist before transport acknowledgement.
            durable = service.get(row.id)
            assert durable.status == PublicationStatus.SCHEDULED
            assert durable.scheduled_event_id == envelope.event_id
            await client.xack("news:publishing", group, message_id)
        finally:
            await client.xgroup_destroy("news:publishing", group)
            await client.aclose()

    asyncio.run(dispatch())
    assert "publication_attempts" not in Base.metadata.tables
    assert "external_post_id" not in Publication.__table__.columns


@pytest.mark.parametrize("first", ["cancel", "facts"])
def test_cancel_or_factual_update_commits_before_dispatch_revalidation(factory, first):
    clock = Clock()
    variant, account, actor = seed_candidate(factory)
    service, scheduler = stack(factory, clock)
    row = service.create(request(variant, account, clock), actor)
    clock.now += timedelta(hours=1)
    entered, release = Event(), Event()
    original = service.revalidate

    def paused_revalidation(session, publication):
        entered.set()
        assert release.wait(10)
        return original(session, publication)

    if first == "facts":
        # Hold the same Story serialization lock used by FactSheetGenerator.
        with factory() as session, session.begin():
            from news_ai_database import ContentDraft, ContentVariant

            draft = session.get(ContentDraft, session.get(ContentVariant, variant).content_draft_id)
            session.scalar(select(Story).where(Story.id == draft.story_id).with_for_update())
            service.revalidate = paused_revalidation
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(scheduler.scan)
                assert entered.wait(10)
                from news_ai_database import FactSheet

                old = session.get(FactSheet, draft.fact_sheet_id)
                session.add(
                    FactSheet(
                        story_id=old.story_id,
                        version=2,
                        headline="Correction",
                        summary="New facts",
                        risk_level=old.risk_level,
                        sensitive_topics=[],
                        semantic_key=str(uuid4()),
                    )
                )
                session.commit()
                release.set()
                assert future.result(timeout=15) == 0
        assert service.get(row.id).status == PublicationStatus.BLOCKED
    else:
        service.cancel(row.id, actor)
        assert scheduler.scan() == 0
        assert service.get(row.id).status == PublicationStatus.CANCELLED
    with factory() as session:
        assert session.scalar(select(EventOutbox)) is None


def test_scheduler_event_then_cancellation_serializes_and_keeps_history(factory):
    clock = Clock()
    variant, account, actor = seed_candidate(factory)
    service, scheduler = stack(factory, clock)
    row = service.create(request(variant, account, clock), actor)
    clock.now += timedelta(hours=1)
    entered, release = Event(), Event()
    original = service.revalidate

    def paused(session, publication):
        original(session, publication)
        entered.set()
        assert release.wait(10)

    service.revalidate = paused
    with ThreadPoolExecutor(max_workers=2) as executor:
        scan = executor.submit(scheduler.scan)
        assert entered.wait(10)
        cancellation = executor.submit(service.cancel, row.id, actor)
        release.set()
        assert scan.result(timeout=15) == 1
        assert cancellation.result(timeout=15).status == PublicationStatus.CANCELLED
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EventOutbox)) == 1
    assert service.get(row.id).scheduled_event_id is not None


def test_outbox_failure_rolls_back_marker_and_does_not_claim_dispatch(factory):
    clock = Clock()
    variant, account, actor = seed_candidate(factory)
    service, scheduler = stack(factory, clock)
    row = service.create(request(variant, account, clock), actor)
    clock.now += timedelta(hours=1)
    engine = factory.kw["bind"]

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO event_outbox"):
            raise RuntimeError("injected outbox failure")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError):
            scheduler.scan()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert service.get(row.id).scheduled_event_id is None
    assert scheduler.scan() == 1


@pytest.mark.parametrize(
    "assignment",
    [
        "status = 'BOGUS'",
        "content_variant_version = 0",
        "fact_sheet_version = 0",
        "scheduled_at = NULL",
        "status = 'CANCELLED'",
    ],
)
def test_postgres_rejects_invalid_publication_writes(factory, assignment):
    clock = Clock()
    variant, account, actor = seed_candidate(factory)
    service, _ = stack(factory, clock)
    row = service.create(request(variant, account, clock), actor)
    with pytest.raises(IntegrityError), factory() as session, session.begin():
        session.execute(
            text(f"UPDATE publications SET {assignment} WHERE id = :id"), {"id": row.id}
        )


def test_postgres_account_lifecycle_values_are_enforced(factory):
    from news_ai_database import SocialAccount, SocialAccountStatus

    _, account, _ = seed_candidate(factory)
    for status in SocialAccountStatus:
        with factory() as session, session.begin():
            session.execute(
                text("UPDATE social_accounts SET status = :status WHERE id = :id"),
                {"id": account, "status": status.value},
            )
        with factory() as session:
            assert session.get(SocialAccount, account).status == status
    with pytest.raises(IntegrityError), factory() as session, session.begin():
        session.execute(
            text("UPDATE social_accounts SET status = 'BOGUS' WHERE id = :id"), {"id": account}
        )
