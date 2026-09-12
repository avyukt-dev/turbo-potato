import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from news_ai_common.config import AppSettings
from news_ai_database import Base, EventOutbox, Publication, PublicationAttempt, SocialAccount
from news_ai_domain import PublicationStatus
from news_ai_events import OutboxDispatcher, RedisStreamPublisher
from news_ai_events.reliability import TransientEventError
from news_ai_publisher.composition import build_production_publisher_stack
from news_ai_social import SocialSettings
from redis.asyncio import Redis
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from unit.publishing.test_execution import setup_execution
from unit.social.test_instagram_adapter import FakeTransport, _ok


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


def test_two_postgres_workers_have_one_execution_and_one_success(factory):
    _, _, _, _, _, row, event, service, adapter, execution = setup_execution(factory)
    barrier = Barrier(2)

    def run():
        barrier.wait()
        try:
            return asyncio.run(execution.execute(event))
        except TransientEventError:
            return "leased"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result(timeout=15) for future in [pool.submit(run), pool.submit(run)]]
    assert outcomes
    assert service.get(row.id).status == PublicationStatus.PUBLISHED
    assert adapter.publishes == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PublicationAttempt)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventOutbox)
                .where(EventOutbox.event_type == "publication.executed")
            )
            == 1
        )


@pytest.mark.parametrize(
    "assignment", ["attempt_count = -1", "status = 'PUBLISHED'", "status = 'RETRYING'"]
)
def test_postgres_execution_constraints_reject_invalid_direct_writes(factory, assignment):
    *_, row, _, _, _, _ = setup_execution(factory)
    with pytest.raises(IntegrityError), factory() as session, session.begin():
        session.execute(
            text(f"UPDATE publications SET {assignment} WHERE id = :id"), {"id": row.id}
        )


def test_postgres_one_active_attempt_constraint(factory):
    _, _, _, _, _, row, event, _, _, execution = setup_execution(factory)
    claim = execution.claim(event)
    with pytest.raises(IntegrityError), factory() as session, session.begin():
        existing = session.get(PublicationAttempt, claim.attempt_id)
        values = {
            column.name: getattr(existing, column.name)
            for column in PublicationAttempt.__table__.columns
            if column.name != "id"
        }
        values.update(attempt_number=2, lease_token=uuid4())
        session.add(PublicationAttempt(**values))
        session.flush()
    with factory() as session:
        assert session.get(Publication, row.id).attempt_count == 1


@pytest.mark.parametrize("mode", ["MOCK", "LIVE"])
def test_real_postgres_redis_execution_composition_and_ack(factory, monkeypatch, mode):
    redis_url = os.getenv("NEWS_AI_REDIS_URL")
    if not redis_url:
        pytest.skip("Redis test service required")
    _, clock, _, account, _, row, _, service, _, _ = setup_execution(factory)
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")
    with factory() as session, session.begin():
        session.get(SocialAccount, account).account_identifier = "123456"
    transport = FakeTransport(
        [
            _ok({"id": "101"}),
            _ok({"status_code": "FINISHED"}),
            _ok({"id": "102"}),
            _ok({"status_code": "FINISHED"}),
            _ok({"id": "201"}),
            _ok({"status_code": "FINISHED"}),
            _ok({"id": "301"}),
            _ok({"id": "301", "media_type": "CAROUSEL_ALBUM"}),
        ]
    )
    sentinel = "INSTAGRAM_SECRET_SENTINEL_NEVER_PERSIST"

    async def run():
        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            # This is the isolated repository test service, not production Redis.
            await client.delete("news:publishing")
            stack = build_production_publisher_stack(
                AppSettings(environment="test", config_dir="config"),
                social_settings=SocialSettings(
                    environment="production",
                    social_mode=mode,
                    publishing_enabled=True,
                    instagram_account_id="123456",
                    instagram_access_token=sentinel,
                ),
                session_factory=factory,
                redis_client=client,
                transport=transport,
                clock=clock,
            )
            stack.worker.consumer.block_ms = 1
            ack = stack.worker.consumer.ack

            async def durable_ack(message):
                if message.event.event_type.value == "publication.scheduled":
                    assert service.get(row.id).status == PublicationStatus.PUBLISHED
                    assert service.get(row.id).published_at
                await ack(message)

            stack.worker.consumer.ack = durable_ack
            await stack.worker.ensure_ready()
            dispatcher = OutboxDispatcher(factory, RedisStreamPublisher(client))
            assert (await dispatcher.dispatch_once()).published == 1
            monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "true")
            _, _, _, deferred = await stack.worker.run_batch()
            assert deferred.retrying == 1 and deferred.dead_lettered == 0
            assert service.get(row.id).status == PublicationStatus.SCHEDULED
            assert service.attempts(row.id) == ()
            assert (await client.xpending("news:publishing", "publisher"))["pending"] == 1
            assert transport.calls == []
            for _ in range(100):
                _, resumed, _, _ = await stack.worker.run_batch()
                assert resumed.retrying == 1
            from news_ai_database import EventDeadLetter, EventProcessingAttempt, ProcessedEvent

            with factory() as session:
                for model in (EventDeadLetter, EventProcessingAttempt, ProcessedEvent):
                    assert session.scalar(select(func.count()).select_from(model)) == 0
            assert transport.calls == [] and service.attempts(row.id) == ()
            assert stack.service.config.pending_reclaim_idle_ms == 7_200_000
            monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")
            _, first, _, _ = await stack.worker.run_batch()
            assert first.processed == 1
            assert service.get(row.id).status == PublicationStatus.PUBLISHED
            assert (await client.xpending("news:publishing", "publisher"))["pending"] == 0
            with factory() as session:
                scheduled = session.scalar(
                    select(EventOutbox).where(EventOutbox.event_type == "publication.scheduled")
                )
                from news_ai_events.outbox import envelope_from_outbox

                raw = envelope_from_outbox(scheduled).model_dump_json()
            for _ in range(10):
                await client.xadd("news:publishing", {"event": raw})
            assert (await stack.worker.run_once()).duplicates == 10
            assert (await dispatcher.dispatch_once()).published == 1
            assert (await stack.worker.run_once()).ignored == 1
            with factory() as session:
                records = tuple(session.scalars(select(EventOutbox)))
                assert len(records) == 2
                assert sentinel not in str([record.payload for record in records])
                assert sentinel not in str(service.attempts(row.id))
        finally:
            await client.xgroup_destroy("news:publishing", "publisher")
            await client.aclose()

    asyncio.run(run())
    if mode == "MOCK":
        assert transport.calls == []
    else:
        assert sum(path.endswith("/media_publish") for _, path, _ in transport.calls) == 1


