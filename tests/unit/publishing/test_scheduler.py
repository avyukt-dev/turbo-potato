from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from news_ai_database import (
    AuditLog,
    ContentDraft,
    ContentQualityCheck,
    ContentVariant,
    EventOutbox,
    FactSheet,
    MediaAsset,
    SocialAccount,
    SocialAccountStatus,
)
from news_ai_domain import PublicationStatus, ReviewState
from news_ai_publishing import (
    CreatePublicationRequest,
    PublicationError,
    PublicationScheduler,
    PublicationService,
    SchedulerConfig,
)
from news_ai_review import ApprovalEligibilityService, ReviewCapability, ReviewService
from pydantic import ValidationError
from sqlalchemy import func, select
from unit.review.test_review_service import _factory, _policy, _principal, decide, seed_reviewable


class Clock:
    def __init__(self):
        self.now = datetime(2030, 1, 1, tzinfo=UTC)

    def __call__(self):
        return self.now


def seed_candidate(factory, *, media=True):
    from news_ai_database import AIModel

    with factory() as session, session.begin():
        previous = session.scalar(select(AIModel).where(AIModel.model_name == "review-fixture"))
        if previous is not None:
            previous.model_name = str(uuid4())
    variant_id = seed_reviewable(factory)[0]
    with factory() as session, session.begin():
        variant = session.get(ContentVariant, variant_id)
        if media:
            asset = MediaAsset(
                asset_type="IMAGE",
                storage_provider="test-artifact",
                storage_key="gauge.jpg",
                public_url="https://media.example.org/gauge.jpg",
                mime_type="image/jpeg",
                file_hash="a" * 64,
                visual_check_status="VALIDATED",
                source_metadata={"media_format": "JPEG"},
            )
            session.add(asset)
            session.flush()
            variant.media_asset_ids = [str(asset.id)]
            second = MediaAsset(
                asset_type="IMAGE",
                storage_provider="test-artifact",
                storage_key="gauge-2.jpg",
                public_url="https://media.example.org/gauge-2.jpg",
                mime_type="image/jpeg",
                file_hash="b" * 64,
                visual_check_status="VALIDATED",
                source_metadata={"media_format": "JPEG"},
            )
            session.add(second)
            session.flush()
            variant.media_asset_ids = [str(asset.id), str(second.id)]
            slide = variant.structured_payload["slides"][0]
            variant.structured_payload = {
                **variant.structured_payload,
                "slides": [slide, {**slide, "position": 2}],
            }
            check = session.scalar(
                select(ContentQualityCheck).where(
                    ContentQualityCheck.content_variant_id == variant_id
                )
            )
            from news_ai_content import content_artifact_hash

            check.content_artifact_hash = content_artifact_hash(
                ReviewService._quality_artifact(variant)
            )
        account = SocialAccount(
            platform="INSTAGRAM",
            account_name="synthetic-destination",
            account_identifier=str(uuid4()),
            status=SocialAccountStatus.ACTIVE,
            capabilities={"image": True, "carousel": True},
        )
        session.add(account)
        session.flush()
        account_id = account.id
    actor = _principal().model_copy(
        update={
            "capabilities": frozenset(
                {ReviewCapability.VIEW, ReviewCapability.APPROVE, ReviewCapability.PUBLISH}
            )
        }
    )
    decide(ReviewService(factory, _policy()), variant_id, actor, key=str(uuid4()))
    return variant_id, account_id, actor


def stack(factory, clock, *, batch=50, paused=False):
    service = PublicationService(
        factory, ApprovalEligibilityService(factory, _policy()), clock=clock
    )
    scheduler = PublicationScheduler(
        service,
        SchedulerConfig(poll_interval_seconds=30, batch_size=batch, publishing_paused=paused),
    )
    return service, scheduler


def request(variant, account, clock, *, key="schedule", seconds=60):
    return CreatePublicationRequest(
        content_variant_id=variant,
        social_account_id=account,
        scheduled_at=clock.now + timedelta(seconds=seconds),
        idempotency_key=key,
    )


