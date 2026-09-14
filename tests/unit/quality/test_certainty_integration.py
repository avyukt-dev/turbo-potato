"""Certainty remains bound to exact durable content and immutable facts."""

from uuid import uuid4

import pytest
from news_ai_content import ContentGenerationOutput, content_artifact_hash
from news_ai_content.certainty import ClaimPresentation
from news_ai_database import ContentDraft, ContentQualityCheck, ContentVariant, EventOutbox
from news_ai_domain import ReviewState
from news_ai_events import PermanentEventError
from news_ai_quality import QualityAssessmentOutput
from news_ai_quality.semantic import SemanticValidator
from news_ai_review import ReviewService
from pydantic import ValidationError
from sqlalchemy import func, select
from unit.quality.test_quality_gate import QualityAI, _factory, _run, _seed, _service
from unit.quality.test_semantic import artifacts as _artifacts
from unit.review.test_review_service import _policy


@pytest.fixture
def artifacts():
    return _artifacts.__wrapped__()


@pytest.mark.parametrize("kind", ["missing", "duplicate", "extra"])
def test_exact_presentation_coverage(artifacts, kind):
    _, content, _ = artifacts
    data = content.model_dump(mode="json")
    item = data["claim_presentations"][0]
    data["claim_presentations"] = (
        []
        if kind == "missing"
        else [item, {**item, "claim_id": str(uuid4())} if kind == "extra" else item]
    )
    with pytest.raises(ValidationError):
        ContentGenerationOutput.model_validate(data)


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("source_status", "SUPPORTED", "CERTAINTY_STATUS_MISMATCH"),
        ("source_fact_check_label", "TRUE", "CERTAINTY_LABEL_MISMATCH"),
        ("assertion_strength", "HIGH", "CERTAINTY_CEILING_EXCEEDED"),
        ("frame", "DIRECT", "CERTAINTY_FRAME_MISMATCH"),
    ],
)
def test_quality_independently_revalidates_and_overrides_ai(field, value, code):
    factory, ai = _factory(), QualityAI()
    event, draft_id, variant_id = _seed(factory)
    with factory() as session, session.begin():
        variant = session.get(ContentVariant, variant_id)
        payload = dict(variant.structured_payload)
        payload["claim_presentations"] = [{**payload["claim_presentations"][0], field: value}]
        variant.structured_payload = payload
    context, result = _run(factory, _service(ai), event)
    assert ai.calls == 1 and result.passed is False
    with factory() as session:
        check = session.scalar(select(ContentQualityCheck))
        assert code in {f["code"] for f in check.semantic_findings["findings"]}
        assert check.certainty_escalations == []
        assert check.semantic_methodology_version == "semantic-validator-v2"
        assert check.methodology_version == "quality-gate-methodology-v5"
        assert session.get(ContentDraft, draft_id).review_state == ReviewState.NOT_READY
        assert session.get(ContentVariant, variant_id).review_state == ReviewState.NOT_READY
        detail = ReviewService(factory, _policy()).detail(
            artifact_type="content_variant", artifact_id=variant_id
        )
        assert detail.quality_check["semantic_findings"] == check.semantic_findings


def test_prose_escalation_is_durable_and_visible_without_recomputation():
    factory = _factory()
    event, _, variant_id = _seed(factory)
    with factory() as session:
        presentation = session.get(ContentVariant, variant_id).structured_payload[
            "claim_presentations"
        ][0]
    escalation = {
        "claim_id": presentation["claim_id"],
        "artifact_path": "slides[0].body",
        "reason_code": "QUALIFICATION_OMITTED",
    }
    ai = QualityAI(mutate={"certainty_escalations": [escalation]})
    context, result = _run(factory, _service(ai), event)
    assert context.variants[0].semantic_report.passed and not result.passed
    with factory() as session:
        check = session.scalar(select(ContentQualityCheck))
        assert check.certainty_escalations == [escalation]
        assert (
            session.scalar(
                select(EventOutbox).where(EventOutbox.event_type == "content.quality_checked")
            ).payload["fact_check_passed"]
            is False
        )
    detail = ReviewService(factory, _policy()).detail(
        artifact_type="content_variant", artifact_id=variant_id
    )
    assert detail.quality_check["certainty_escalations"] == [escalation]
    assert detail.content_variant["structured_payload"]["claim_presentations"] == [presentation]


def test_historical_content_missing_metadata_requires_regeneration():
    factory, ai = _factory(), QualityAI()
    event, draft_id, variant_id = _seed(factory)
    with factory() as session, session.begin():
        variant = session.get(ContentVariant, variant_id)
        variant.structured_payload = {
            k: v for k, v in variant.structured_payload.items() if k != "claim_presentations"
        }
    with factory() as session, pytest.raises(PermanentEventError):
        _service(ai).load_context(session, event)
    assert ai.calls == 0
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContentQualityCheck)) == 0


def test_quality_policy_version_invalidates_identity(monkeypatch):
    import news_ai_quality.service as service_module

    factory = _factory()
    event, _, _ = _seed(factory)
    service = _service(QualityAI())
    before, _ = _run(factory, service, event)
    monkeypatch.setattr(service_module, "CERTAINTY_POLICY_VERSION", "certainty-policy-v2")
    after, result = _run(factory, service, event)
    assert before.event_semantic_key != after.event_semantic_key and result.created


