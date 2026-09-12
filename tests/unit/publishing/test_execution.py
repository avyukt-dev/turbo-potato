import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from news_ai_database import (
    EventOutbox,
    Publication,
    PublicationAttempt,
    PublicationAttemptStatus,
)
from news_ai_database import (
    PublicationAttemptPhase as Phase,
)
from news_ai_domain import PublicationStatus
from news_ai_events.outbox import envelope_from_outbox
from news_ai_events.reliability import PermanentEventError, TransientEventError
from news_ai_publishing import PublicationExecutionService, PublisherConfig
from news_ai_publishing.execution import as_utc
from news_ai_social import (
    MockInstagramAdapter,
    PublicationVerificationResult,
    PublicationVerificationStatus,
    SocialAdapterError,
    SocialErrorClass,
    load_instagram_config,
)
from sqlalchemy import func, select
from unit.publishing.test_scheduler import Clock, mutate, request, seed_candidate, stack
from unit.review.test_review_service import _factory


async def no_sleep(_):
    pass


class CountingAdapter(MockInstagramAdapter):
    def __init__(self):
        super().__init__(load_instagram_config("config"))
        self.preparations = self.publishes = self.verifications = 0
        self.failure = None
        self.after_publish = None
        self.verification_status = PublicationVerificationStatus.PUBLISHED

    async def prepare_publication(self, request):
        self.preparations += 1
        if self.failure:
            raise self.failure
        return await super().prepare_publication(request)

    async def publish_prepared(self, prepared, *, verify=True):
        self.publishes += 1
        result = await super().publish_prepared(prepared, verify=verify)
        if self.after_publish:
            self.after_publish()
        return result

    async def verify_publication(self, external_post_id):
        self.verifications += 1
        return PublicationVerificationResult(
            external_post_id=external_post_id,
            status=self.verification_status,
            mock=True,
        )


def setup_execution(factory=None, *, config=None):
    factory = factory or _factory()
    clock = Clock()
    variant, account, actor = seed_candidate(factory)
    service, scheduler = stack(factory, clock)
    row = service.create(request(variant, account, clock), actor, correlation_id=uuid4())
    clock.now += timedelta(hours=1)
    assert scheduler.scan() == 1
    with factory() as session:
        event = (
            envelope_from_outbox(
                session.scalar(
                    select(EventOutbox).where(EventOutbox.event_id == row.scheduled_event_id)
                )
            )
            if row.scheduled_event_id
            else envelope_from_outbox(session.scalar(select(EventOutbox)))
        )
    adapter = CountingAdapter()
    execution = PublicationExecutionService(
        service,
        adapter,
        config or PublisherConfig(),
        paused=lambda: False,
        sleeper=no_sleep,
        jitter=lambda: 0.5,
    )
    return factory, clock, variant, account, actor, row, event, service, adapter, execution


def test_mock_success_atomic_v2_nullable_url_and_replay():
    factory, _, _, _, _, row, event, service, adapter, execution = setup_execution()
    asyncio.run(execution.execute(event))
    result = service.get(row.id)
    assert result.status == PublicationStatus.PUBLISHED
    assert result.external_post_id and result.external_url is None
    assert result.published_at and result.attempt_count == 1
    asyncio.run(execution.execute(event))
    assert adapter.publishes == adapter.preparations == 1
    with factory() as session:
        events = tuple(
            session.scalars(
                select(EventOutbox).where(EventOutbox.event_type == "publication.executed")
            )
        )
        assert len(events) == 1
        output = envelope_from_outbox(events[0])
        assert output.schema_version == 2 and output.payload["external_url"] is None
        assert (
            output.causation_id == event.event_id and output.correlation_id == event.correlation_id
        )
        attempt = session.scalar(select(PublicationAttempt))
        assert attempt.status == PublicationAttemptStatus.SUCCEEDED
        assert attempt.phase == Phase.COMPLETE and attempt.execution_request_hash


@pytest.mark.parametrize(
    "mutation", ["facts", "wording", "version", "quality", "review", "account"]
)
def test_execution_revalidates_before_provider(mutation):
    factory, _, variant, account, _, row, event, service, adapter, execution = setup_execution()
    mutate(factory, variant, account, mutation)
    asyncio.run(execution.execute(event))
    assert service.get(row.id).status == PublicationStatus.BLOCKED
    assert adapter.publishes == adapter.preparations == 0
    assert service.attempts(row.id) == ()


def test_global_pause_blocks_before_adapter():
    *_, row, event, service, adapter, execution = setup_execution()
    execution.paused = lambda: True
    asyncio.run(execution.execute(event))
    assert service.get(row.id).status == PublicationStatus.BLOCKED
    assert adapter.preparations == 0


