from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from media_fixtures import persist_caller_assets
from news_ai_common.config import ConfigLoader
from news_ai_content import content_artifact_hash
from news_ai_content.integrity import quality_artifact
from news_ai_database import (
    AIModel,
    AIRun,
    AuditLog,
    Base,
    ContentDraft,
    ContentQualityCheck,
    ContentVariant,
    EventOutbox,
    FactSheet,
    ReviewDecisionRecord,
    Story,
)
from news_ai_domain import ReviewState, RiskLevel
from news_ai_editorial import EditorialConfigLoader
from news_ai_quality import QUALITY_METHODOLOGY_VERSION
from news_ai_review import (
    ApprovalEligibilityService,
    ArtifactType,
    ReviewActionRequest,
    ReviewCapability,
    ReviewConflictError,
    ReviewerPrincipal,
    ReviewPreconditionError,
    ReviewService,
    ReviewValidationError,
)
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _policy():
    return EditorialConfigLoader(ConfigLoader("config")).load_publishing_policy()


def _principal() -> ReviewerPrincipal:
    return ReviewerPrincipal(
        reviewer_id=uuid4(),
        capabilities=frozenset(
            {ReviewCapability.VIEW, ReviewCapability.REVIEW, ReviewCapability.APPROVE}
        ),
    )


def _brief(story_id: UUID, sheet_id: UUID, claim_id: UUID, check_id: UUID) -> dict:
    return {
        "story_id": str(story_id),
        "fact_sheet_id": str(sheet_id),
        "fact_sheet_version": 1,
        "headline": "Verified gauge record",
        "summary": "The official record supports the measurement.",
        "editorial_angle": "Report only the documented measurement.",
        "key_points": ["The gauge measured two metres."],
        "exclusions": [],
        "tone": "measured",
        "audience_relevance": None,
        "claims": [
            {
                "claim_id": str(claim_id),
                "text": "The gauge measured two metres.",
                "status": "SUPPORTED",
                "fact_check_id": str(check_id),
                "label": "TRUE",
                "confidence_score": 0.9,
                "evidence_ids": [],
                "evidence_excerpts": [],
            }
        ],
        "risk_level": "LOW",
        "sensitive_topics": [],
        "unresolved_questions": [],
        "priority_topics": [],
        "style_rules": {
            "source_aware": True,
            "explicit_about_uncertainty": True,
            "avoid_unsupported_motive_attribution": True,
            "avoid_sensational_overstatement": True,
        },
        "target": {"platform": "INSTAGRAM", "format": "CAROUSEL"},
        "human_review_required": True,
    }


