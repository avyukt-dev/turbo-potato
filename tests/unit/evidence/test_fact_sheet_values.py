"""Snapshots copy current evaluated anchors without arithmetic or classification."""

import pytest
from news_ai_database import Claim, FactSheet
from news_ai_domain.values import anchors_for_claim
from news_ai_events import PermanentEventError
from news_ai_evidence import FactSheetGenerator
from unit.domain.test_value_policy import measurement
from unit.evidence.test_fact_sheet_generator import _factory, _seed_verified_story


def test_values_copy_exactly_and_change_new_fact_sheet_identity():
    factory = _factory()
    event, _, claims, _ = _seed_verified_story(factory)
    with factory() as session, session.begin():
        claim = session.get(Claim, claims[0])
        claim.claim_text = "The convoy travelled 5 km and 6 km."
        claim.value_anchors = [
            a.model_dump(mode="json")
            for a in anchors_for_claim(claim.id, (measurement("5 km", "5"),))
        ]
        result = FactSheetGenerator().generate(session, event)
        first = session.get(FactSheet, result.fact_sheet_id)
        snapshot = first.claims_snapshot
        values = next(
            c.values
            for c in FactSheetGenerator.artifact_from_row(first).claims
            if c.claim_id == claim.id
        )
        assert values.anchors[0].model_dump(mode="json") == claim.value_anchors[0]
        assert values.ai_run_id == claim.value_ai_run_id
        claim.value_anchors = [
            a.model_dump(mode="json")
            for a in anchors_for_claim(claim.id, (measurement("6 km", "6"),))
        ]
    with factory() as session, session.begin():
        newer = FactSheetGenerator().generate(session, event)
        assert newer.fact_sheet_id != result.fact_sheet_id
        assert session.get(FactSheet, result.fact_sheet_id).claims_snapshot == snapshot


def test_evaluated_empty_is_valid_and_missing_metadata_fails_closed():
    factory = _factory()
    event, _, claims, _ = _seed_verified_story(factory)
    with factory() as session, session.begin():
        result = FactSheetGenerator().generate(session, event)
        artifact = FactSheetGenerator.artifact_from_row(
            session.get(FactSheet, result.fact_sheet_id)
        )
        assert all(c.values.anchors == () for c in artifact.claims)
        claim = session.get(Claim, claims[0])
        claim.value_anchors = claim.value_policy_version = claim.value_ai_run_id = None
    with (
        factory() as session,
        session.begin(),
        pytest.raises(PermanentEventError, match="CLAIM_VALUES_MISSING"),
    ):
        FactSheetGenerator().generate(session, event)


def test_historical_fact_sheet_values_not_fabricated():
    factory = _factory()
    event, _, _, _ = _seed_verified_story(factory)
    with factory() as session, session.begin():
        result = FactSheetGenerator().generate(session, event)
        row = session.get(FactSheet, result.fact_sheet_id)
        row.claims_snapshot = [
            {k: v for k, v in c.items() if k != "values"} for c in row.claims_snapshot
        ]
        before = row.claims_snapshot
        assert all(c.values is None for c in FactSheetGenerator.artifact_from_row(row).claims)
        assert row.claims_snapshot == before
