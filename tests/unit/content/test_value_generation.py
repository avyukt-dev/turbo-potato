"""Value coverage and occurrence binding use the official router invalid-response path."""

import asyncio
from uuid import UUID, uuid4

import pytest
from news_ai_content import content_artifact_hash
from news_ai_database import ContentDraft, ContentVariant, FactSheet
from news_ai_domain.values import ClaimValues, anchors_for_claim
from news_ai_events import PermanentEventError
from pydantic import ValidationError
from sqlalchemy import select
from unit.content.test_content_generation import ContentAI, _factory, _seed, _service
from unit.domain.test_value_policy import currency, measurement, numeric, temporal


def attach_values(factory, *, quality=False, candidate=None):
    with factory() as session, session.begin():
        sheet = session.scalar(select(FactSheet))
        claim = dict(sheet.claims_snapshot[0])
        candidate = candidate or measurement("5 km", "5")
        block = ClaimValues(
            policy_version="value-integrity-policy-v1",
            anchors=anchors_for_claim(UUID(claim["claim_id"]), (candidate,)),
        )
        claim["claim_text"] = f"The source reports {candidate.source_text}."
        claim["values"] = block.model_dump(mode="json")
        sheet.claims_snapshot = [claim]
        if quality:
            draft = session.scalar(select(ContentDraft))
            brief = dict(draft.editorial_brief_snapshot)
            brief_claim = dict(brief["claims"][0])
            brief_claim.update(text=claim["claim_text"], values=claim["values"])
            brief["claims"] = [brief_claim]
            draft.editorial_brief_snapshot = brief
            variant = session.scalar(select(ContentVariant))
            payload = dict(variant.structured_payload)
            payload["claim_value_presentations"] = [record(claim)]
            variant.structured_payload = payload
        return claim


def record(claim, occurrences=()):
    return {
        "claim_id": claim["claim_id"],
        "anchor_id": claim["values"]["anchors"][0]["anchor_id"],
        "occurrences": list(occurrences),
    }


def occurrence(text="5 km", amount="5", unit="km", path="caption", transformation="EXACT"):
    return {
        "artifact_path": path,
        "rendered_text": text,
        "presented_value": measurement(text, amount, unit).value.model_dump(mode="json"),
        "transformation": transformation,
    }


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "duplicate",
        "unknown",
        "owner",
        "path",
        "text",
        "embedded",
        "magnitude",
        "unit",
        "conversion",
    ],
)
def test_bad_value_metadata_uses_existing_fallback(mode):
    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    claim = attach_values(factory)
    item = record(claim, [occurrence()])
    caption = "A convoy travelled 5 km, 6 km, 5 m and 5000 m."
    if mode == "missing":
        records = []
    elif mode == "duplicate":
        records = [item, item]
    else:
        records = [item]
        if mode == "unknown":
            item["anchor_id"] = str(uuid4())
        elif mode == "owner":
            item["claim_id"] = str(uuid4())
        elif mode == "path":
            item["occurrences"][0]["artifact_path"] = "slides[19].body"
        elif mode == "text":
            item["occurrences"][0]["rendered_text"] = "absent source"
        elif mode == "magnitude":
            item["occurrences"] = [occurrence("6 km", "6")]
        elif mode == "unit":
            item["occurrences"] = [
                occurrence("5 m", "5", "m", transformation="EXACT_UNIT_CONVERSION")
            ]
        elif mode == "conversion":
            item["occurrences"] = [
                occurrence("5000 m", "5000", "m", transformation="FORMAT_EQUIVALENT")
            ]
        elif mode == "embedded":
            caption = "The convoy travelled 15 km."
            item["occurrences"] = [occurrence("5 km", "5")]
    ai.mutate = {
        "caption": caption,
        "claim_value_presentations": records,
    }
    service = _service(ai)
    service.router.registry.get("local-llama").mutate = {}
    with factory() as session:
        context = service.load_context(session, event)
    result = asyncio.run(service.generate(context, event))
    assert result.routed.attempts[0].failure_reason.value == "INVALID_RESPONSE"
    assert result.routed.response.provider == "local-llama"


