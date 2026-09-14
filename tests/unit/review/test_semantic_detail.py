"""Read-only visibility of the exact durable deterministic quality report."""

from datetime import UTC, datetime

from news_ai_database import ContentQualityCheck, FactSheet
from news_ai_quality import SemanticValidator
from news_ai_review import ReviewService
from sqlalchemy import select

from unit.quality.test_quality_gate import QualityAI, _factory, _run, _seed, _service
from unit.review.test_review_service import _policy, seed_reviewable


def check_warning_detail(factory, monkeypatch):
    event, _, variant_id = _seed(factory)
    with factory() as session, session.begin():
        sheet = session.scalar(select(FactSheet))
        claim = dict(sheet.claims_snapshot[0])
        claim["temporal_start"] = datetime(2026, 1, 1).isoformat()
        claim["temporal_end"] = datetime(2026, 1, 2, tzinfo=UTC).isoformat()
        sheet.claims_snapshot = [claim]
    ai = QualityAI()
    _run(factory, _service(ai), event)
    with factory() as session:
        check = session.scalar(select(ContentQualityCheck))
        expected_id = str(check.id)
        expected_report = check.semantic_findings

    def forbidden(*args, **kwargs):
        raise AssertionError("review must not recompute semantic validation")

    monkeypatch.setattr(SemanticValidator, "validate", forbidden)
    detail = ReviewService(factory, _policy()).detail(
        artifact_type="content_variant", artifact_id=variant_id
    )
    assert detail.current_reviewable is True
    payload = detail.model_dump(mode="json")["quality_check"]
    assert payload["quality_check_id"] == expected_id
    assert payload["semantic_validation_passed"] is True
    assert payload["semantic_methodology_version"] == "semantic-validator-v4"
    assert payload["semantic_findings"] == expected_report
    assert payload["claim_semantic_escalations"] == []
    assert payload["value_escalations"] == []
    assert detail.fact_sheet["claims"][0]["semantics"]["semantic_state"] == "OBSERVED"
    assert detail.editorial_brief["claims"][0]["semantics"]["semantic_state"] == "OBSERVED"
    assert detail.content_variant["structured_payload"]["claim_semantic_presentations"]
    assert payload["semantic_findings"]["findings"][0]["severity"] == "WARNING"
    assert ai.calls == 1


def test_warning_only_quality_is_reviewable_and_visible_without_recomputation(monkeypatch):
    check_warning_detail(_factory(), monkeypatch)


def test_nullable_semantic_fields_remain_null_in_serialized_review_detail():
    factory = _factory()
    variant_id = seed_reviewable(factory)[0]
    detail = ReviewService(factory, _policy()).detail(
        artifact_type="content_variant", artifact_id=variant_id
    )
    payload = detail.model_dump(mode="json")["quality_check"]
    assert payload["semantic_validation_passed"] is None
    assert payload["semantic_methodology_version"] is None
    assert payload["semantic_findings"] is None
    assert payload["certainty_escalations"] is None
    assert payload["claim_semantic_escalations"] is None
    assert payload["value_escalations"] is None


def test_historical_v3_quality_payload_does_not_fabricate_semantic_result():
    factory = _factory()
    seed_reviewable(factory)
    with factory() as session, session.begin():
        legacy = session.scalar(select(ContentQualityCheck))
        legacy.methodology_version = "quality-gate-methodology-v3"
        payload = ReviewService._quality_payload(legacy)
    assert payload["semantic_validation_passed"] is None
    assert payload["semantic_methodology_version"] is None
    assert payload["semantic_findings"] is None
