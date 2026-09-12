import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from news_ai_database import (
    EventDeadLetter,
    EventOutbox,
    EventProcessingAttempt,
    ProcessedEvent,
    PublicationAttempt,
    SocialAccount,
    SocialAccountStatus,
)
from news_ai_database import (
    PublicationAttemptPhase as Phase,
)
from news_ai_domain import PublicationStatus
from news_ai_events import StreamMessage
from news_ai_events.reliability import DeferredWorkError
from news_ai_publisher import PublisherWorker
from news_ai_publisher.runner import run
from news_ai_publishing import PublicationError
from news_ai_social import InstagramContainerStatus as ContainerStatus
from news_ai_social import InstagramContainerStatusResult, SocialAdapterError, SocialErrorClass
from sqlalchemy import func, select
from unit.publishing.test_execution import setup_execution


@pytest.mark.parametrize("kind", [SocialErrorClass.AUTHENTICATION, SocialErrorClass.PERMISSION])
def test_remediated_blocked_pre_intent_manual_retry(kind):
    _, _, _, _, actor, row, event, service, adapter, execution = setup_execution()
    adapter.failure = SocialAdapterError("safe", classification=kind)
    asyncio.run(execution.execute(event))
    assert service.get(row.id).status == PublicationStatus.BLOCKED
    assert service.attempts(row.id)[0].phase == Phase.PREPARING
    adapter.failure = None  # operator repairs provider credentials/configuration
    service.retry(row.id, actor, idempotency_key="repaired")
    service.retry(row.id, actor, idempotency_key="repaired")
    asyncio.run(execution.retry_due())
    assert service.get(row.id).status == PublicationStatus.PUBLISHED
    assert len(service.attempts(row.id)) == 2 and adapter.publishes == 1


def test_blocked_retry_still_revalidates_account():
    factory, _, _, account, actor, row, event, service, adapter, execution = setup_execution()
    adapter.failure = SocialAdapterError("safe", classification=SocialErrorClass.AUTHENTICATION)
    asyncio.run(execution.execute(event))
    with factory() as session, session.begin():
        session.get(SocialAccount, account).status = SocialAccountStatus.AUTH_ERROR
    with pytest.raises(PublicationError):
        service.retry(row.id, actor, idempotency_key="not-repaired")
    with factory() as session, session.begin():
        session.get(SocialAccount, account).status = SocialAccountStatus.ACTIVE
    adapter.failure = None
    service.retry(row.id, actor, idempotency_key="repaired")
    asyncio.run(execution.retry_due())
    assert adapter.publishes == 1


def test_account_blocked_before_attempt_is_recoverable_after_repair():
    factory, _, _, account, actor, row, event, service, adapter, execution = setup_execution()
    with factory() as session, session.begin():
        session.get(SocialAccount, account).status = SocialAccountStatus.AUTH_ERROR
    asyncio.run(execution.execute(event))
    assert service.attempts(row.id) == ()
    assert service.get(row.id).status == PublicationStatus.BLOCKED
    with factory() as session, session.begin():
        session.get(SocialAccount, account).status = SocialAccountStatus.ACTIVE
    service.retry(row.id, actor, idempotency_key="fixed-account")
    asyncio.run(execution.retry_due())
    assert adapter.publishes == adapter.preparations == 1