@pytest.mark.parametrize("kind", [SocialErrorClass.TRANSIENT, SocialErrorClass.RATE_LIMIT])
def test_pre_intent_retry_backoff_and_due_success(kind):
    _, clock, _, _, _, row, event, service, adapter, execution = setup_execution()
    adapter.failure = SocialAdapterError("safe", classification=kind, retry_after_seconds=90)
    asyncio.run(execution.execute(event))
    result = service.get(row.id)
    assert result.status == PublicationStatus.RETRYING
    assert as_utc(result.next_retry_at) == clock.now + timedelta(seconds=90)
    asyncio.run(execution.retry_due())
    assert adapter.preparations == 1
    clock.now += timedelta(seconds=90)
    adapter.failure = None
    asyncio.run(execution.retry_due())
    assert service.get(row.id).status == PublicationStatus.PUBLISHED
    assert adapter.preparations == 2 and adapter.publishes == 1


@pytest.mark.parametrize(
    "kind",
    [
        SocialErrorClass.AUTHENTICATION,
        SocialErrorClass.PERMISSION,
        SocialErrorClass.VALIDATION,
        SocialErrorClass.MEDIA,
    ],
)
def test_permanent_provider_failure_blocks_without_retry(kind):
    *_, row, event, service, adapter, execution = setup_execution()
    adapter.failure = SocialAdapterError("DO_NOT_PERSIST_SECRET", classification=kind)
    asyncio.run(execution.execute(event))
    assert service.get(row.id).status == PublicationStatus.BLOCKED
    assert not service.attempts(row.id)[0].retryable
    assert "DO_NOT_PERSIST_SECRET" not in str(service.attempts(row.id))
    asyncio.run(execution.retry_due())
    assert adapter.preparations == 1


def test_retry_exhaustion_preserves_attempts():
    config = PublisherConfig(max_attempts=3, retry_delays_seconds=(1, 2), jitter_ratio=0)
    _, clock, _, _, _, row, event, service, adapter, execution = setup_execution(config=config)
    adapter.failure = SocialAdapterError("safe", classification=SocialErrorClass.TRANSIENT)
    asyncio.run(execution.execute(event))
    for delay in (1, 2):
        clock.now += timedelta(seconds=delay)
        asyncio.run(execution.retry_due())
    assert service.get(row.id).status == PublicationStatus.FAILED
    assert len(service.attempts(row.id)) == 3 and adapter.preparations == 3


@pytest.mark.parametrize(
    "phase", [Phase.PREPARING, Phase.PREPARED, Phase.PUBLISH_INTENT_RECORDED, Phase.VERIFYING]
)
def test_crash_checkpoint_recovery_never_republishes_intent(phase):
    factory, clock, _, _, _, row, event, service, adapter, execution = setup_execution()
    claim = execution.claim(event)
    prepared = asyncio.run(adapter.prepare_publication(claim.request))
    with factory() as session, session.begin():
        attempt = session.get(PublicationAttempt, claim.attempt_id)
        attempt.phase = phase
        attempt.provider_operation_id = prepared.container_id
        attempt.provider_metadata = {
            "child_container_ids": list(prepared.child_container_ids),
            "mock": True,
        }
        if phase == Phase.VERIFYING:
            result = asyncio.run(adapter.publish_prepared(prepared, verify=False))
            attempt.external_post_id = result.external_post_id
            session.get(Publication, row.id).external_post_id = result.external_post_id
    clock.now += timedelta(seconds=execution.config.lease_seconds + 1)
    before = adapter.publishes
    asyncio.run(execution.execute(event))
    if phase == Phase.PUBLISH_INTENT_RECORDED:
        assert adapter.publishes == before
        assert service.get(row.id).status == PublicationStatus.BLOCKED
        assert service.attempts(row.id)[0].ambiguous
    elif phase == Phase.VERIFYING:
        assert adapter.publishes == before
        assert service.get(row.id).status == PublicationStatus.PUBLISHED
    else:
        assert adapter.publishes == before + 1
        assert service.get(row.id).status == PublicationStatus.PUBLISHED
        if phase == Phase.PREPARED:
            assert adapter.preparations == 1


@pytest.mark.parametrize(
    "status",
    [
        PublicationVerificationStatus.UNKNOWN,
        PublicationVerificationStatus.PROCESSING,
        PublicationVerificationStatus.NOT_FOUND,
        PublicationVerificationStatus.FAILED,
    ],
)
def test_known_id_uncertain_verification_blocks_without_republish(status):
    *_, row, event, service, adapter, execution = setup_execution()
    adapter.verification_status = status
    asyncio.run(execution.execute(event))
    assert service.get(row.id).status == PublicationStatus.BLOCKED
    assert service.get(row.id).external_post_id
    assert service.attempts(row.id)[0].ambiguous
    asyncio.run(execution.execute(event))
    assert adapter.publishes == 1


def test_active_execution_is_fenced():
    *_, row, event, service, adapter, execution = setup_execution()
    execution.claim(event)
    with pytest.raises(TransientEventError):
        execution.claim(event)
    assert service.get(row.id).attempt_count == 1 and adapter.preparations == 0


def test_different_event_id_cannot_override_scheduled_intent():
    *_, event, _, adapter, execution = setup_execution()
    changed = event.model_copy(update={"event_id": uuid4()})
    with pytest.raises(PermanentEventError):
        asyncio.run(execution.execute(changed))
    assert adapter.preparations == 0


