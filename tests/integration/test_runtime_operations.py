import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from integration import test_publication_execution
from news_ai_api.main import create_app
from news_ai_common.config import AppSettings
from news_ai_database import AuditLog, EventOutbox, Job, RuntimeControl
from news_ai_domain import PublicationStatus
from news_ai_events import OutboxDispatcher, RedisStreamPublisher
from news_ai_publisher.composition import build_production_publisher_stack
from news_ai_runtime.cli import main
from news_ai_runtime.health import build_monitor
from news_ai_runtime.metrics import render_metrics
from news_ai_runtime.publishing import DatabasePublishingControl
from news_ai_scheduler import build_production_scheduler_stack
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from unit.publishing.test_execution import setup_execution

SENTINEL = "SUPER_SECRET_RUNTIME_TOKEN_123"
factory = test_publication_execution.factory


@pytest.mark.parametrize("hard_pause", [False, True])
def test_durable_pause_resume_without_process_restart_and_safe_pending(
    factory, monkeypatch, hard_pause
):
    redis_url = os.getenv("NEWS_AI_REDIS_URL")
    if not redis_url:
        pytest.skip("isolated Redis service required")
    _, clock, _, _, _, row, _, service, _, _ = setup_execution(factory)
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")
    control = DatabasePublishingControl(factory, clock=clock)
    settings = AppSettings(environment="test", config_dir="config")
    scheduler = build_production_scheduler_stack(settings, session_factory=factory, clock=clock)

    async def run():
        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            await client.delete("news:publishing")
            publisher = build_production_publisher_stack(
                settings, session_factory=factory, redis_client=client, clock=clock
            )
            publisher.worker.consumer.block_ms = 1
            await publisher.worker.ensure_ready()
            assert (
                await OutboxDispatcher(factory, RedisStreamPublisher(client)).dispatch_once()
            ).published == 1
            assert (
                main(["publish", "pause", "--reason", "maintenance " + SENTINEL], control=control)
                == 0
            )
            assert scheduler.scheduler.paused()
            assert scheduler.scheduler.scan() == 0
            _, _, _, fresh = await publisher.worker.run_batch()
            assert fresh.retrying == 1
            assert service.get(row.id).status == PublicationStatus.SCHEDULED
            assert service.attempts(row.id) == ()
            assert (await client.xpending("news:publishing", "publisher"))["pending"] == 1
            if hard_pause:
                monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "true")
            assert main(["publish", "resume", "--reason", "complete"], control=control) == 0
            assert not control.snapshot().database_pause
            if hard_pause:
                _, resumed, _, _ = await publisher.worker.run_batch()
                assert resumed.retrying == 1 and service.attempts(row.id) == ()
                assert control.snapshot().environment_pause and scheduler.scheduler.paused()
                monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")
            assert not scheduler.scheduler.paused()
            _, resumed, _, _ = await publisher.worker.run_batch()
            assert resumed.processed == 1
            assert service.get(row.id).status == PublicationStatus.PUBLISHED
            assert len(service.attempts(row.id)) == 1
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
                audits = list(
                    session.scalars(
                        select(AuditLog).where(AuditLog.artifact_type == "runtime_control")
                    )
                )
                assert len(audits) == 2 and SENTINEL not in str([audit.reason for audit in audits])
        finally:
            await client.xgroup_destroy("news:publishing", "publisher")
            await client.aclose()

    asyncio.run(run())


def test_control_failure_fails_closed_before_any_provider_call(factory, monkeypatch):
    _, _, _, _, _, row, event, service, adapter, execution = setup_execution(factory)
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")

    def unavailable():
        raise RuntimeError(SENTINEL)

    control = DatabasePublishingControl(unavailable)
    execution.paused = control.paused
    from news_ai_events.reliability import DeferredWorkError

    with pytest.raises(DeferredWorkError):
        asyncio.run(execution.execute(event))
    assert adapter.preparations == adapter.publishes == 0
    assert service.get(row.id).status == PublicationStatus.SCHEDULED
    assert service.attempts(row.id) == ()
    assert not control.snapshot().available and control.snapshot().effective_pause