@pytest.mark.parametrize("kind", ["wrong_claim", "wrong_location", "unknown_code", "duplicate"])
def test_invalid_ai_certainty_findings_follow_invalid_response_path(kind):
    import asyncio

    factory = _factory()
    event, _, variant_id = _seed(factory)
    with factory() as session:
        claim_id = session.get(ContentVariant, variant_id).claim_ids_used[0]
    finding = {
        "claim_id": claim_id,
        "artifact_path": "caption",
        "reason_code": "QUALIFICATION_OMITTED",
    }
    if kind == "wrong_claim":
        finding["claim_id"] = str(uuid4())
    elif kind == "wrong_location":
        finding["artifact_path"] = "slides[19].body"
    elif kind == "unknown_code":
        finding["reason_code"] = "PROVIDER_SECRET_SENTINEL"
    ai = QualityAI(
        mutate={"certainty_escalations": [finding, finding] if kind == "duplicate" else [finding]}
    )
    service = _service(ai)
    with factory() as session:
        context = service.load_context(session, event)
    with pytest.raises(PermanentEventError) as caught:
        asyncio.run(service.assess(context, event))
    assert "PROVIDER_SECRET_SENTINEL" not in str(caught.value)


def test_ai_must_explicitly_report_prose_certainty_result():
    with pytest.raises(ValidationError):
        QualityAssessmentOutput.model_validate(
            {
                "content_variant_id": uuid4(),
                "factual_accuracy_passed": True,
                "source_alignment_passed": True,
                "citation_alignment_passed": True,
                "style_passed": True,
                "defamation_risk": False,
                "sensitive_topic_error": False,
            }
        )


def test_missing_presentation_and_invalid_upstream_pair_have_typed_errors(artifacts):
    sheet, content, sources = artifacts
    validator = SemanticValidator()
    missing = validator.validate(
        sheet, content.model_copy(update={"claim_presentations": ()}), source_ids_used=sources
    )
    assert any(f.code == "CERTAINTY_PRESENTATION_MISSING" for f in missing.findings)
    invalid_sheet = sheet.model_copy(
        update={"fact_checks": (sheet.fact_checks[0].model_copy(update={"label": "FALSE"}),)}
    )
    invalid = validator.validate(invalid_sheet, content, source_ids_used=sources)
    assert any(f.code == "CERTAINTY_SOURCE_COMBINATION_INVALID" for f in invalid.findings)


def test_v4_result_is_not_reused_as_v5(monkeypatch):
    import news_ai_quality.service as service_module

    factory = _factory()
    event, _, _ = _seed(factory)
    service = _service(QualityAI())
    with monkeypatch.context() as patch:
        patch.setattr(service_module, "QUALITY_METHODOLOGY_VERSION", "quality-gate-methodology-v4")
        before, _ = _run(factory, service, event)
    with factory() as session, session.begin():
        old = session.scalar(select(ContentQualityCheck))
        old.certainty_escalations = None
        old_id = old.id
    after, result = _run(factory, service, event)
    assert before.event_semantic_key != after.event_semantic_key and result.created
    with factory() as session:
        assert session.get(ContentQualityCheck, old_id).certainty_escalations is None
        assert (
            session.get(ContentQualityCheck, old_id).methodology_version
            == "quality-gate-methodology-v4"
        )


def test_presentation_changes_exact_artifact_hash(artifacts):
    _, content, _ = artifacts
    first = content.structured_payload()
    second = {
        **first,
        "claim_presentations": [{**first["claim_presentations"][0], "assertion_strength": "LOW"}],
    }
    assert content_artifact_hash(first) != content_artifact_hash(second)


def test_multi_claim_cannot_inherit_stronger_ceiling(artifacts):
    sheet, content, source_ids = artifacts
    weak = sheet.claims[0].model_copy(
        update={"claim_id": uuid4(), "status": "UNVERIFIED", "evidence_ids": ()}
    )
    weak_check = sheet.fact_checks[0].model_copy(
        update={
            "claim_id": weak.claim_id,
            "fact_check_id": uuid4(),
            "label": "UNVERIFIED",
            "supporting_evidence_ids": (),
        }
    )
    sheet = sheet.model_copy(
        update={"claims": (*sheet.claims, weak), "fact_checks": (*sheet.fact_checks, weak_check)}
    )
    copied = ClaimPresentation(
        claim_id=weak.claim_id,
        source_status="UNVERIFIED",
        source_fact_check_label="UNVERIFIED",
        assertion_strength="MEDIUM",
        frame="QUALIFIED",
    )
    content = content.model_copy(
        update={
            "claim_ids_used": (*content.claim_ids_used, weak.claim_id),
            "claim_presentations": (*content.claim_presentations, copied),
            "slides": (
                content.slides[0].model_copy(
                    update={"claim_ids": (*content.slides[0].claim_ids, weak.claim_id)}
                ),
                content.slides[1],
            ),
        }
    )
    report = SemanticValidator().validate(sheet, content, source_ids_used=source_ids)
    assert not report.passed
    assert any(
        f.code == "CERTAINTY_CEILING_EXCEEDED" and weak.claim_id in f.claim_ids
        for f in report.findings
    )