def seed_reviewable(factory: sessionmaker[Session], *, variants: int = 1) -> tuple[UUID, ...]:
    story_id, sheet_id, claim_id, fact_check_id = (uuid4() for _ in range(4))
    with factory() as session, session.begin():
        model = AIModel(
            provider="test", model_name="review-fixture", locality="LOCAL", capabilities={}
        )
        session.add(model)
        session.flush()
        generation = AIRun(
            ai_model_id=model.id,
            task_type="CONTENT_GENERATION",
            prompt_id="content-generation",
            prompt_version="v1",
            prompt_checksum="a" * 64,
            status="SUCCEEDED",
            validation_status="VALIDATED",
        )
        quality_run = AIRun(
            ai_model_id=model.id,
            task_type="QUALITY_CHECKING",
            prompt_id="content-quality",
            prompt_version="v1",
            prompt_checksum="b" * 64,
            status="SUCCEEDED",
            validation_status="VALIDATED",
        )
        session.add_all((generation, quality_run))
        session.add(
            Story(
                id=story_id,
                canonical_headline="Verified gauge record",
                status="VERIFIED",
                language="en",
                risk_level=RiskLevel.LOW,
            )
        )
        session.flush()
        session.add(
            FactSheet(
                id=sheet_id,
                story_id=story_id,
                version=1,
                headline="Verified gauge record",
                summary="The official record supports the measurement.",
                claims_snapshot=[],
                fact_checks_snapshot=[],
                evidence_snapshot=[],
                sources_snapshot=[],
                risk_level=RiskLevel.LOW,
                sensitive_topics=[],
                semantic_key=f"sheet:{sheet_id}",
            )
        )
        session.flush()
        draft = ContentDraft(
            story_id=story_id,
            fact_sheet_id=sheet_id,
            fact_sheet_version=1,
            version=1,
            methodology_version="content-generation-methodology-v1",
            editorial_brief_snapshot=_brief(story_id, sheet_id, claim_id, fact_check_id),
            risk_level=RiskLevel.LOW,
            sensitive_topics=[],
            review_required=True,
            review_state=ReviewState.READY_FOR_REVIEW,
            created_by_ai_run_id=generation.id,
            semantic_key=f"draft:{uuid4()}",
        )
        session.add(draft)
        session.flush()
        variant_ids = []
        for index in range(variants):
            variant = ContentVariant(
                content_draft_id=draft.id,
                platform="INSTAGRAM",
                format="CAROUSEL",
                language=("en" if index == 0 else "fr" if index == 1 else f"x-{index}"),
                title=f"Gauge record {index}",
                body="Measurement\nThe gauge measured two metres.",
                caption="Official record: two metres.",
                structured_payload={
                    "slides": [
                        {
                            "position": 1,
                            "heading": "Measurement",
                            "body": "The gauge measured two metres.",
                            "claim_ids": [str(claim_id)],
                        }
                    ],
                    "hashtags": ["#records"],
                    "claim_ids_used": [str(claim_id)],
                },
                claim_ids_used=[str(claim_id)],
                source_ids_used=[],
                media_asset_ids=[],
                review_state=ReviewState.READY_FOR_REVIEW,
                version=1,
            )
            session.add(variant)
            session.flush()
            slide = variant.structured_payload["slides"][0]
            variant.structured_payload = {
                **variant.structured_payload,
                "slides": [slide, {**slide, "position": 2}],
            }
            variant.media_asset_ids = [str(item) for item in persist_caller_assets(session)]
            session.add(
                ContentQualityCheck(
                    content_draft_id=draft.id,
                    content_variant_id=variant.id,
                    content_variant_version=1,
                    fact_sheet_id=sheet_id,
                    fact_sheet_version=1,
                    methodology_version=QUALITY_METHODOLOGY_VERSION,
                    content_artifact_hash=content_artifact_hash(quality_artifact(session, variant)),
                    factual_accuracy_passed=True,
                    source_alignment_passed=True,
                    citation_alignment_passed=True,
                    style_passed=True,
                    unsupported_claims=[],
                    fabricated_quotes=[],
                    incorrect_names=[],
                    incorrect_dates=[],
                    incorrect_numbers=[],
                    missing_context=[],
                    defamation_risk=False,
                    sensitive_topic_error=False,
                    passed=True,
                    review_required=True,
                    notes=[],
                    ai_run_id=quality_run.id,
                    semantic_key=f"quality:{variant.id}",
                )
            )
            variant_ids.append(variant.id)
    return tuple(variant_ids)


def decide(
    service: ReviewService,
    variant_id: UUID,
    principal: ReviewerPrincipal,
    decision: ReviewState = ReviewState.APPROVED,
    *,
    reason: str | None = None,
    key: str = "review-operation-1",
):
    return service.decide(
        artifact_type=ArtifactType.CONTENT_VARIANT.value,
        artifact_id=variant_id,
        request=ReviewActionRequest(artifact_version=1, reason=reason),
        decision=decision,
        principal=principal,
        idempotency_key=key,
        request_id=uuid4(),
        correlation_id=uuid4(),
    )