def test_concurrent_control_creation_and_transitions_have_unique_revision_audits(
    factory, monkeypatch
):
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")
    control = DatabasePublishingControl(factory)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda value: control.set_paused(False, reason="resume"), range(4)))
    assert control.snapshot().revision == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda value: control.set_paused(True, reason="pause"), range(4)))
    assert control.snapshot().revision == 2
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda value: control.set_paused(value, reason="operator"),
                (True, False, True, False),
            )
        )
    with factory() as session:
        row = session.scalar(select(RuntimeControl))
        versions = list(
            session.scalars(select(AuditLog.artifact_version).order_by(AuditLog.artifact_version))
        )
        assert versions == list(range(1, row.revision + 1))
        assert len(versions) == len(set(versions))


@pytest.mark.parametrize(
    "assignment", ["control_key='ARBITRARY'", "revision=0", "reason=''", "boolean_value=NULL"]
)
def test_runtime_control_postgres_constraints(factory, assignment):
    DatabasePublishingControl(factory).set_paused(False, reason="test")
    with pytest.raises(IntegrityError), factory() as session, session.begin():
        session.execute(text(f"UPDATE runtime_controls SET {assignment}"))


def test_metrics_real_postgres_redis_aggregates_and_no_privileged_http(factory, monkeypatch):
    redis_url = os.getenv("NEWS_AI_REDIS_URL")
    if not redis_url:
        pytest.skip("Redis required")
    _, _, _, _, _, row, event, _, adapter, execution = setup_execution(factory)
    from news_ai_social import SocialAdapterError, SocialErrorClass

    adapter.failure = SocialAdapterError(SENTINEL, classification=SocialErrorClass.AUTHENTICATION)
    asyncio.run(execution.execute(event))
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")
    with factory() as session, session.begin():
        for _ in range(2):
            session.add(Job(job_type="TEST", status="PENDING", payload={"secret": SENTINEL}))
        session.add(Job(job_type="TEST", status=SENTINEL, payload={}))

    async def run():
        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            await client.delete("news:publishing")
            await client.xgroup_create("news:publishing", "publisher", id="0", mkstream=True)
            await client.xadd("news:publishing", {"secret": SENTINEL})
            await client.xreadgroup("publisher", "metrics-test", {"news:publishing": ">"}, count=1)
            monitor = build_monitor(
                AppSettings(environment="test", service_manager="manual"),
                factory=factory,
                redis_client=client,
            )
            snapshot = await monitor.collect(deep=False)
            payload = render_metrics(snapshot)
            assert b'news_ai_jobs{status="PENDING"} 2.0' in payload
            assert b'news_ai_jobs{status="OTHER"} 1.0' in payload
            assert (
                b'news_ai_stream_pending{group="publisher",stream="news:publishing"} 1.0' in payload
            )
            assert b'news_ai_publications{platform="INSTAGRAM",status="BLOCKED"} 1.0' in payload
            assert (
                b'news_ai_publication_attempts{error_class="PERMANENT",status="BLOCKED"} 1.0'
                in payload
            )
            assert b'error_class="AUTHENTICATION"' not in payload
            assert b'news_ai_social_accounts{platform="INSTAGRAM",status="ACTIVE"} 1.0' in payload
            assert b'news_ai_outbox_events{status="PENDING"} 2.0' in payload
            assert SENTINEL.encode() not in payload and str(row.id).encode() not in payload
            return payload
        finally:
            await client.xgroup_destroy("news:publishing", "publisher")
            await client.aclose()

    payload = asyncio.run(run())

    async def metrics_probe(settings):
        return payload

    async def ready(settings):
        return {name: True for name in ("postgres", "redis", "ai_router")}

    app = create_app(
        AppSettings(environment="test", database_url=None),
        metrics_probe=metrics_probe,
        readiness_probe=ready,
    )
    with TestClient(app) as client:
        assert client.get("/metrics").status_code == 200
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json() == {
            "status": "ready",
            "postgres": True,
            "redis": True,
            "ai_router": True,
        }
        assert all(not route.path.startswith("/service") for route in app.routes)