def test_future_due_overdue_replay_and_cancelled_event_history():
    factory, clock = _factory(), Clock()
    variant, account, actor = seed_candidate(factory)
    service, scheduler = stack(factory, clock)
    body = request(variant, account, clock)
    row = service.create(body, actor, correlation_id=uuid4())
    assert row.status == PublicationStatus.SCHEDULED
    assert service.create(body, actor).id == row.id
    assert scheduler.scan() == 0
    clock.now += timedelta(hours=1)
    assert scheduler.scan() == 1

    assert scheduler.scan() == 0
    with factory() as session:
        events = tuple(session.scalars(select(EventOutbox)))
        assert len(events) == 1
        event = events[0]
        assert event.event_type == "publication.scheduled"
        assert event.aggregate_type == "publication" and event.aggregate_id == row.id
        assert event.correlation_id is not None and event.causation_id is None
        assert set(event.payload) == {
            "publication_id",
            "content_variant_id",
            "social_account_id",
            "scheduled_at",
        }
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "PUBLICATION_CREATED")
            )
            == 1
        )
    assert service.cancel(row.id, actor).status == PublicationStatus.CANCELLED
    assert service.get(row.id).scheduled_event_id == event.event_id
    assert scheduler.scan() == 0


@pytest.mark.parametrize("change", ["account", "time", "variant"])
def test_idempotency_material_conflict(change):
    factory, clock = _factory(), Clock()
    variant, account, actor = seed_candidate(factory)
    service, _ = stack(factory, clock)
    body = request(variant, account, clock)
    service.create(body, actor)
    changed = body.model_copy(
        update={"social_account_id": uuid4()}
        if change == "account"
        else {"content_variant_id": uuid4()}
        if change == "variant"
        else {"scheduled_at": clock.now + timedelta(hours=2)}
    )
    with pytest.raises(PublicationError, match="Publication") as exc:
        service.create(changed, actor)
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"


def test_duplicate_active_candidate_new_key_conflicts_but_cancel_permits_new_request():
    factory, clock = _factory(), Clock()
    variant, account, actor = seed_candidate(factory)
    service, _ = stack(factory, clock)
    row = service.create(request(variant, account, clock), actor)
    with pytest.raises(PublicationError) as exc:
        service.create(request(variant, account, clock, key="other"), actor)
    assert exc.value.code == "PUBLICATION_CONFLICT"
    service.cancel(row.id, actor)
    assert service.create(request(variant, account, clock, key="other"), actor).id != row.id


@pytest.mark.parametrize(
    "mutation", ["facts", "wording", "version", "quality", "review", "account"]
)
def test_invalidation_before_due_blocks_without_event(mutation):
    factory, clock = _factory(), Clock()
    variant, account, actor = seed_candidate(factory)
    service, scheduler = stack(factory, clock)
    row = service.create(request(variant, account, clock), actor)
    mutate(factory, variant, account, mutation)
    clock.now += timedelta(hours=1)
    assert scheduler.scan() == 0
    assert service.get(row.id).status == PublicationStatus.BLOCKED
    with factory() as session:
        assert session.scalar(select(EventOutbox)) is None
    assert scheduler.scan() == 0


def mutate(factory, variant, account, mutation):
    with factory() as session, session.begin():
        item = session.get(ContentVariant, variant)
        if mutation == "facts":
            draft = session.get(ContentDraft, item.content_draft_id)
            sheet = session.get(FactSheet, draft.fact_sheet_id)
            session.add(
                FactSheet(
                    story_id=sheet.story_id,
                    version=2,
                    headline="Correction",
                    summary="Changed facts",
                    risk_level=sheet.risk_level,
                    sensitive_topics=[],
                    semantic_key=str(uuid4()),
                )
            )
        elif mutation == "wording":
            item.caption = "Changed after approval"
        elif mutation == "version":
            item.version += 1
        elif mutation == "quality":
            session.scalar(
                select(ContentQualityCheck).where(ContentQualityCheck.content_variant_id == variant)
            ).passed = False
        elif mutation == "review":
            item.review_state = ReviewState.REJECTED
        else:
            session.get(SocialAccount, account).status = SocialAccountStatus.PAUSED


def test_pause_unpause_cancel_before_due_and_publish_now():
    factory, clock = _factory(), Clock()
    variant, account, actor = seed_candidate(factory)
    service, scheduler = stack(factory, clock, paused=True)
    row = service.create(request(variant, account, clock), actor)
    service.publish_now(row.id, actor)
    assert scheduler.scan() == 0
    scheduler.config = scheduler.config.model_copy(update={"publishing_paused": False})
    assert scheduler.scan() == 1
    with pytest.raises(PublicationError):
        service.publish_now(row.id, actor)
    service.cancel(row.id, actor)
    second = service.create(request(variant, account, clock, key="new"), actor)
    service.cancel(second.id, actor)
    clock.now += timedelta(days=1)
    assert scheduler.scan() == 0