def test_exact_version_approval_persists_snapshot_audit_and_eligibility() -> None:
    factory = _factory()
    variant_id = seed_reviewable(factory)[0]
    principal = _principal()
    service = ReviewService(factory, _policy())

    result = decide(service, variant_id, principal)

    assert result.decision is ReviewState.APPROVED
    assert ApprovalEligibilityService(factory, _policy()).is_exact_version_approved(variant_id)
    with factory() as session:
        record = session.scalar(select(ReviewDecisionRecord))
        audit = session.scalar(select(AuditLog))
        assert record.artifact_hash and len(record.artifact_hash) == 64
        assert record.artifact_snapshot["content_variant_id"] == str(variant_id)
        assert record.quality_check_id is not None
        assert audit.actor_id == principal.reviewer_id
        assert audit.review_decision_id == record.id
        assert not list(session.scalars(select(EventOutbox)))


@pytest.mark.parametrize(
    ("decision", "reason"),
    [
        (ReviewState.REJECTED, "The framing is unsuitable."),
        (ReviewState.CHANGES_REQUESTED, "Clarify the measurement context."),
    ],
)
def test_nonapproval_terminal_decisions_are_durable_and_ineligible(
    decision: ReviewState, reason: str
) -> None:
    factory = _factory()
    variant_id = seed_reviewable(factory)[0]
    result = decide(
        ReviewService(factory, _policy()),
        variant_id,
        _principal(),
        decision,
        reason=reason,
    )
    assert result.current_review_state is decision
    assert not ApprovalEligibilityService(factory, _policy()).is_exact_version_approved(variant_id)


@pytest.mark.parametrize("decision", [ReviewState.REJECTED, ReviewState.CHANGES_REQUESTED])
def test_nonapproval_requires_reason(decision: ReviewState) -> None:
    factory = _factory()
    variant_id = seed_reviewable(factory)[0]
    with pytest.raises(ReviewValidationError):
        decide(ReviewService(factory, _policy()), variant_id, _principal(), decision)


def test_same_idempotent_operation_reuses_and_changed_operation_conflicts() -> None:
    factory = _factory()
    variant_id = seed_reviewable(factory)[0]
    principal = _principal()
    service = ReviewService(factory, _policy())
    first = decide(service, variant_id, principal)
    replay = decide(service, variant_id, principal)
    assert replay.review_decision_id == first.review_decision_id
    with pytest.raises(ReviewConflictError):
        decide(
            service,
            variant_id,
            principal,
            ReviewState.REJECTED,
            reason="Different operation",
        )


def test_second_terminal_decision_for_exact_version_conflicts() -> None:
    factory = _factory()
    variant_id = seed_reviewable(factory)[0]
    service = ReviewService(factory, _policy())
    decide(service, variant_id, _principal())
    with pytest.raises(ReviewConflictError):
        decide(
            service,
            variant_id,
            _principal(),
            ReviewState.REJECTED,
            reason="Conflicting reviewer",
            key="review-operation-2",
        )


def test_stale_fact_sheet_and_stale_ui_version_cannot_approve() -> None:
    factory = _factory()
    variant_id = seed_reviewable(factory)[0]
    with factory() as session, session.begin():
        variant = session.get(ContentVariant, variant_id)
        draft = session.get(ContentDraft, variant.content_draft_id)
        sheet = session.get(FactSheet, draft.fact_sheet_id)
        session.add(
            FactSheet(
                story_id=sheet.story_id,
                version=2,
                headline="Corrected",
                summary="Corrected factual basis",
                risk_level=sheet.risk_level,
                sensitive_topics=[],
                semantic_key=f"sheet:{uuid4()}",
            )
        )


