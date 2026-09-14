"""Independent durable metadata checks and secondary prose analysis cannot change truth."""

import asyncio
from uuid import uuid4

import pytest
from helpers.claim_semantics import presentation
from news_ai_content import ClaimSemanticPresentation
from news_ai_database import (
    ContentDraft,
    ContentQualityCheck,
    ContentVariant,
    EventOutbox,
    FactSheet,
)
from news_ai_domain import ReviewState
from news_ai_events import PermanentEventError
from news_ai_quality import SemanticValidator
from news_ai_review import ReviewService
from sqlalchemy import func, select
from unit.quality.test_quality_gate import QualityAI, _factory, _run, _seed, _service
from unit.quality.test_semantic import artifacts as artifact_fixture
from unit.review.test_review_service import _policy


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("source_semantic_type", "EVENT", "SOURCE_TYPE_MISMATCH"),
        ("source_semantic_state", "PLANNED", "SOURCE_STATE_MISMATCH"),
        ("presented_semantic_type", "EVENT", "TYPE_MISMATCH"),
        ("presented_semantic_state", "PLANNED", "STATE_MISMATCH"),
    ],
)
def test_independent_semantic_error_overrides_ai_and_is_durable(field, value, code):
    factory, ai = _factory(), QualityAI()
    event, draft_id, variant_id = _seed(factory)
    with factory() as session, session.begin():
        row = session.get(ContentVariant, variant_id)
        payload = dict(row.structured_payload)
        payload["claim_semantic_presentations"] = [
            {**payload["claim_semantic_presentations"][0], field: value}
        ]
        row.structured_payload = payload
    context, result = _run(factory, _service(ai), event)
    assert ai.calls == 1 and not result.passed
    with factory() as session:
        check = session.scalar(select(ContentQualityCheck))
        assert f"CLAIM_SEMANTICS_{code}" in {f["code"] for f in check.semantic_findings["findings"]}
        assert check.claim_semantic_escalations == []
        assert session.get(ContentDraft, draft_id).review_state == ReviewState.NOT_READY
        assert session.get(ContentVariant, variant_id).review_state == ReviewState.NOT_READY
        assert (
            _service(ai).existing_result(session, context).quality_check_ids
            == result.quality_check_ids
        )
    detail = ReviewService(factory, _policy()).detail(
        artifact_type="content_variant", artifact_id=variant_id
    )
    assert detail.quality_check["semantic_findings"] == check.semantic_findings
    assert (
        detail.content_variant["structured_payload"]["claim_semantic_presentations"]
        == payload["claim_semantic_presentations"]
    )


@pytest.mark.parametrize(
    "code",
    [
        "ANNOUNCEMENT_AS_COMPLETED",
        "PLAN_AS_COMPLETED",
        "EXPECTATION_AS_OBSERVED",
        "PREDICTION_AS_OUTCOME",
        "ATTRIBUTION_DROPPED",
        "SEMANTIC_TYPE_RECAST",
    ],
)
def test_prose_semantic_escalations_fail_and_are_visible(code):
    factory = _factory()
    event, _, variant_id = _seed(factory)
    with factory() as session:
        claim_id = session.get(ContentVariant, variant_id).claim_ids_used[0]
        snapshot_before = session.scalar(select(FactSheet)).claims_snapshot
    escalation = {"claim_id": claim_id, "artifact_path": "caption", "reason_code": code}
    context, result = _run(
        factory, _service(QualityAI(mutate={"claim_semantic_escalations": [escalation]})), event
    )
    assert context.variants[0].semantic_report.passed and not result.passed
    with factory() as session:
        check = session.scalar(select(ContentQualityCheck))
        assert check.claim_semantic_escalations == [escalation]
        assert (
            session.scalar(
                select(EventOutbox).where(EventOutbox.event_type == "content.quality_checked")
            ).payload["fact_check_passed"]
            is False
        )
        assert session.get(FactSheet, context.fact_sheet_id).claims_snapshot == snapshot_before
    detail = ReviewService(factory, _policy()).detail(
        artifact_type="content_variant", artifact_id=variant_id
    )
    assert detail.quality_check["claim_semantic_escalations"] == [escalation]


@pytest.mark.parametrize("mode", ["code", "claim", "path", "duplicate"])
def test_invalid_prose_findings_follow_invalid_response_path(mode):
    factory = _factory()
    event, _, variant_id = _seed(factory)
    with factory() as session:
        claim_id = session.get(ContentVariant, variant_id).claim_ids_used[0]
    finding = {"claim_id": claim_id, "artifact_path": "caption", "reason_code": "PLAN_AS_COMPLETED"}
    if mode == "code":
        finding["reason_code"] = "SECRET_SENTINEL"
    elif mode == "claim":
        finding["claim_id"] = str(uuid4())
    elif mode == "path":
        finding["artifact_path"] = "slides[19].body"
    service = _service(
        QualityAI(
            mutate={
                "claim_semantic_escalations": [finding, finding]
                if mode == "duplicate"
                else [finding]
            }
        )
    )
    with factory() as session:
        context = service.load_context(session, event)
    with pytest.raises(PermanentEventError) as error:
        asyncio.run(service.assess(context, event))
    assert "SECRET_SENTINEL" not in str(error.value)