def test_manual_retry_identity_is_distinct_and_safe():
    _, _, _, _, actor, row, event, service, adapter, execution = setup_execution()
    adapter.failure = SocialAdapterError("safe", classification=SocialErrorClass.TRANSIENT)
    asyncio.run(execution.execute(event))
    result = service.retry(row.id, actor, idempotency_key="manual")
    replay = service.retry(row.id, actor, idempotency_key="manual")
    assert as_utc(replay.next_retry_at) == as_utc(result.next_retry_at)
    adapter.failure = None
    asyncio.run(execution.retry_due())
    assert service.get(row.id).status == PublicationStatus.PUBLISHED
    assert (
        service.retry(row.id, actor, idempotency_key="manual").status == PublicationStatus.PUBLISHED
    )
    from news_ai_publishing import PublicationError

    with pytest.raises(PublicationError):
        service.retry(row.id, actor, idempotency_key="new-key")


def test_response_database_failure_keeps_intent_and_recovery_does_not_publish_twice(monkeypatch):
    factory, clock, _, _, _, row, event, service, adapter, execution = setup_execution()
    original = execution._locked

    def fail_response(session, claim):
        if adapter.publishes:
            raise RuntimeError("database unavailable")
        return original(session, claim)

    monkeypatch.setattr(execution, "_locked", fail_response)
    with pytest.raises(RuntimeError):
        asyncio.run(execution.execute(event))
    with factory() as session:
        assert session.scalar(select(PublicationAttempt)).phase == Phase.PUBLISH_INTENT_RECORDED
        assert session.get(Publication, row.id).external_post_id is None
    monkeypatch.setattr(execution, "_locked", original)
    clock.now += timedelta(seconds=execution.config.lease_seconds + 1)
    asyncio.run(execution.execute(event))
    assert adapter.publishes == 1 and service.get(row.id).status == PublicationStatus.BLOCKED


def test_verified_id_is_persisted_before_verification():
    factory, _, _, _, _, row, event, _, adapter, execution = setup_execution()
    original = adapter.verify_publication

    async def verify(external_id):
        with factory() as session:
            assert session.get(Publication, row.id).external_post_id == external_id
            assert session.scalar(select(PublicationAttempt)).phase == Phase.VERIFYING
        return await original(external_id)

    adapter.verify_publication = verify
    asyncio.run(execution.execute(event))
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PublicationAttempt)) == 1


def test_changed_artifact_during_preparation_blocks_before_publish():
    factory, _, variant, account, _, row, event, service, adapter, execution = setup_execution()
    original = adapter.prepare_publication

    async def prepare(request):
        result = await original(request)
        mutate(factory, variant, account, "wording")
        return result

    adapter.prepare_publication = prepare
    asyncio.run(execution.execute(event))
    assert adapter.publishes == 0
    assert service.get(row.id).status == PublicationStatus.BLOCKED


def test_outbox_failure_rolls_back_success_and_known_id_recovery_verifies_only(monkeypatch):
    factory, clock, _, _, _, row, event, service, adapter, execution = setup_execution()
    original = execution._event

    def fail(*args, **kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(execution, "_event", fail)
    with pytest.raises(RuntimeError):
        asyncio.run(execution.execute(event))
    assert service.get(row.id).status == PublicationStatus.PUBLISHING
    assert service.get(row.id).external_post_id
    with factory() as session:
        assert session.scalar(select(PublicationAttempt)).phase == Phase.VERIFYING
    monkeypatch.setattr(execution, "_event", original)
    clock.now += timedelta(seconds=execution.config.lease_seconds + 1)
    asyncio.run(execution.execute(event))
    assert adapter.publishes == 1 and service.get(row.id).status == PublicationStatus.PUBLISHED


def test_executed_v1_is_unchanged_v2_nullable_url_is_explicit():
    from news_ai_events.payloads import PublicationExecutedV1, PublicationExecutedV2
    from pydantic import ValidationError

    payload = dict(
        publication_id=str(uuid4()),
        attempt_id=str(uuid4()),
        platform="INSTAGRAM",
        external_post_id="301",
        external_url=None,
        published_at=Clock().now.isoformat(),
    )
    with pytest.raises(ValidationError):
        PublicationExecutedV1.model_validate(payload)
    assert PublicationExecutedV2.model_validate(payload).external_url is None
    with pytest.raises(ValidationError):
        PublicationExecutedV2.model_validate(
            {key: value for key, value in payload.items() if key != "external_url"}
        )
    with pytest.raises(ValidationError):
        PublicationExecutedV2.model_validate({**payload, "external_url": ""})


@pytest.mark.parametrize(
    "change",
    [
        {"consumer_group": "wrong"},
        {"max_attempts": 3},
        {"retry_delays_seconds": (0, 1, 2, 3, 4)},
        {"unexpected": True},
        {"jitter_ratio": 2},
    ],
)
def test_publisher_policy_is_closed_and_validated(change):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PublisherConfig.model_validate(change)
