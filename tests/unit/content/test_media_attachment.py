import asyncio
from uuid import UUID, uuid4

import pytest
from media_fixtures import persist_caller_assets
from news_ai_content import (
    ContentMediaAttachmentService,
    MediaAttachmentConflict,
    MediaAttachmentRequest,
)
from news_ai_content.integrity import content_artifact_hash
from news_ai_content.media import MediaValidationError, load_media_provenance
from news_ai_database import ContentQualityCheck, ContentVariant, MediaAsset, ReviewDecisionRecord
from news_ai_domain import ReviewState
from news_ai_events import PermanentEventError, StaleWorkError
from news_ai_events.reliability import DeferredWorkError
from news_ai_review import ReviewPreconditionError, ReviewService
from pydantic import ValidationError
from sqlalchemy import func, select
from unit.quality.test_quality_gate import QualityAI, _factory, _run, _seed, _service
from unit.review.test_review_service import _policy, _principal, decide


def setup_attachment(factory=None):
    factory = factory or _factory()
    event, draft_id, variant_id = _seed(factory, media=False)
    with factory() as session, session.begin():
        ids = persist_caller_assets(session)
    request = MediaAttachmentRequest(
        content_variant_id=variant_id,
        expected_content_variant_version=1,
        ordered_media_asset_ids=ids,
    )
    return factory, event, request


def test_first_attachment_is_ordered_idempotent_and_conflicting_replacement_rejected():
    factory, event, request = setup_attachment()
    attachment = ContentMediaAttachmentService(factory)
    assert attachment.attach_media(request) == request.ordered_media_asset_ids
    assert attachment.attach_media(request) == request.ordered_media_asset_ids
    with pytest.raises(MediaAttachmentConflict):
        attachment.attach_media(
            request.model_copy(
                update={"ordered_media_asset_ids": tuple(reversed(request.ordered_media_asset_ids))}
            )
        )
    with factory() as session:
        variant = session.get(ContentVariant, request.content_variant_id)
        assert variant.version == 1
        snapshot = load_media_provenance(session, variant)
        assert [item["id"] for item in snapshot] == [
            str(item) for item in request.ordered_media_asset_ids
        ]
        assert "storage_key" not in repr(snapshot)
        assert session.scalar(select(func.count()).select_from(ContentQualityCheck)) == 0
    ai = QualityAI()
    service = _service(ai)
    context, result = _run(factory, service, event)
    assert result.passed
    assert "media_provenance" not in ai.requests[0].input["content_artifact"]
    with factory() as session:
        assert context.variants[0].artifact_hash == content_artifact_hash(
            ReviewService._quality_artifact(session.get(ContentVariant, request.content_variant_id))
        )
    with pytest.raises(MediaAttachmentConflict):
        attachment.attach_media(request)


@pytest.mark.parametrize("ids", [[], ["malformed"], [str(uuid4())] * 2])
def test_attachment_contract_rejects_missing_malformed_duplicate_ids(ids):
    with pytest.raises(ValidationError):
        MediaAttachmentRequest(
            content_variant_id=uuid4(),
            expected_content_variant_version=1,
            ordered_media_asset_ids=ids,
        )


@pytest.mark.parametrize("count", [1, 3])
def test_wrong_carousel_count_rejected_without_mutation(count):
    factory, event, request = setup_attachment()
    with factory() as session, session.begin():
        ids = persist_caller_assets(session, count)
    with pytest.raises(MediaValidationError):
        ContentMediaAttachmentService(factory).attach_media(
            request.model_copy(update={"ordered_media_asset_ids": ids})
        )
    with factory() as session:
        assert session.get(ContentVariant, request.content_variant_id).media_asset_ids == []


BAD_MEDIA = [
    ("asset_type", "VIDEO"),
    ("mime_type", "image/png"),
    ("source_metadata", {"media_format": "MPO"}),
    ("visual_check_status", "GENERATED"),
    ("file_hash", "z" * 64),
    ("public_url", "http://media.example.org/image.jpg"),
    ("public_url", "https://127.0.0.1/image.jpg"),
    ("public_url", "https://10.0.0.1/image.jpg"),
    ("public_url", "https://localhost/image.jpg"),
    ("public_url", "https://user:SUPER_SECRET@example.org/image.jpg"),
    ("public_url", "https://media.example.org/image.jpg?access_token=SUPER_SECRET"),
]


@pytest.mark.parametrize(("field", "value"), BAD_MEDIA)
def test_invalid_caller_media_rejected_at_attachment_and_quality(field, value, caplog):
    factory, event, request = setup_attachment()
    with factory() as session, session.begin():
        setattr(session.get(MediaAsset, request.ordered_media_asset_ids[0]), field, value)
    with pytest.raises(MediaValidationError):
        ContentMediaAttachmentService(factory).attach_media(request)
    # Tampered durable rows must independently fail quality before AI.
    with factory() as session, session.begin():
        session.get(ContentVariant, request.content_variant_id).media_asset_ids = [
            str(item) for item in request.ordered_media_asset_ids
        ]
    ai = QualityAI()
    with factory() as session, pytest.raises(PermanentEventError):
        _service(ai).load_context(session, event)
    assert ai.calls == 0 and "SUPER_SECRET" not in caplog.text