def test_real_redis_reclaims_crashed_publisher_before_preparation(factory, monkeypatch):
    from datetime import timedelta

    redis_url = os.getenv("NEWS_AI_REDIS_URL")
    if not redis_url:
        pytest.skip("Redis test service required")
    _, clock, _, _, _, row, event, service, _, _ = setup_execution(factory)
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")

    async def run():
        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            await client.delete("news:publishing")
            stack = build_production_publisher_stack(
                AppSettings(environment="test", config_dir="config"),
                session_factory=factory,
                redis_client=client,
                clock=clock,
            )
            stack.worker.consumer.block_ms = 1
            await stack.worker.ensure_ready()
            await client.xadd("news:publishing", {"event": event.model_dump_json()})
            messages = await stack.worker.consumer.read()
            assert len(messages) == 1
            stack.service.claim(event)  # crash before provider call; original stays pending
            assert service.get(row.id).status == PublicationStatus.PUBLISHING
            clock.now += timedelta(seconds=stack.service.config.lease_seconds + 1)
            # XCLAIM establishes a deterministic idle time without sleeps.
            await client.xclaim(
                "news:publishing",
                "publisher",
                "dead-worker",
                0,
                [messages[0].message_id],
                idle=10000,
            )
            _, resumed, recovered, received = await stack.worker.run_batch()
            assert resumed.received == recovered.received == received.received == 0
            assert service.get(row.id).status == PublicationStatus.PUBLISHING
            _, result = await stack.worker.recover_once(min_idle_ms=1)
            assert result.processed == 1
            assert service.get(row.id).status == PublicationStatus.PUBLISHED
            assert (await client.xpending("news:publishing", "publisher"))["pending"] == 0
            assert len(service.attempts(row.id)) == 1
        finally:
            await client.xgroup_destroy("news:publishing", "publisher")
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("scenario", ["prepared_pause", "in_progress", "ack_failure"])
def test_normal_batch_resumes_prepared_and_ack_failures(factory, monkeypatch, scenario):
    from news_ai_database import PublicationAttemptPhase as Phase
    from news_ai_social import InstagramContainerStatus, InstagramContainerStatusResult

    redis_url = os.getenv("NEWS_AI_REDIS_URL")
    if not redis_url:
        pytest.skip("Redis test service required")
    _, clock, _, _, _, row, _, service, _, _ = setup_execution(factory)
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")

    async def run():
        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            await client.delete("news:publishing")
            stack = build_production_publisher_stack(
                AppSettings(environment="test", config_dir="config"),
                session_factory=factory,
                redis_client=client,
                clock=clock,
            )
            stack.worker.consumer.block_ms = 1
            stack.service.config = stack.service.config.model_copy(
                update={"verification_max_attempts": 1}
            )
            adapter = stack.service.adapter
            prepare, publish, check = (
                adapter.prepare_publication,
                adapter.publish_prepared,
                adapter.get_container_status,
            )
            calls = []

            async def wrapped_prepare(request):
                calls.append("prepare")
                result = await prepare(request)
                if scenario != "ack_failure":
                    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "true")
                return result

            async def wrapped_publish(prepared, **kwargs):
                calls.append("publish")
                return await publish(prepared, **kwargs)

            async def wrapped_check(identifier):
                calls.append("check")
                if scenario == "in_progress" and calls.count("check") == 1:
                    return InstagramContainerStatusResult(
                        container_id=identifier, status=InstagramContainerStatus.IN_PROGRESS
                    )
                return await check(identifier)

            adapter.prepare_publication = wrapped_prepare
            adapter.publish_prepared = wrapped_publish
            adapter.get_container_status = wrapped_check
            ack = stack.worker.consumer.ack

            async def failed_ack(message):
                raise ConnectionError("simulated ACK outage")

            await stack.worker.ensure_ready()
            assert (
                await OutboxDispatcher(factory, RedisStreamPublisher(client)).dispatch_once()
            ).published == 1
            if scenario == "ack_failure":
                stack.worker.consumer.ack = failed_ack
                with pytest.raises(ConnectionError):
                    await stack.worker.run_batch()
                assert service.get(row.id).status == PublicationStatus.PUBLISHED
                stack.worker.consumer.ack = ack
            else:
                await stack.worker.run_batch()
                attempt = service.attempts(row.id)[0]
                assert attempt.phase == Phase.PREPARED and calls == ["prepare"]
                with factory() as session:
                    assert session.get(PublicationAttempt, attempt.id).lease_expires_at == clock.now
                monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")
                if scenario == "in_progress":
                    _, resumed, _, _ = await stack.worker.run_batch()
                    assert resumed.retrying == 1 and calls == ["prepare", "check"]
                    assert service.attempts(row.id)[0].phase == Phase.PREPARED
            assert (await client.xpending("news:publishing", "publisher"))["pending"] == 1
            _, resumed, _, _ = await stack.worker.run_batch()
            assert resumed.duplicates == 1 if scenario == "ack_failure" else resumed.processed == 1
            assert service.get(row.id).status == PublicationStatus.PUBLISHED
            assert len(service.attempts(row.id)) == 1
            assert calls.count("prepare") == calls.count("publish") == 1
            if scenario != "ack_failure":
                assert calls.index("check") < calls.index("publish")
            assert (await client.xpending("news:publishing", "publisher"))["pending"] == 0
            with factory() as session:
                assert (
                    session.scalar(
                        select(func.count())
                        .select_from(EventOutbox)
                        .where(EventOutbox.event_type == "publication.executed")
                    )
                    == 1
                )
        finally:
            await client.xgroup_destroy("news:publishing", "publisher")
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("status", ["FINISHED", "EXPIRED", "PUBLISHED"])
def test_postgres_recovered_parent_status_before_intent(factory, status):
    from news_ai_social import InstagramContainerStatus, InstagramContainerStatusResult
    from unit.publishing.test_review_corrections import recovered_prepared

    _, _, _, _, actor, row, event, service, adapter, execution = recovered_prepared(factory)

    async def check(identifier):
        return InstagramContainerStatusResult(
            container_id=identifier, status=InstagramContainerStatus(status)
        )

    adapter.get_container_status = check
    asyncio.run(execution.execute(event))
    assert adapter.publishes == (1 if status == "FINISHED" else 0)
    if status == "PUBLISHED":
        from news_ai_publishing import PublicationError

        assert service.attempts(row.id)[0].ambiguous
        with pytest.raises(PublicationError):
            service.retry(row.id, actor, idempotency_key="must-not-replay")


@pytest.mark.parametrize("kind", ["AUTHENTICATION", "PERMISSION"])
def test_postgres_manual_retry_api_repairs_blocked_pre_intent(factory, kind):
    from news_ai_social import SocialAdapterError, SocialErrorClass
    from unit.publishing.test_publication_api import client

    _, _, _, _, actor, row, event, service, adapter, execution = setup_execution(factory)
    adapter.failure = SocialAdapterError("safe", classification=SocialErrorClass(kind))
    asyncio.run(execution.execute(event))
    assert service.get(row.id).status == PublicationStatus.BLOCKED
    adapter.failure = None
    api = client(service, actor)
    headers = {"Authorization": "Bearer synthetic-review-token", "Idempotency-Key": "repaired"}
    path = f"/api/v1/publications/{row.id}/retry"
    assert api.post(path).status_code == 401
    assert api.post(path, headers=headers).status_code == 200
    assert api.post(path, headers=headers).status_code == 200
    asyncio.run(execution.retry_due())
    assert service.get(row.id).status == PublicationStatus.PUBLISHED
    assert len(service.attempts(row.id)) == 2 and adapter.publishes == 1
