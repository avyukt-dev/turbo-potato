"""Response-validator enforcement and versioned operation identity."""

import asyncio

import news_ai_content.generation as generation
import pytest
from unit.content.test_content_generation import ContentAI, _factory, _seed, _service


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_status", "SUPPORTED"),
        ("source_fact_check_label", "TRUE"),
        ("assertion_strength", "HIGH"),
        ("frame", "DIRECT"),
    ],
)
def test_generation_invalid_response_path(field, value):
    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    service = _service(ai)
    with factory() as session:
        context = service.load_context(session, event)
    claim = context.brief.claims[0]
    presentation = {
        "claim_id": str(claim.claim_id),
        "source_status": claim.status.value,
        "source_fact_check_label": claim.label.value,
        "assertion_strength": "MEDIUM",
        "frame": "QUALIFIED",
    }
    ai.mutate = {"claim_presentations": [{**presentation, field: value}]}
    result = asyncio.run(service.generate(context, event))
    assert result.routed.response.provider == "local-llama"
    assert result.routed.attempts[0].failure_reason.value == "INVALID_RESPONSE"
    assert result.output.claim_presentations[0].source_status == claim.status


def test_certainty_policy_version_changes_generation_identity(monkeypatch):
    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    service = _service(ai)
    with factory() as session:
        before = service.load_context(session, event)
    monkeypatch.setattr(generation, "CERTAINTY_POLICY_VERSION", "certainty-policy-v2")
    with factory() as session:
        after = service.load_context(session, event)
    assert before.semantic_key != after.semantic_key


def test_v1_artifact_is_not_reused_as_v2():
    from news_ai_database import ContentVariant

    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    old = _service(ai)
    old.style = old.style.model_copy(
        update={"methodology_version": "content-generation-methodology-v1"}
    )
    with factory() as session:
        before = old.load_context(session, event)
    execution = asyncio.run(old.generate(before, event))
    with factory() as session, session.begin():
        first = old.persist(session, context=before, event=event, execution=execution)
        variant = session.get(ContentVariant, first.content_variant_ids[0])
        variant.structured_payload = {
            key: value
            for key, value in variant.structured_payload.items()
            if key != "claim_presentations"
        }
    current = _service(ai)
    with factory() as session:
        after = current.load_context(session, event)
        assert current.existing_result(session, after) is None
    assert before.semantic_key != after.semantic_key
