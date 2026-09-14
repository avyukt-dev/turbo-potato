"""Independent metadata errors and secondary AI findings are durable and review-visible."""

import asyncio
from uuid import uuid4

import pytest
from news_ai_database import ContentDraft, ContentQualityCheck, ContentVariant, FactSheet
from news_ai_events import PermanentEventError
from news_ai_quality.contracts import ValueEscalationCode
from news_ai_review import ReviewService
from sqlalchemy import select
from unit.content.test_value_generation import attach_values, occurrence, record
from unit.quality.test_quality_gate import QualityAI, _factory, _run, _seed, _service
from unit.review.test_review_service import _policy


def test_deterministic_value_error_overrides_ai_and_is_review_visible():
    factory, ai = _factory(), QualityAI()
    event, draft_id, variant_id = _seed(factory)
    claim = attach_values(factory, quality=True)
    with factory() as session, session.begin():
        variant = session.get(ContentVariant, variant_id)
        payload = dict(variant.structured_payload)
        payload["claim_value_presentations"] = [
            record(claim, [occurrence("5 m", "5", "m", transformation="EXACT_UNIT_CONVERSION")])
        ]
        variant.caption = "The convoy travelled 5 m."
        variant.structured_payload = payload
    context, result = _run(factory, _service(ai), event)
    assert ai.calls == 1 and not result.passed
    with factory() as session:
        check = session.scalar(select(ContentQualityCheck))
        assert "VALUE_MAGNITUDE_MISMATCH" in {
            f["code"] for f in check.semantic_findings["findings"]
        }
        assert check.value_escalations == []
        assert session.get(ContentVariant, variant_id).review_state == "NOT_READY"
        assert session.get(ContentDraft, draft_id).review_state == "NOT_READY"
        assert (
            _service(ai).existing_result(session, context).quality_check_ids
            == result.quality_check_ids
        )
    detail = ReviewService(factory, _policy()).detail(
        artifact_type="content_variant", artifact_id=variant_id
    )
    assert detail.quality_check["semantic_findings"] == check.semantic_findings
    assert detail.quality_check["value_escalations"] == []
    assert detail.fact_sheet["claims"][0]["values"] == claim["values"]
    assert (
        detail.content_variant["structured_payload"]["claim_value_presentations"]
        == payload["claim_value_presentations"]
    )


@pytest.mark.parametrize("code", list(ValueEscalationCode))
def test_prose_value_escalations_fail_without_changing_fact_sheet(code):
    factory = _factory()
    event, _, variant_id = _seed(factory)
    claim = attach_values(factory, quality=True)
    finding = {
        "claim_id": claim["claim_id"],
        "anchor_id": None
        if code == "UNDECLARED_VALUE"
        else claim["values"]["anchors"][0]["anchor_id"],
        "artifact_path": "caption",
        "reason_code": code.value,
    }
    _, result = _run(factory, _service(QualityAI(mutate={"value_escalations": [finding]})), event)
    assert not result.passed
    with factory() as session:
        check = session.scalar(select(ContentQualityCheck))
        assert check.value_escalations == [finding]
        assert session.scalar(select(FactSheet)).claims_snapshot[0]["values"] == claim["values"]
    assert ReviewService(factory, _policy()).detail(
        artifact_type="content_variant", artifact_id=variant_id
    ).quality_check["value_escalations"] == [finding]


@pytest.mark.parametrize("mode", ["claim", "anchor", "path", "code", "null", "duplicate"])
def test_invalid_value_escalations_are_invalid_ai_response(mode):
    factory = _factory()
    event, _, _ = _seed(factory)
    claim = attach_values(factory, quality=True)
    finding = {
        "claim_id": claim["claim_id"],
        "anchor_id": claim["values"]["anchors"][0]["anchor_id"],
        "artifact_path": "caption",
        "reason_code": "MAGNITUDE_MISMATCH",
    }
    if mode == "claim":
        finding["claim_id"] = str(uuid4())
    elif mode == "anchor":
        finding["anchor_id"] = str(uuid4())
    elif mode == "path":
        finding["artifact_path"] = "slides[19].body"
    elif mode == "code":
        finding["reason_code"] = "SECRET_SENTINEL"
    elif mode == "null":
        finding["anchor_id"] = None
    service = _service(
        QualityAI(
            mutate={"value_escalations": [finding, finding] if mode == "duplicate" else [finding]}
        )
    )
    with factory() as session:
        context = service.load_context(session, event)
    with pytest.raises(PermanentEventError) as error:
        asyncio.run(service.assess(context, event))
    assert "SECRET_SENTINEL" not in str(error.value)


