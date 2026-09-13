"""Real PostgreSQL locking and exact media authorization regressions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import UUID

import pytest
from integration.test_human_review import postgres_factory as _postgres_factory
from media_fixtures import persist_caller_assets
from news_ai_content import ContentMediaAttachmentService, MediaAttachmentConflict
from news_ai_database import ContentVariant, MediaAsset, Publication, ReviewDecisionRecord
from news_ai_domain import PublicationStatus
from news_ai_publishing import (
    CreatePublicationRequest,
    PublicationScheduler,
    PublicationService,
    SchedulerConfig,
)
from news_ai_review import ApprovalEligibilityService, ReviewCapability, ReviewService
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from unit.content.test_media_attachment import (
    setup_attachment,
)
from unit.content.test_media_attachment import (
    test_media_changed_during_ai_is_stale_without_quality_persistence as check_inflight,
)
from unit.content.test_media_attachment import (
    test_media_tamper_after_quality_cannot_authorize_review as check_tamper,
)
from unit.quality.test_quality_gate import QualityAI, _run, _service
from unit.review.test_review_service import _policy, _principal, decide


@pytest.fixture
def postgres_factory():
    import os

    if not os.getenv("NEWS_AI_DATABASE_URL"):
        pytest.skip("PostgreSQL dependency is not configured")
    yield from _postgres_factory.__wrapped__()


def test_concurrent_distinct_attachments_commit_one_complete_ordered_list(postgres_factory):
    factory, event, first = setup_attachment(postgres_factory)
    with factory() as session, session.begin():
        other = persist_caller_assets(session)
    second = first.model_copy(update={"ordered_media_asset_ids": other})
    barrier = Barrier(2)

    def attach(request):
        barrier.wait(timeout=10)
        try:
            return ContentMediaAttachmentService(factory).attach_media(request)
        except MediaAttachmentConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        tasks = [executor.submit(attach, request) for request in (first, second)]
        results = [task.result(timeout=15) for task in tasks]
    assert results.count("conflict") == 1
    winning = next(result for result in results if result != "conflict")
    with factory() as session:
        variant = session.get(ContentVariant, first.content_variant_id)
        assert variant.media_asset_ids == [str(item) for item in winning]
        assert variant.version == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "id",
        "order",
        "file_hash",
        "public_url",
        "mime_type",
        "source_metadata",
        "asset_type",
        "visual_check_status",
    ],
)
def test_postgres_media_tamper_after_quality_blocks_review(postgres_factory, mutation):
    check_tamper(mutation, factory=postgres_factory)


def test_postgres_media_mutation_during_ai_does_not_promote_variant(postgres_factory):
    check_inflight(factory=postgres_factory)


@pytest.mark.parametrize("mutation", ["file_hash", "public_url", "order", "id"])
def test_postgres_media_tamper_after_approval_blocks_due_scheduler(postgres_factory, mutation):
    from news_ai_database import SocialAccount, SocialAccountStatus

    factory, event, request = setup_attachment(postgres_factory)
    ContentMediaAttachmentService(factory).attach_media(request)
    _run(factory, _service(QualityAI()), event)
    principal = _principal().model_copy(
        update={"capabilities": frozenset({ReviewCapability.PUBLISH})}
    )
    decide(ReviewService(factory, _policy()), request.content_variant_id, principal)
    with factory() as session, session.begin():
        account = SocialAccount(
            platform="INSTAGRAM",
            account_name="synthetic",
            account_identifier="synthetic",
            status=SocialAccountStatus.ACTIVE,
            capabilities={"image": True, "carousel": True},
        )
        session.add(account)
        session.flush()
        account_id = account.id
    now = datetime.now(UTC)

    def clock():
        return now

    eligibility = ApprovalEligibilityService(factory, _policy())
    service = PublicationService(factory, eligibility, clock=clock)
    row = service.create(
        CreatePublicationRequest(
            content_variant_id=request.content_variant_id,
            social_account_id=account_id,
            scheduled_at=now + timedelta(seconds=1),
            idempotency_key="media-tamper",
        ),
        principal,
    )
    with factory() as session, session.begin():
        variant = session.get(ContentVariant, request.content_variant_id)
        if mutation == "order":
            variant.media_asset_ids = list(reversed(variant.media_asset_ids))
        elif mutation == "id":
            ids = persist_caller_assets(session)
            variant.media_asset_ids = [str(item) for item in ids]
        else:
            asset = session.get(MediaAsset, UUID(variant.media_asset_ids[0]))
            setattr(
                asset,
                mutation,
                "e" * 64 if mutation == "file_hash" else "https://media.example.org/changed.jpg",
            )
    now += timedelta(seconds=2)
    scheduler = PublicationScheduler(
        service, SchedulerConfig(poll_interval_seconds=30, batch_size=10, publishing_paused=False)
    )
    assert scheduler.scan() == 0
    assert service.get(row.id).status is PublicationStatus.BLOCKED
    assert not eligibility.is_exact_version_approved(request.content_variant_id)
    # Historical idempotent action replay is not fresh authorization.
    decide(ReviewService(factory, _policy()), request.content_variant_id, principal)
    assert not eligibility.is_exact_version_approved(request.content_variant_id)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Publication)) == 1
        assert session.scalar(select(func.count()).select_from(ReviewDecisionRecord)) == 1


def test_review_locks_media_through_quality_hash_check_and_snapshot(postgres_factory, monkeypatch):
    factory, event, request = setup_attachment(postgres_factory)
    ContentMediaAttachmentService(factory).attach_media(request)
    _run(factory, _service(QualityAI()), event)
    entered, release, mutation_started = Event(), Event(), Event()
    original = ReviewService._artifact_snapshot

    def paused_snapshot(self, graph, **kwargs):
        if kwargs.get("lock_media"):
            entered.set()
            assert release.wait(10)
        return original(self, graph, **kwargs)

    monkeypatch.setattr(ReviewService, "_artifact_snapshot", paused_snapshot)

    def mutate():
        with pytest.raises(OperationalError) as error, factory() as session, session.begin():
            session.execute(text("SET LOCAL lock_timeout = '50ms'"))
            mutation_started.set()
            session.get(MediaAsset, request.ordered_media_asset_ids[0]).file_hash = "f" * 64
            session.flush()
        assert error.value.orig.sqlstate == "55P03"
        return "locked"

    with ThreadPoolExecutor(max_workers=2) as executor:
        review = executor.submit(
            decide, ReviewService(factory, _policy()), request.content_variant_id, _principal()
        )
        assert entered.wait(10)
        change = executor.submit(mutate)
        assert mutation_started.wait(10)
        assert change.result(timeout=5) == "locked"
        release.set()
        review.result(timeout=15)
    with factory() as session, session.begin():
        session.get(MediaAsset, request.ordered_media_asset_ids[0]).file_hash = "f" * 64
    assert not ApprovalEligibilityService(factory, _policy()).is_exact_version_approved(
        request.content_variant_id
    )
    with factory() as session:
        decision = session.scalar(select(ReviewDecisionRecord))
        assert decision.artifact_snapshot["media_provenance"][0]["file_hash"] != "f" * 64
