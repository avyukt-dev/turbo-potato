"""Current evaluated value classification cannot reuse PR3-only operations."""

from dataclasses import replace

import news_ai_ai.claim_extraction as extraction
import pytest
from news_ai_ai import ClaimExtractionOutput
from news_ai_database import Claim
from news_ai_events import PermanentEventError
from pydantic import ValidationError
from unit.ai.test_claim_extraction import _claim, _seed_story, _session_factory
from unit.ai.test_extraction_semantics import extract, service_for
from unit.domain.test_value_policy import measurement


def item(text="The convoy travelled 5 km.", amount="5"):
    return {
        **_claim(text),
        "value_candidates": [measurement(f"{amount} km", amount).model_dump(mode="json")],
    }


def test_values_required_and_span_bound():
    value = _claim()
    assert (
        ClaimExtractionOutput.model_validate({"claims": [value]}).claims[0].value_candidates == ()
    )
    del value["value_candidates"]
    with pytest.raises(ValidationError):
        ClaimExtractionOutput.model_validate({"claims": [value]})
    with pytest.raises(ValidationError):
        ClaimExtractionOutput.model_validate({"claims": [item("The convoy travelled nowhere.")]})
    with pytest.raises(ValidationError):
        ClaimExtractionOutput.model_validate({"claims": [item("The convoy travelled 15 km.", "5")]})


def test_value_anchors_durable_identity_and_reused_provenance(tmp_path):
    factory = _session_factory()
    story, _, _ = _seed_story(factory)
    service = service_for(tmp_path)
    first = extract(factory, service, story.id, item())
    with factory() as session, session.begin():
        claim = session.get(Claim, first.claim_ids[0])
        assert claim.status == "UNASSESSED"
        assert claim.value_ai_run_id == first.ai_run_id
        anchors = claim.value_anchors
        semantic_run = claim.semantic_ai_run_id
        claim.status = "SUPPORTED"
        claim.value_anchors = claim.value_policy_version = claim.value_ai_run_id = None
    service.prompt = replace(service.prompt, checksum="b" * 64)
    second = extract(factory, service, story.id, item())
    with factory() as session:
        claim = session.get(Claim, first.claim_ids[0])
        assert claim.status == "SUPPORTED" and claim.semantic_ai_run_id == semantic_run
        assert claim.value_ai_run_id == second.ai_run_id and claim.value_anchors == anchors
    service.prompt = replace(service.prompt, checksum="c" * 64)
    third = extract(factory, service, story.id, item())
    with factory() as session:
        assert session.get(Claim, third.claim_ids[0]).value_ai_run_id == second.ai_run_id
    assert first.claim_ids == third.claim_ids


def test_conflicting_reused_value_semantics_fail_closed(tmp_path):
    factory = _session_factory()
    story, _, _ = _seed_story(factory)
    service = service_for(tmp_path)
    text = "The route includes 5 km and 6 km."
    first = extract(factory, service, story.id, item(text))
    service.prompt = replace(service.prompt, checksum="d" * 64)
    with pytest.raises(PermanentEventError, match="CLAIM_VALUES_CONFLICT"):
        extract(factory, service, story.id, item(text, "6"))
    with factory() as session:
        assert session.get(Claim, first.claim_ids[0]).value_anchors[0]["source_text"] == "5 km"


def test_value_policy_and_previous_extraction_methodology_change_identity(tmp_path, monkeypatch):
    service = service_for(tmp_path)
    original = service.operation_identity("context")
    monkeypatch.setattr(extraction, "VALUE_INTEGRITY_POLICY_VERSION", "value-integrity-policy-v2")
    assert service.operation_identity("context") != original
    monkeypatch.setattr(extraction, "VALUE_INTEGRITY_POLICY_VERSION", "value-integrity-policy-v1")
    monkeypatch.setattr(
        extraction, "CLAIM_EXTRACTION_METHODOLOGY_VERSION", "claim-extraction-methodology-v2"
    )
    assert service.operation_identity("context") != original