def test_missing_assets_and_stale_version_are_rejected():
    factory, event, request = setup_attachment()
    with pytest.raises(MediaValidationError):
        ContentMediaAttachmentService(factory).attach_media(
            request.model_copy(update={"ordered_media_asset_ids": (uuid4(), uuid4())})
        )
    with pytest.raises(MediaAttachmentConflict):
        ContentMediaAttachmentService(factory).attach_media(
            request.model_copy(update={"expected_content_variant_version": 2})
        )


def test_missing_media_is_deferred_without_quality_or_ai():
    factory, event, request = setup_attachment()
    ai = QualityAI()
    with factory() as session, pytest.raises(DeferredWorkError):
        _service(ai).load_context(session, event)
    assert ai.calls == 0
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContentQualityCheck)) == 0
        assert (
            session.get(ContentVariant, request.content_variant_id).review_state
            is ReviewState.NOT_READY
        )


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
def test_media_tamper_after_quality_cannot_authorize_review(mutation, factory=None):
    factory, event, request = setup_attachment(factory)
    ContentMediaAttachmentService(factory).attach_media(request)
    _, result = _run(factory, _service(QualityAI()), event)
    with factory() as session, session.begin():
        variant = session.get(ContentVariant, request.content_variant_id)
        if mutation == "order":
            variant.media_asset_ids = list(reversed(variant.media_asset_ids))
        elif mutation == "id":
            variant.media_asset_ids = [str(uuid4()), variant.media_asset_ids[1]]
        else:
            values = {
                "file_hash": "c" * 64,
                "public_url": "https://media.example.org/changed.jpg",
                "mime_type": "image/png",
                "source_metadata": {"media_format": "WEBP"},
                "asset_type": "VIDEO",
                "visual_check_status": "GENERATED",
            }
            setattr(
                session.get(MediaAsset, UUID(variant.media_asset_ids[0])),
                mutation,
                values[mutation],
            )
    with pytest.raises(ReviewPreconditionError):
        decide(ReviewService(factory, _policy()), request.content_variant_id, _principal())
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ReviewDecisionRecord)) == 0


def test_media_changed_during_ai_is_stale_without_quality_persistence(factory=None):
    factory, event, request = setup_attachment(factory)
    ContentMediaAttachmentService(factory).attach_media(request)
    service = _service(QualityAI())
    with factory() as session:
        context = service.load_context(session, event)
    executions = asyncio.run(service.assess(context, event))
    with factory() as session, session.begin():
        session.get(MediaAsset, request.ordered_media_asset_ids[0]).file_hash = "d" * 64
    with factory() as session, session.begin(), pytest.raises(StaleWorkError):
        service.persist(session, context=context, event=event, executions=executions)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContentQualityCheck)) == 0
        assert (
            session.get(ContentVariant, request.content_variant_id).review_state
            is ReviewState.NOT_READY
        )


@pytest.mark.parametrize("change", ["order", "file_hash", "public_url"])
def test_quality_identity_binds_material_media_not_just_ids(change):
    factory, event, request = setup_attachment()
    ContentMediaAttachmentService(factory).attach_media(request)
    service = _service(QualityAI())
    with factory() as session:
        original = service.load_context(session, event)
        replay = service.load_context(session, event)
        assert original.event_semantic_key == replay.event_semantic_key
    with factory() as session, session.begin():
        variant = session.get(ContentVariant, request.content_variant_id)
        if change == "order":
            variant.media_asset_ids = list(reversed(variant.media_asset_ids))
        else:
            asset = session.get(MediaAsset, request.ordered_media_asset_ids[0])
            setattr(
                asset,
                change,
                "b" * 64
                if change == "file_hash"
                else "https://media.example.org/material-change.jpg",
            )
    with factory() as session:
        changed = service.load_context(session, event)
        assert changed.event_semantic_key != original.event_semantic_key
        assert changed.variants[0].artifact_hash != original.variants[0].artifact_hash


def test_storage_secrets_and_unused_metadata_never_enter_quality_ai():
    factory, event, request = setup_attachment()
    with factory() as session, session.begin():
        asset = session.get(MediaAsset, request.ordered_media_asset_ids[0])
        asset.storage_key = "/internal/SUPER_SECRET/image.jpg"
        asset.source_metadata = {"media_format": "JPEG", "api_key": "SUPER_SECRET"}
    ContentMediaAttachmentService(factory).attach_media(request)
    ai = QualityAI()
    _run(factory, _service(ai), event)
    assert "SUPER_SECRET" not in repr(ai.requests[0].input)


@pytest.mark.parametrize(("field", "value"), [("platform", "X"), ("format", "POST")])
def test_unsupported_media_attachment_target_rejected(field, value):
    factory, event, request = setup_attachment()
    with factory() as session, session.begin():
        setattr(session.get(ContentVariant, request.content_variant_id), field, value)
    with pytest.raises(MediaValidationError):
        ContentMediaAttachmentService(factory).attach_media(request)


def test_earlier_methodology_cannot_authorize_current_media_review():
    factory, event, request = setup_attachment()
    ContentMediaAttachmentService(factory).attach_media(request)
    _run(factory, _service(QualityAI()), event)
    with factory() as session, session.begin():
        check = session.scalar(select(ContentQualityCheck))
        # Even a coincidentally matching hash from an earlier methodology is insufficient.
        check.methodology_version = "quality-gate-methodology-v2"
        old_hash = check.content_artifact_hash
    with pytest.raises(ReviewPreconditionError):
        decide(ReviewService(factory, _policy()), request.content_variant_id, _principal())
    with factory() as session:
        assert session.scalar(select(ContentQualityCheck)).content_artifact_hash == old_hash