@pytest.mark.parametrize("missing", ["sheet", "brief", "presentation"])
def test_historical_missing_semantics_cannot_be_evaluated_as_current(missing):
    factory, ai = _factory(), QualityAI()
    event, draft_id, variant_id = _seed(factory)
    with factory() as session, session.begin():
        if missing == "sheet":
            sheet = session.scalar(select(FactSheet))
            sheet.claims_snapshot = [
                {k: v for k, v in sheet.claims_snapshot[0].items() if k != "semantics"}
            ]
        elif missing == "brief":
            draft = session.get(ContentDraft, draft_id)
            brief = dict(draft.editorial_brief_snapshot)
            brief["claims"] = [{k: v for k, v in brief["claims"][0].items() if k != "semantics"}]
            draft.editorial_brief_snapshot = brief
        else:
            variant = session.get(ContentVariant, variant_id)
            variant.structured_payload = {
                k: v
                for k, v in variant.structured_payload.items()
                if k != "claim_semantic_presentations"
            }
    with factory() as session, pytest.raises(PermanentEventError):
        _service(ai).load_context(session, event)
    assert ai.calls == 0
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContentQualityCheck)) == 0


def test_multi_claim_no_state_or_type_laundering():
    sheet, content, sources = artifact_fixture.__wrapped__()
    observed = sheet.claims[0]
    planned = observed.model_copy(
        update={
            "claim_id": uuid4(),
            "evidence_ids": (),
            "semantics": observed.semantics.model_copy(
                update={"semantic_type": "POLICY_COMMITMENT", "semantic_state": "PLANNED"}
            ),
        }
    )
    check = sheet.fact_checks[0].model_copy(
        update={
            "fact_check_id": uuid4(),
            "claim_id": planned.claim_id,
            "supporting_evidence_ids": (),
        }
    )
    sheet = sheet.model_copy(
        update={"claims": (*sheet.claims, planned), "fact_checks": (*sheet.fact_checks, check)}
    )
    first = content.slides[0].model_copy(
        update={"claim_ids": (observed.claim_id, planned.claim_id)}
    )
    copied_certainty = content.claim_presentations[0].model_copy(
        update={"claim_id": planned.claim_id}
    )
    # The other claim's observed state cannot authorize this plan as an observed event.
    wrong = ClaimSemanticPresentation.model_validate(presentation(planned.claim_id))
    content = content.model_copy(
        update={
            "slides": (first, content.slides[1]),
            "claim_ids_used": (observed.claim_id, planned.claim_id),
            "claim_presentations": (*content.claim_presentations, copied_certainty),
            "claim_semantic_presentations": (*content.claim_semantic_presentations, wrong),
        }
    )
    report = SemanticValidator().validate(sheet, content, source_ids_used=sources)
    assert not report.passed
    assert {f.code.value for f in report.findings} >= {
        "CLAIM_SEMANTICS_SOURCE_STATE_MISMATCH",
        "CLAIM_SEMANTICS_STATE_MISMATCH",
        "CLAIM_SEMANTICS_TYPE_MISMATCH",
    }


def test_v5_v2_checks_cannot_satisfy_current_v6_v3(monkeypatch):
    import news_ai_quality.service as quality

    factory = _factory()
    event, _, _ = _seed(factory)
    service = _service(QualityAI())
    with monkeypatch.context() as patch:
        patch.setattr(quality, "QUALITY_METHODOLOGY_VERSION", "quality-gate-methodology-v5")
        service.semantic_validator.methodology_version = "semantic-validator-v2"
        previous, first = _run(factory, service, event)
    with factory() as session, session.begin():
        old = session.get(ContentQualityCheck, first.quality_check_ids[0])
        old.claim_semantic_escalations = None
    service.semantic_validator.methodology_version = "semantic-validator-v3"
    current, second = _run(factory, service, event)
    assert previous.event_semantic_key != current.event_semantic_key
    assert first.quality_check_ids != second.quality_check_ids
    with factory() as session:
        old = session.get(ContentQualityCheck, first.quality_check_ids[0])
        assert old.methodology_version == "quality-gate-methodology-v5"
        assert old.semantic_methodology_version == "semantic-validator-v2"
        assert old.claim_semantic_escalations is None


def test_brief_semantics_mismatch_has_deterministic_finding():
    factory, ai = _factory(), QualityAI()
    event, draft_id, _ = _seed(factory)
    with factory() as session, session.begin():
        draft = session.get(ContentDraft, draft_id)
        brief = dict(draft.editorial_brief_snapshot)
        brief["claims"] = [
            {
                **brief["claims"][0],
                "semantics": {**brief["claims"][0]["semantics"], "semantic_state": "PLANNED"},
            }
        ]
        draft.editorial_brief_snapshot = brief
    context, result = _run(factory, _service(ai), event)
    assert ai.calls == 1 and not result.passed
    assert any(
        f.code == "CLAIM_SEMANTICS_POLICY_MISMATCH"
        for f in context.variants[0].semantic_report.findings
    )