def test_valid_exact_conversion_persists_and_hashes_reviewed_metadata():
    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    claim = attach_values(factory)
    item = record(
        claim, [occurrence("5000 m", "5000", "m", transformation="EXACT_UNIT_CONVERSION")]
    )
    ai.mutate = {"caption": "The convoy travelled 5000 m.", "claim_value_presentations": [item]}
    service = _service(ai)
    with factory() as session:
        context = service.load_context(session, event)
    execution = asyncio.run(service.generate(context, event))
    assert execution.routed.response.provider == ai.provider_id
    with factory() as session, session.begin():
        result = service.persist(session, context=context, event=event, execution=execution)
    with factory() as session:
        payload = session.get(ContentVariant, result.content_variant_ids[0]).structured_payload
        assert payload["claim_value_presentations"] == [item]
        assert content_artifact_hash(payload) != content_artifact_hash(
            {**payload, "claim_value_presentations": []}
        )
        assert (
            service.existing_result(session, context).content_variant_ids
            == result.content_variant_ids
        )


def test_historical_values_are_readable_but_not_current_generation():
    factory, ai = _factory(), ContentAI()
    event, _, sheet_id = _seed(factory)
    with factory() as session, session.begin():
        sheet = session.get(FactSheet, sheet_id)
        sheet.claims_snapshot = [
            {k: v for k, v in sheet.claims_snapshot[0].items() if k != "values"}
        ]
    with factory() as session, pytest.raises(PermanentEventError):
        _service(ai).load_context(session, event)
    assert ai.calls == 0


def test_value_presentations_required_even_when_empty():
    from news_ai_content import ContentGenerationOutput

    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    service = _service(ai)
    with factory() as session:
        context = service.load_context(session, event)
    output = asyncio.run(service.generate(context, event)).output.model_dump(mode="json")
    assert output["claim_value_presentations"] == []
    del output["claim_value_presentations"]
    with pytest.raises(ValidationError):
        ContentGenerationOutput.model_validate(output)


def test_value_policy_changes_content_operation_identity(monkeypatch):
    import news_ai_content.generation as generation

    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    service = _service(ai)
    with factory() as session:
        first = service.load_context(session, event)
    monkeypatch.setattr(generation, "VALUE_INTEGRITY_POLICY_VERSION", "value-integrity-policy-v2")
    with factory() as session:
        assert service.load_context(session, event).semantic_key != first.semantic_key


def test_content_v3_result_is_not_reused_by_v4():
    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    old = _service(ai)
    old.style = old.style.model_copy(
        update={"methodology_version": "content-generation-methodology-v3"}
    )
    with factory() as session:
        previous = old.load_context(session, event)
    execution = asyncio.run(old.generate(previous, event))
    with factory() as session, session.begin():
        result = old.persist(session, context=previous, event=event, execution=execution)
        variant = session.get(ContentVariant, result.content_variant_ids[0])
        variant.structured_payload = {
            k: v for k, v in variant.structured_payload.items() if k != "claim_value_presentations"
        }
    current = _service(ai)
    with factory() as session:
        context = current.load_context(session, event)
        assert context.semantic_key != previous.semantic_key
        assert current.existing_result(session, context) is None


