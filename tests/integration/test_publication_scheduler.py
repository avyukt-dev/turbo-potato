import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from news_ai_database import Base, EventOutbox, Publication, Story
from news_ai_domain import PublicationStatus
from news_ai_events import EventEnvelope, OutboxDispatcher, RedisStreamPublisher
from redis.asyncio import Redis
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.engine import make_url
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


def test_same_key_concurrent_publish_now_is_one_logical_mutation(factory):
    clock = Clock()
    variant, account, actor = seed_candidate(factory)
    service, _ = stack(factory, clock)
    row = service.create(request(variant, account, clock), actor)
    barrier = Barrier(2)

    def publish_now():
        barrier.wait()
        return service.publish_now(row.id, actor, idempotency_key="double-click")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=15) for future in [executor.submit(publish_now) for _ in range(2)]
        ]
    assert results[0].scheduled_at == results[1].scheduled_at
    with factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Publication).where(Publication.id == row.id)
            )
            == 1
        )
        from news_ai_database import AuditLog

        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "PUBLICATION_PUBLISH_NOW")
            )
            == 1
        )


def test_different_publish_now_keys_race_to_one_success(factory):
    clock = Clock()
    variant, account, actor = seed_candidate(factory)
    service, _ = stack(factory, clock)
    row = service.create(request(variant, account, clock), actor)
    barrier = Barrier(2)

    def publish_now(key):
        barrier.wait()
        try:
            return service.publish_now(row.id, actor, idempotency_key=key).scheduled_at
        except Exception as exc:  # returned for deterministic assertion outside the thread
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=15)
            for future in [
                executor.submit(publish_now, "first"),
                executor.submit(publish_now, "second"),
            ]
        ]
    from news_ai_publishing import PublicationError

    assert sum(not isinstance(result, Exception) for result in results) == 1
    errors = [result for result in results if isinstance(result, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], PublicationError)
    assert errors[0].code == "IDEMPOTENCY_CONFLICT"


def test_locked_media_revalidation_serializes_concurrent_url_mutation(factory, monkeypatch):
    import news_ai_publishing.service as service_module
    from news_ai_database import ContentVariant, MediaAsset
    from news_ai_review import ApprovalEligibilityService
    from unit.review.test_review_service import _policy

    clock = Clock()
    variant_id, account, actor = seed_candidate(factory)
    service, scheduler = stack(factory, clock)
    row = service.create(request(variant_id, account, clock), actor)
    clock.now += timedelta(hours=1)
    entered, release = Event(), Event()
    original = service_module.build_instagram_publication_request

    def paused(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(service_module, "build_instagram_publication_request", paused)

    def mutate_url():
        with factory() as session, session.begin():
            variant = session.get(ContentVariant, variant_id)
            asset = session.get(MediaAsset, UUID(variant.media_asset_ids[0]))
            asset.public_url = "https://media.example.org/after-lock.jpg"

    with ThreadPoolExecutor(max_workers=2) as executor:
        scan = executor.submit(scheduler.scan)
        assert entered.wait(10)
        mutation = executor.submit(mutate_url)
        release.set()
        assert scan.result(timeout=15) == 1
        mutation.result(timeout=15)
    assert not ApprovalEligibilityService(factory, _policy()).is_exact_version_approved(variant_id)
    assert service.get(row.id).scheduled_event_id is not None


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


def test_migration_0010_downgrades_to_the_true_0009_schema():
    database_url = os.getenv("NEWS_AI_DATABASE_URL")
    if not database_url:
        pytest.skip("PostgreSQL integration dependency is not configured")
    base_url = make_url(database_url)
    database_name = f"news_ai_publication_migration_{uuid4().hex}"
    test_url = base_url.set(database=database_name)
    admin = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    previous = os.environ.get("NEWS_AI_DATABASE_URL")
    os.environ["NEWS_AI_DATABASE_URL"] = test_url.render_as_string(hide_password=False)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "0009_human_review")
        engine = create_engine(test_url)
        assert inspect(engine).get_columns("audit_log")[1]["name"] == "actor_id"
        actor_column = next(
            column
            for column in inspect(engine).get_columns("audit_log")
            if column["name"] == "actor_id"
        )
        assert actor_column["nullable"] is False
        command.upgrade(config, "0010_publication_scheduler")
        actor_column = next(
            column
            for column in inspect(engine).get_columns("audit_log")
            if column["name"] == "actor_id"
        )
        assert actor_column["nullable"] is True
        audit_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, actor_id, action, artifact_type, artifact_id, artifact_version, "
                    "result, metadata) VALUES "
                    "(:id, NULL, 'PUBLICATION_BLOCKED', 'publication', :artifact_id, 1, "
                    "'BLOCKED', CAST(:metadata AS jsonb))"
                ),
                {"id": audit_id, "artifact_id": uuid4(), "metadata": "{}"},
            )
        command.downgrade(config, "0009_human_review")
        inspector = inspect(engine)
        assert {"publications", "social_accounts", "media_assets"}.isdisjoint(
            inspector.get_table_names()
        )
        actor_column = next(
            column for column in inspector.get_columns("audit_log") if column["name"] == "actor_id"
        )
        assert actor_column["nullable"] is False
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM audit_log WHERE id = :id"), {"id": audit_id}
                )
                == 0
            )
        command.upgrade(config, "head")
        command.check(config)
        engine.dispose()
    finally:
        if previous is None:
            os.environ.pop("NEWS_AI_DATABASE_URL", None)
        else:
            os.environ["NEWS_AI_DATABASE_URL"] = previous
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        admin.dispose()
