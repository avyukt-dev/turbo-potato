"""Extraction classification requires actual versioned model output and honest provenance."""

import asyncio
from dataclasses import replace

import news_ai_ai.claim_extraction as extraction
import pytest
from news_ai_ai import ClaimExtractionOutput, ClaimExtractionService, ProviderLocality
from news_ai_database import AIRun, Claim, EventOutbox
from news_ai_domain import ClaimVerificationStatus
from news_ai_events import PermanentEventError
from pydantic import ValidationError
from sqlalchemy import func, select
from unit.ai.test_claim_extraction import (
    FakeProvider,
    _claim,
    _prompt,
    _response,
    _router,
    _seed_story,
    _session_factory,
    _story_event,
)


@pytest.mark.parametrize(
    "semantic_type,state",
    [
        ("EVENT", "OBSERVED"),
        ("POLICY_COMMITMENT", "ANNOUNCED"),
        ("POLICY_COMMITMENT", "PLANNED"),
        ("PREDICTION_FORECAST", "EXPECTED"),
        ("PREDICTION_FORECAST", "PREDICTED"),
        ("LEGAL_PROCEDURAL", "OBSERVED"),
        ("ATTRIBUTION", "OBSERVED"),
    ],
)
def test_v2_closed_classification(semantic_type, state):
    item = {**_claim(), "semantic_type": semantic_type, "semantic_state": state}
    assert (
        ClaimExtractionOutput.model_validate({"claims": [item]}).claims[0].semantic_state == state
    )


@pytest.mark.parametrize("field", ["semantic_type", "semantic_state"])
@pytest.mark.parametrize("mode", ["missing", "invalid"])
def test_v2_classification_required(field, mode):
    item = _claim()
    if mode == "missing":
        del item[field]
    else:
        item[field] = "INVALID"
    with pytest.raises(ValidationError):
        ClaimExtractionOutput.model_validate({"claims": [item]})


def extract(factory, service, story_id, item):
    provider = service.router.registry.get("local")
    provider.outcomes.append(_response("local", [item]))
    event = _story_event(story_id)
    with factory() as session:
        context = service.load_context(session, story_id)
    execution = asyncio.run(service.generate(context, event))
    with factory() as session, session.begin():
        return service.persist(
            session, context=context, triggering_event=event, execution=execution
        )


def service_for(tmp_path):
    return ClaimExtractionService(
        _router(FakeProvider("local", ProviderLocality.LOCAL, [])), _prompt(tmp_path)
    )


def test_new_and_reused_classification_is_honest_and_preserves_verification(tmp_path):
    factory = _session_factory()
    story, _, _ = _seed_story(factory)
    service = service_for(tmp_path)
    first = extract(factory, service, story.id, _claim())
    with factory() as session, session.begin():
        claim = session.get(Claim, first.claim_ids[0])
        assert claim.status == ClaimVerificationStatus.UNASSESSED
        assert claim.semantic_ai_run_id == first.ai_run_id
        assert claim.semantic_type == "POLICY_COMMITMENT"
        assert claim.semantic_state == "ANNOUNCED"
        assert session.get(AIRun, claim.semantic_ai_run_id).prompt_version == "v3"
        # Simulate an honest pre-PR3 row; original wording/verification remains historical.
        claim.status = ClaimVerificationStatus.SUPPORTED
        claim.claim_type = "POLICY_ACTION"
        claim.semantic_type = claim.semantic_state = None
        claim.semantic_policy_version = claim.semantic_ai_run_id = None
    service.prompt = replace(service.prompt, checksum="c" * 64)
    second = extract(factory, service, story.id, _claim())
    with factory() as session:
        claim = session.get(Claim, first.claim_ids[0])
        assert first.claim_ids == second.claim_ids
        assert claim.status == ClaimVerificationStatus.SUPPORTED
        assert claim.claim_type == "POLICY_ACTION"
        assert claim.semantic_ai_run_id == second.ai_run_id
    service.prompt = replace(service.prompt, checksum="d" * 64)
    third = extract(factory, service, story.id, _claim())
    with factory() as session:
        assert session.get(Claim, first.claim_ids[0]).semantic_ai_run_id == second.ai_run_id
        assert third.claim_ids == first.claim_ids


@pytest.mark.parametrize(
    "field,value", [("semantic_type", "EVENT"), ("semantic_state", "OBSERVED")]
)
def test_reused_current_classification_conflict_rolls_back(tmp_path, field, value):
    factory = _session_factory()
    story, _, _ = _seed_story(factory)
    service = service_for(tmp_path)
    first = extract(factory, service, story.id, _claim())
    service.prompt = replace(service.prompt, checksum="c" * 64)
    with pytest.raises(PermanentEventError, match="CLAIM_SEMANTICS_CONFLICT"):
        extract(factory, service, story.id, {**_claim(), field: value})
    with factory() as session:
        assert session.get(Claim, first.claim_ids[0]).semantic_state == "ANNOUNCED"
        assert session.scalar(select(func.count()).select_from(AIRun)) == 1
        assert session.scalar(select(func.count()).select_from(EventOutbox)) == 1


def test_semantic_replay_serialized_at_persistence(tmp_path):
    factory = _session_factory()
    story, _, _ = _seed_story(factory)
    service = service_for(tmp_path)
    first = extract(factory, service, story.id, _claim())
    second = extract(factory, service, story.id, _claim())
    assert first.event_id == second.event_id and first.ai_run_id == second.ai_run_id
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Claim)) == 1
        assert session.scalar(select(func.count()).select_from(AIRun)) == 1


@pytest.mark.parametrize(
    "field", ["context", "prompt_id", "version", "checksum", "methodology", "policy"]
)
def test_identity_binds_all_operation_inputs(tmp_path, monkeypatch, field):
    service = service_for(tmp_path)
    before = service.operation_identity("a" * 64)
    if field == "context":
        assert before != service.operation_identity("b" * 64)
        return
    if field in ("prompt_id", "version", "checksum"):
        service.prompt = replace(service.prompt, **{field: "changed"})
    else:
        constant = (
            "CLAIM_EXTRACTION_METHODOLOGY_VERSION"
            if field == "methodology"
            else "CLAIM_SEMANTICS_POLICY_VERSION"
        )
        monkeypatch.setattr(extraction, constant, "future-v2")
    assert before != service.operation_identity("a" * 64)


def test_v1_outbox_cannot_complete_current_v2(tmp_path):
    factory = _session_factory()
    story, _, _ = _seed_story(factory)
    service = service_for(tmp_path)
    result = extract(factory, service, story.id, _claim())
    with factory() as session, session.begin():
        context = service.load_context(session, story.id)
        row = session.scalar(select(EventOutbox).where(EventOutbox.event_id == result.event_id))
        row.idempotency_key = f"claims.extracted:{story.id}:{context.context_hash}"
    with factory() as session:
        assert (
            service.existing_result(session, story_id=story.id, context_hash=context.context_hash)
            is None
        )