@pytest.mark.parametrize(
    "corruption",
    [
        "not-ready",
        "quality-failed",
        "quality-missing",
        "risk-mismatch",
        "sensitivity-mismatch",
        "quality-sheet-mismatch",
        "brief-identity-mismatch",
        "brief-review-bypass",
        "content-after-quality",
    ],
)
def test_corrupt_or_unready_durable_graph_cannot_receive_human_decision(
    corruption: str,
) -> None:
    factory = _factory()
    variant_id = seed_reviewable(factory)[0]
    with factory() as session, session.begin():
        variant = session.get(ContentVariant, variant_id)
        draft = session.get(ContentDraft, variant.content_draft_id)
        quality = session.scalar(select(ContentQualityCheck))
        if corruption == "not-ready":
            variant.review_state = ReviewState.NOT_READY
        elif corruption == "quality-failed":
            quality.passed = False
        elif corruption == "quality-missing":
            session.delete(quality)
        elif corruption == "risk-mismatch":
            draft.risk_level = RiskLevel.HIGH
        elif corruption == "sensitivity-mismatch":
            draft.sensitive_topics = ["RELIGIOUS_VIOLENCE"]
        elif corruption == "quality-sheet-mismatch":
            quality.fact_sheet_version = 2
        elif corruption == "brief-identity-mismatch":
            brief = dict(draft.editorial_brief_snapshot)
            brief["fact_sheet_id"] = str(uuid4())
            draft.editorial_brief_snapshot = brief
        elif corruption == "brief-review-bypass":
            brief = dict(draft.editorial_brief_snapshot)
            brief["human_review_required"] = False
            draft.editorial_brief_snapshot = brief
        else:
            variant.caption = "Changed after quality without a version bump"
    with pytest.raises(ReviewPreconditionError):
        decide(ReviewService(factory, _policy()), variant_id, _principal())
    with factory() as session:
        assert session.scalar(select(ReviewDecisionRecord)) is None
        assert session.scalar(select(AuditLog)) is None
    service = ReviewService(factory, _policy())
    with pytest.raises(ReviewPreconditionError):
        decide(service, variant_id, _principal())
    with pytest.raises(ReviewConflictError):
        service.decide(
            artifact_type="content_variant",
            artifact_id=variant_id,
            request=ReviewActionRequest(artifact_version=2),
            decision=ReviewState.APPROVED,
            principal=_principal(),
            idempotency_key="stale-ui",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "wording",
        "structured",
        "claims",
        "sources",
        "media",
        "version",
        "fact_sheet",
        "quality",
    ],
)
def test_historical_approval_is_retained_but_current_eligibility_revalidates(
    mutation: str,
) -> None:
    factory = _factory()
    variant_id = seed_reviewable(factory)[0]
    service = ReviewService(factory, _policy())
    decide(service, variant_id, _principal())
    with factory() as session, session.begin():
        variant = session.get(ContentVariant, variant_id)
        draft = session.get(ContentDraft, variant.content_draft_id)
        if mutation == "wording":
            variant.caption = "Silently changed wording"
        elif mutation == "structured":
            variant.structured_payload = {**variant.structured_payload, "hashtags": ["#changed"]}
        elif mutation == "claims":
            variant.claim_ids_used = [str(uuid4())]
        elif mutation == "sources":
            variant.source_ids_used = [str(uuid4())]
        elif mutation == "media":
            variant.media_asset_ids = [str(uuid4())]
        elif mutation == "version":
            variant.version = 2
        elif mutation == "fact_sheet":
            sheet = session.get(FactSheet, draft.fact_sheet_id)
            session.add(
                FactSheet(
                    story_id=sheet.story_id,
                    version=2,
                    headline="Correction",
                    summary="New facts",
                    risk_level=sheet.risk_level,
                    sensitive_topics=[],
                    semantic_key=f"sheet:{uuid4()}",
                )
            )
        else:
            check = session.scalar(select(ContentQualityCheck))
            check.passed = False
    assert not ApprovalEligibilityService(factory, _policy()).is_exact_version_approved(variant_id)
    with factory() as session:
        assert session.scalar(select(ReviewDecisionRecord)) is not None