def test_publisher_module_has_executable_secret_safe_startup_boundary():
    import os
    import subprocess
    import sys

    env = {
        **os.environ,
        "NEWS_AI_ENVIRONMENT": "test",
        "NEWS_AI_DATABASE_URL": "",
        "NEWS_AI_REDIS_URL": "",
    }
    result = subprocess.run(
        [sys.executable, "-m", "news_ai_publisher"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert "publisher startup or process unavailable" in result.stderr
    assert "Traceback" not in result.stderr


def recovered_prepared(factory=None):
    values = setup_execution(factory)
    factory, clock, _, _, _, _, event, _, adapter, execution = values
    claim = execution.claim(event)
    prepared = asyncio.run(adapter.prepare_publication(claim.request))
    with factory() as session, session.begin():
        attempt = session.get(PublicationAttempt, claim.attempt_id)
        attempt.phase = Phase.PREPARED
        attempt.provider_operation_id = prepared.container_id
        attempt.provider_metadata = {
            "child_container_ids": list(prepared.child_container_ids),
            "mock": True,
        }
    clock.now += timedelta(seconds=execution.config.lease_seconds + 1)
    return values


@pytest.mark.parametrize("status", list(ContainerStatus))
def test_recovered_prepared_status_checked_before_intent(status):
    factory, clock, _, _, actor, row, event, service, adapter, execution = recovered_prepared()
    calls = []

    async def check(container_id):
        with factory() as session:
            assert session.scalar(select(PublicationAttempt)).phase == Phase.PREPARED
        calls.append(container_id)
        return InstagramContainerStatusResult(container_id=container_id, status=status)

    adapter.get_container_status = check
    if status in {ContainerStatus.IN_PROGRESS, ContainerStatus.UNKNOWN}:
        with pytest.raises(DeferredWorkError):
            asyncio.run(execution.execute(event))
        assert adapter.publishes == 0 and service.attempts(row.id)[0].phase == Phase.PREPARED
        assert service.attempts(row.id)[0].completed_at is None
        assert len(calls) <= execution.config.verification_max_attempts
    else:
        asyncio.run(execution.execute(event))
        assert calls
        if status == ContainerStatus.FINISHED:
            assert adapter.publishes == 1 and adapter.preparations == 1
        elif status == ContainerStatus.PUBLISHED:
            assert adapter.publishes == 0 and service.attempts(row.id)[0].ambiguous
            with pytest.raises(PublicationError):
                service.retry(row.id, actor, idempotency_key="unsafe")
        else:
            assert adapter.publishes == 0
            assert service.get(row.id).status == PublicationStatus.RETRYING
            assert service.attempts(row.id)[0].phase == Phase.PREPARED
            clock.now += timedelta(seconds=30)
            asyncio.run(execution.retry_due())
            assert adapter.preparations == 2 and adapter.publishes == 1


def test_recovered_in_progress_becomes_finished_before_intent():
    *_, row, event, service, adapter, execution = recovered_prepared()
    statuses = iter([ContainerStatus.IN_PROGRESS, ContainerStatus.FINISHED])

    async def check(identifier):
        return InstagramContainerStatusResult(container_id=identifier, status=next(statuses))

    adapter.get_container_status = check
    asyncio.run(execution.execute(event))
    assert service.get(row.id).status == PublicationStatus.PUBLISHED
    assert adapter.preparations == adapter.publishes == 1


def test_pause_during_preparation_preserves_prepared_resume():
    *_, row, event, service, adapter, execution = setup_execution()
    original = adapter.prepare_publication

    async def prepare(request):
        result = await original(request)
        execution.paused = lambda: True
        return result

    adapter.prepare_publication = prepare
    with pytest.raises(DeferredWorkError):
        asyncio.run(execution.execute(event))
    assert adapter.publishes == 0
    assert service.attempts(row.id)[0].phase == Phase.PREPARED
    execution.paused = lambda: False
    asyncio.run(execution.execute(event))
    assert adapter.preparations == adapter.publishes == 1


def test_long_pause_does_not_exhaust_reliability_or_mark_processed():
    factory, _, _, _, _, row, event, service, adapter, execution = setup_execution()
    acknowledgements = []

    async def ack(message):
        acknowledgements.append(message)

    consumer = SimpleNamespace(stream="news:publishing", group="publisher", ack=ack)
    worker = PublisherWorker(consumer, execution)
    execution.paused = lambda: True
    message = StreamMessage("news:publishing", "1-0", event)

    async def exercise():
        for _ in range(10):
            result = await worker.reliability.process([message], worker._handle)
            assert result.retrying == 1 and result.dead_lettered == 0
        assert service.get(row.id).status == PublicationStatus.SCHEDULED
        with factory() as session:
            for model in (
                EventDeadLetter,
                EventProcessingAttempt,
                ProcessedEvent,
                PublicationAttempt,
            ):
                assert session.scalar(select(func.count()).select_from(model)) == 0
            assert session.scalar(select(func.count()).select_from(EventOutbox)) == 1
        assert not acknowledgements and adapter.preparations == 0
        execution.paused = lambda: False
        assert (await worker.reliability.process([message], worker._handle)).processed == 1
        assert len(acknowledgements) == 1 and adapter.publishes == 1

    asyncio.run(exercise())


def test_runner_retries_safely_waits_and_closes_without_secret_logging(caplog):
    calls = []

    async def ensure():
        calls.append("ensure")

    async def batch():
        calls.append("batch")
        if calls.count("batch") == 1:
            raise RuntimeError("DO_NOT_LOG_SECRET")

    async def wait(seconds):
        assert seconds == 5
        calls.append("wait")

    async def close(stack):
        calls.append("close")

    stack = SimpleNamespace(
        worker=SimpleNamespace(ensure_ready=ensure, run_batch=batch),
        service=SimpleNamespace(config=SimpleNamespace(loop_interval_seconds=5)),
    )
    asyncio.run(
        run(stack, should_stop=lambda: calls.count("batch") == 2, wait=wait, shutdown=close)
    )
    assert calls == ["ensure", "batch", "wait", "batch", "close"]
    assert "DO_NOT_LOG_SECRET" not in caplog.text


def test_runner_cancellation_closes_resources():
    closed = []

    async def ensure():
        pass

    async def batch():
        raise asyncio.CancelledError

    async def close(stack):
        closed.append(True)

    stack = SimpleNamespace(worker=SimpleNamespace(ensure_ready=ensure, run_batch=batch))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run(stack, shutdown=close))
    assert closed == [True]