def test_missing_media_is_honest_blocked_not_execution_ready():
    factory, clock = _factory(), Clock()
    variant, account, actor = seed_candidate(factory, media=False)
    service, scheduler = stack(factory, clock)
    row = service.create(request(variant, account, clock), actor)
    assert row.status == PublicationStatus.BLOCKED and row.blocking_reason == "MEDIA_UNAVAILABLE"
    clock.now += timedelta(days=1)
    assert scheduler.scan() == 0


@pytest.mark.parametrize(
    "field", ["actor_id", "status", "access_token", "caption", "external_post_id"]
)
def test_request_rejects_client_controlled_fields(field):
    with pytest.raises(ValidationError):
        CreatePublicationRequest.model_validate(
            {
                "content_variant_id": uuid4(),
                "social_account_id": uuid4(),
                "idempotency_key": "key",
                field: "untrusted",
            }
        )


def test_timezone_past_and_authorization_fail_closed():
    with pytest.raises(ValidationError):
        CreatePublicationRequest(
            content_variant_id=uuid4(),
            social_account_id=uuid4(),
            scheduled_at=datetime(2030, 1, 1),
            idempotency_key="x",
        )
    factory, clock = _factory(), Clock()
    variant, account, actor = seed_candidate(factory)
    service, _ = stack(factory, clock)
    with pytest.raises(PublicationError) as exc:
        service.create(request(variant, account, clock, seconds=-1), actor)
    assert exc.value.code == "INVALID_SCHEDULE"
    with pytest.raises(PublicationError) as exc:
        service.create(request(variant, account, clock), _principal())
    assert exc.value.code == "FORBIDDEN"


def test_batch_is_bounded_and_due_order_is_deterministic():
    factory, clock = _factory(), Clock()
    service, scheduler = stack(factory, clock, batch=1)
    rows = []
    for seconds in [120, 60]:
        variant, account, actor = seed_candidate(factory)
        rows.append(
            service.create(
                request(variant, account, clock, key=str(seconds), seconds=seconds), actor
            )
        )
    clock.now += timedelta(hours=1)
    assert scheduler.scan() == 1
    assert service.get(rows[1].id).scheduled_event_id is not None
    assert service.get(rows[0].id).scheduled_event_id is None
    assert scheduler.scan() == 1


def test_same_idempotency_key_cannot_silently_schedule_a_new_variant_version():
    factory, clock = _factory(), Clock()
    variant, account, actor = seed_candidate(factory)
    service, _ = stack(factory, clock)
    body = request(variant, account, clock)
    service.create(body, actor)
    mutate(factory, variant, account, "version")
    with pytest.raises(PublicationError) as exc:
        service.create(body, actor)
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("format", ["PNG", "WEBP", "MPO", "JPS", None])
def test_non_jpeg_media_cannot_be_treated_as_ready_even_with_jpeg_mime(format):
    factory, clock = _factory(), Clock()
    variant, account, actor = seed_candidate(factory)
    with factory() as session, session.begin():
        asset_id = session.get(ContentVariant, variant).media_asset_ids[0]
        from uuid import UUID

        session.get(MediaAsset, UUID(asset_id)).source_metadata = {"media_format": format}
    service, _ = stack(factory, clock)
    # Format mutation also invalidates the exact human approval binding.
    with pytest.raises(PublicationError) as exc:
        service.create(request(variant, account, clock), actor)
    assert exc.value.code == "CONTENT_NOT_APPROVED"


def test_replacing_reviewed_media_bytes_cannot_retain_approval():
    factory, clock = _factory(), Clock()
    variant, account, actor = seed_candidate(factory)
    service, scheduler = stack(factory, clock)
    row = service.create(request(variant, account, clock), actor)
    from uuid import UUID

    with factory() as session, session.begin():
        identifier = session.get(ContentVariant, variant).media_asset_ids[0]
        session.get(MediaAsset, UUID(identifier)).file_hash = "c" * 64
    clock.now += timedelta(hours=1)
    assert scheduler.scan() == 0
    assert service.get(row.id).blocking_reason == "APPROVAL_STALE"