def test_multi_variant_draft_needs_every_variant_approved() -> None:
    factory = _factory()
    first, second = seed_reviewable(factory, variants=2)
    service = ReviewService(factory, _policy())
    decide(service, first, _principal(), key="first")
    with factory() as session:
        draft = session.get(ContentDraft, session.get(ContentVariant, first).content_draft_id)
        assert draft.review_state is ReviewState.IN_REVIEW
    decide(service, second, _principal(), key="second")
    with factory() as session:
        draft = session.get(ContentDraft, session.get(ContentVariant, first).content_draft_id)
        assert draft.review_state is ReviewState.APPROVED


def test_queue_and_detail_are_bounded_side_effect_free_evidence_views() -> None:
    factory = _factory()
    variant_id = seed_reviewable(factory)[0]
    service = ReviewService(factory, _policy())
    page = service.queue(limit=10)
    detail = service.detail(artifact_type="content_variant", artifact_id=variant_id)
    assert page.total == 1
    assert page.items[0].artifact_id == variant_id
    assert detail.current_reviewable is True
    assert detail.quality_check["passed"] is True
    assert detail.generation_ai_provenance.task == "CONTENT_GENERATION"
    with factory() as session:
        assert session.get(ContentVariant, variant_id).review_state is ReviewState.READY_FOR_REVIEW
        assert session.scalar(select(ReviewDecisionRecord)) is None
        assert session.scalar(select(AuditLog)) is None


def test_first_queue_page_hydrates_only_a_bounded_subset(monkeypatch) -> None:
    factory = _factory()
    seed_reviewable(factory, variants=205)
    service = ReviewService(factory, _policy())
    original = service._load_graph
    loaded = 0

    def counted(*args, **kwargs):
        nonlocal loaded
        loaded += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_load_graph", counted)
    first = service.queue(limit=10)
    second = service.queue(limit=10)

    assert first.total == 205
    assert len(first.items) == 10
    assert [item.artifact_id for item in first.items] == [item.artifact_id for item in second.items]
    assert loaded == 20


def test_queue_overfetches_in_bounded_batches_to_skip_invalid_candidates(monkeypatch) -> None:
    factory = _factory()
    variant_ids = seed_reviewable(factory, variants=70)
    old = datetime(2020, 1, 1, tzinfo=UTC)
    new = datetime(2030, 1, 1, tzinfo=UTC)
    with factory() as session, session.begin():
        for index, variant_id in enumerate(variant_ids):
            variant = session.get(ContentVariant, variant_id)
            if index < 55:
                variant.caption = "Changed after its exact quality assessment"
                variant.updated_at = old
            else:
                variant.updated_at = new
    service = ReviewService(factory, _policy())
    original = service._load_graph
    loaded = 0

    def counted(*args, **kwargs):
        nonlocal loaded
        loaded += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_load_graph", counted)
    page = service.queue(limit=10)

    assert len(page.items) == 10
    assert 55 < loaded <= 100
    assert loaded < len(variant_ids)


def test_superseded_item_is_not_queued_but_remains_visible_as_historical_detail() -> None:
    factory = _factory()
    variant_id = seed_reviewable(factory)[0]
    with factory() as session, session.begin():
        variant = session.get(ContentVariant, variant_id)
        draft = session.get(ContentDraft, variant.content_draft_id)
        sheet = session.get(FactSheet, draft.fact_sheet_id)
        session.add(
            FactSheet(
                story_id=sheet.story_id,
                version=2,
                headline="Correction",
                summary="A corrected factual basis.",
                risk_level=sheet.risk_level,
                sensitive_topics=[],
                semantic_key=f"corrected:{uuid4()}",
            )
        )
    service = ReviewService(factory, _policy())
    assert service.queue().items == ()
    detail = service.detail(artifact_type="content_variant", artifact_id=variant_id)
    assert detail.current_reviewable is False
    assert detail.artifact_id == variant_id


def test_closed_action_contract_rejects_client_controlled_identity_and_time() -> None:
    with pytest.raises(ValidationError):
        ReviewActionRequest.model_validate(
            {
                "artifact_version": 1,
                "reviewer_id": str(uuid4()),
                "decided_at": datetime.now(UTC).isoformat(),
            }
        )