@pytest.mark.parametrize("owner", ["sheet", "brief", "content"])
def test_historical_missing_values_cannot_get_current_quality(owner):
    factory, ai = _factory(), QualityAI()
    event, draft_id, variant_id = _seed(factory)
    with factory() as session, session.begin():
        if owner == "sheet":
            sheet = session.scalar(select(FactSheet))
            sheet.claims_snapshot = [
                {k: v for k, v in sheet.claims_snapshot[0].items() if k != "values"}
            ]
        elif owner == "brief":
            draft = session.get(ContentDraft, draft_id)
            brief = dict(draft.editorial_brief_snapshot)
            brief["claims"] = [{k: v for k, v in brief["claims"][0].items() if k != "values"}]
            draft.editorial_brief_snapshot = brief
        else:
            variant = session.get(ContentVariant, variant_id)
            variant.structured_payload = {
                k: v
                for k, v in variant.structured_payload.items()
                if k != "claim_value_presentations"
            }
    with factory() as session, pytest.raises(PermanentEventError):
        _service(ai).load_context(session, event)
    assert ai.calls == 0


def test_v6_semantic_v3_result_is_not_reused_by_current_v7_v4(monkeypatch):
    import news_ai_quality.service as quality

    factory = _factory()
    event, _, _ = _seed(factory)
    service = _service(QualityAI())
    with monkeypatch.context() as patch:
        patch.setattr(quality, "QUALITY_METHODOLOGY_VERSION", "quality-gate-methodology-v6")
        service.semantic_validator.methodology_version = "semantic-validator-v3"
        old_context, old_result = _run(factory, service, event)
    service.semantic_validator.methodology_version = "semantic-validator-v4"
    with factory() as session:
        current = service.load_context(session, event)
        assert current.variants[0].semantic_key != old_context.variants[0].semantic_key
        assert service.existing_result(session, current) is None
        assert (
            session.get(ContentQualityCheck, old_result.quality_check_ids[0]).methodology_version
            == "quality-gate-methodology-v6"
        )


def test_ai_escalation_cannot_reference_another_claims_anchor():
    from dataclasses import replace

    from news_ai_domain.values import ClaimValues, anchors_for_claim
    from unit.domain.test_value_policy import measurement

    factory = _factory()
    event, _, _ = _seed(factory)
    first = attach_values(factory, quality=True)
    other = uuid4()
    block = ClaimValues(
        policy_version="value-integrity-policy-v1",
        anchors=anchors_for_claim(other, (measurement("5 km", "5"),)),
    )
    service = _service(
        QualityAI(
            mutate={
                "value_escalations": [
                    {
                        "claim_id": first["claim_id"],
                        "anchor_id": str(block.anchors[0].anchor_id),
                        "artifact_path": "caption",
                        "reason_code": "MAGNITUDE_MISMATCH",
                    }
                ]
            }
        )
    )
    with factory() as session:
        context = service.load_context(session, event)
    # The assessment seam sees an additional canonical anchor belonging to another claim.
    # It must reject that anchor even though it is known, not just reject unknown UUIDs.
    context = replace(
        context,
        fact_sheet={
            **context.fact_sheet,
            "claims": [
                *context.fact_sheet["claims"],
                {
                    **context.fact_sheet["claims"][0],
                    "claim_id": str(other),
                    "values": block.model_dump(mode="json"),
                },
            ],
        },
    )
    with pytest.raises(PermanentEventError):
        asyncio.run(service.assess(context, event))