@pytest.mark.parametrize(
    "a,b",
    [
        (numeric("1.2 billion", "1200000000"), numeric("1.2 million", "1200000")),
        (
            numeric("10%", "10", "PERCENT"),
            numeric("10 percentage points", "10", "PERCENTAGE_POINT"),
        ),
    ],
)
def test_multi_claim_values_cannot_launder_anchor_or_slide_ownership(a, b):
    from types import SimpleNamespace

    from news_ai_content.values import ClaimValuePresentation, presentation_errors

    first, second = uuid4(), uuid4()
    blocks = {
        identity: ClaimValues(
            policy_version="value-integrity-policy-v1",
            anchors=anchors_for_claim(identity, (candidate,)),
        )
        for identity, candidate in ((first, a), (second, b))
    }
    assert blocks[first].anchors[0].anchor_id != blocks[second].anchors[0].anchor_id
    records = tuple(
        ClaimValuePresentation(
            claim_id=identity, anchor_id=block.anchors[0].anchor_id, occurrences=()
        )
        for identity, block in blocks.items()
    )
    content = SimpleNamespace(
        claim_ids_used=(first, second),
        slides=(
            SimpleNamespace(claim_ids=(first, second), heading=a.source_text, body=b.source_text),
        ),
        title="5 km",
        caption="5 km",
        claim_value_presentations=records,
    )
    assert not presentation_errors(content, blocks)
    content.slides[0].claim_ids = (first,)
    content.claim_value_presentations = (
        records[0],
        ClaimValuePresentation.model_validate(
            {
                **records[1].model_dump(mode="json"),
                "occurrences": [
                    {
                        "artifact_path": "slides[0].body",
                        "rendered_text": b.source_text,
                        "presented_value": b.value.model_dump(mode="json"),
                        "transformation": "EXACT",
                    }
                ],
            }
        ),
    )
    assert "OCCURRENCE_SCOPE_MISMATCH" in {
        code for code, _, _ in presentation_errors(content, blocks)
    }
    content.claim_value_presentations = (
        records[0],
        records[1].model_copy(update={"claim_id": first}),
    )
    assert "WRONG_ANCHOR_OWNER" in {code for code, _, _ in presentation_errors(content, blocks)}
    content.claim_ids_used = (first,)
    content.claim_value_presentations = records
    assert "UNKNOWN_ANCHOR" in {code for code, _, _ in presentation_errors(content, blocks)}


@pytest.mark.parametrize(
    "source,target",
    [
        (
            numeric("10%", "10", "PERCENT"),
            numeric("10 percentage points", "10", "PERCENTAGE_POINT"),
        ),
        (currency("USD 100", "100"), currency("EUR 100", "100", "EUR")),
        (temporal("2026-09-14", "2026-09-14"), temporal("2026-09-15", "2026-09-15")),
        (temporal("September 2026", "2026-09", "MONTH"), temporal("2026-09-01", "2026-09-01")),
        (numeric("1.2 billion", "1200000000"), numeric("1.2 million", "1200000")),
    ],
)
def test_value_semantic_mismatch_is_invalid_generation_response(source, target):
    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    claim = attach_values(factory, candidate=source)
    ai.mutate = {
        "caption": f"The source reports {target.source_text}.",
        "claim_value_presentations": [
            record(
                claim,
                [
                    {
                        "artifact_path": "caption",
                        "rendered_text": target.source_text,
                        "presented_value": target.value.model_dump(mode="json"),
                        "transformation": "FORMAT_EQUIVALENT",
                    }
                ],
            )
        ],
    }
    service = _service(ai)
    service.router.registry.get("local-llama").mutate = {}
    with factory() as session:
        context = service.load_context(session, event)
    result = asyncio.run(service.generate(context, event))
    assert result.routed.attempts[0].failure_reason.value == "INVALID_RESPONSE"
    assert result.routed.response.provider == "local-llama"


def test_exact_scale_format_equivalence_is_accepted_at_generation_boundary():
    source, target = numeric("1.2 billion", "1200000000"), numeric("1,200 million", "1200000000")
    factory, ai = _factory(), ContentAI()
    event, _, _ = _seed(factory)
    claim = attach_values(factory, candidate=source)
    ai.mutate = {
        "caption": f"The source reports {target.source_text}.",
        "claim_value_presentations": [
            record(
                claim,
                [
                    {
                        "artifact_path": "caption",
                        "rendered_text": target.source_text,
                        "presented_value": target.value.model_dump(mode="json"),
                        "transformation": "FORMAT_EQUIVALENT",
                    }
                ],
            )
        ],
    }
    service = _service(ai)
    with factory() as session:
        context = service.load_context(session, event)
    assert context.brief.claims[0].values == context.fact_sheet.claims[0].values
    assert asyncio.run(service.generate(context, event)).routed.response.provider == ai.provider_id
