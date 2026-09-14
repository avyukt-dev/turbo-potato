"""Generation preserves declaration semantics independently of factual certainty."""

import asyncio
from uuid import uuid4

import news_ai_content.generation as generation
import pytest
from helpers.claim_semantics import presentation
from news_ai_content import (
    ClaimSemanticPresentation,
    ContentGenerationOutput,
    content_artifact_hash,
)
from news_ai_content.claim_semantics import semantic_presentation_violations
from news_ai_database import ContentVariant, FactSheet
from news_ai_domain import ClaimSemantics
from news_ai_events import PermanentEventError
from pydantic import ValidationError
from unit.content.test_content_generation import ContentAI, _factory, _seed, _service


@pytest.mark.parametrize("state", ["OBSERVED", "ANNOUNCED", "PLANNED", "EXPECTED", "PREDICTED"])
def test_exact_semantics_preserved_and_future_state_cannot_become_observed(state):
    semantics = ClaimSemantics(
        policy_version="claim-semantics-policy-v1",
        semantic_type="POLICY_COMMITMENT",
        semantic_state=state,
    )
    item = ClaimSemanticPresentation.model_validate(
        presentation(uuid4(), semantics.model_dump(mode="json"))
    )
    assert not semantic_presentation_violations(item, semantics)
    if state != "OBSERVED":
        assert "STATE_MISMATCH" in semantic_presentation_violations(
            item.model_copy(update={"presented_semantic_state": "OBSERVED"}), semantics
        )


@pytest.mark.parametrize("kind", ["missing", "duplicate", "extra"])
def test_exact_semantic_presentation_coverage(kind):
    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    service = _service(ai)
    with factory() as session:
        context = service.load_context(session, event)
    output = asyncio.run(service.generate(context, event)).output.model_dump(mode="json")
    item = output["claim_semantic_presentations"][0]
    output["claim_semantic_presentations"] = (
        []
        if kind == "missing"
        else [item, item if kind == "duplicate" else {**item, "claim_id": str(uuid4())}]
    )
    with pytest.raises(ValidationError):
        ContentGenerationOutput.model_validate(output)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_semantic_type", "EVENT"),
        ("source_semantic_state", "PLANNED"),
        ("presented_semantic_type", "EVENT"),
        ("presented_semantic_state", "PLANNED"),
    ],
)
def test_semantic_violation_uses_existing_invalid_response_fallback(field, value):
    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    service = _service(ai)
    with factory() as session:
        context = service.load_context(session, event)
    claim = context.brief.claims[0]
    ai.mutate = {
        "claim_semantic_presentations": [
            {**presentation(claim.claim_id, claim.semantics.model_dump(mode="json")), field: value}
        ]
    }
    result = asyncio.run(service.generate(context, event))
    assert result.routed.attempts[0].failure_reason.value == "INVALID_RESPONSE"
    assert result.routed.response.provider == "local-llama"


def test_semantics_copied_into_brief_payload_hash_and_operation_identity(monkeypatch):
    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    service = _service(ai)
    with factory() as session:
        context = service.load_context(session, event)
    claim = context.fact_sheet.claims[0]
    assert context.brief.claims[0].semantics == claim.semantics
    execution = asyncio.run(service.generate(context, event))
    with factory() as session, session.begin():
        result = service.persist(session, context=context, event=event, execution=execution)
    with factory() as session:
        payload = session.get(ContentVariant, result.content_variant_ids[0]).structured_payload
    assert (
        payload["claim_semantic_presentations"]
        == execution.output.model_dump(mode="json")["claim_semantic_presentations"]
    )
    changed = {
        **payload,
        "claim_semantic_presentations": [
            {**payload["claim_semantic_presentations"][0], "presented_semantic_state": "PLANNED"}
        ],
    }
    assert content_artifact_hash(changed) != content_artifact_hash(payload)
    monkeypatch.setattr(generation, "CLAIM_SEMANTICS_POLICY_VERSION", "claim-semantics-policy-v2")
    with factory() as session:
        assert service.load_context(session, event).semantic_key != context.semantic_key


def test_historical_fact_sheet_parses_but_cannot_generate_current_content():
    from news_ai_evidence import FactSheetGenerator

    factory, ai = _factory(), ContentAI()
    event, _, sheet_id = _seed(factory)
    with factory() as session, session.begin():
        row = session.get(FactSheet, sheet_id)
        row.claims_snapshot = [
            {k: v for k, v in row.claims_snapshot[0].items() if k != "semantics"}
        ]
        assert FactSheetGenerator.artifact_from_row(row).claims[0].semantics is None
    with factory() as session, pytest.raises(PermanentEventError):
        _service(ai).load_context(session, event)
    assert ai.calls == 0


def test_content_v2_not_reused_as_v3():
    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    old = _service(ai)
    old.style = old.style.model_copy(
        update={"methodology_version": "content-generation-methodology-v2"}
    )
    with factory() as session:
        previous = old.load_context(session, event)
    execution = asyncio.run(old.generate(previous, event))
    with factory() as session, session.begin():
        result = old.persist(session, context=previous, event=event, execution=execution)
        variant = session.get(ContentVariant, result.content_variant_ids[0])
        variant.structured_payload = {
            k: v
            for k, v in variant.structured_payload.items()
            if k != "claim_semantic_presentations"
        }
    current = _service(ai)
    with factory() as session:
        context = current.load_context(session, event)
        assert context.semantic_key != previous.semantic_key
        assert current.existing_result(session, context) is None


@pytest.mark.parametrize("state", ["ANNOUNCED", "PLANNED", "EXPECTED", "PREDICTED"])
def test_future_declaration_cannot_be_laundered_as_observed_through_generation(state):
    factory, ai = _factory(), ContentAI()
    event, _, sheet_id = _seed(factory)
    with factory() as session, session.begin():
        sheet = session.get(FactSheet, sheet_id)
        claim = dict(sheet.claims_snapshot[0])
        claim["semantics"] = {**claim["semantics"], "semantic_state": state}
        sheet.claims_snapshot = [claim]
    service = _service(ai)
    with factory() as session:
        context = service.load_context(session, event)
    claim = context.brief.claims[0]
    ai.mutate = {
        "claim_semantic_presentations": [
            {
                **presentation(claim.claim_id, claim.semantics.model_dump(mode="json")),
                "presented_semantic_state": "OBSERVED",
            }
        ]
    }
    result = asyncio.run(service.generate(context, event))
    assert result.routed.attempts[0].failure_reason.value == "INVALID_RESPONSE"
    assert result.output.claim_semantic_presentations[0].presented_semantic_state == state
