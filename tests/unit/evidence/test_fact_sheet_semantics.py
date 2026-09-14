"""Snapshotting copies classified durable state, never inventing historical semantics."""

import pytest
from news_ai_database import Claim, FactSheet
from news_ai_events import PermanentEventError
from news_ai_evidence import FactSheetGenerator
from unit.evidence.test_fact_sheet_generator import _factory, _seed_verified_story


def test_current_fact_sheet_copies_exact_classification_and_identity_changes():
    factory = _factory()
    event, _, claim_ids, _ = _seed_verified_story(factory)
    generator = FactSheetGenerator()
    with factory() as session, session.begin():
        first = generator.generate(session, event)
        row = session.get(FactSheet, first.fact_sheet_id)
        old_snapshot = list(row.claims_snapshot)
        artifact = generator.artifact_from_row(row)
        claim = session.get(Claim, claim_ids[0])
        assert artifact.claims[0].semantics.semantic_type == claim.semantic_type
        assert artifact.claims[0].semantics.semantic_state == claim.semantic_state
        assert artifact.claims[0].semantics.ai_run_id == claim.semantic_ai_run_id
        status = claim.status
        claim.semantic_state = "ANNOUNCED"
    with factory() as session, session.begin():
        second = generator.generate(session, event)
        assert first.fact_sheet_id != second.fact_sheet_id
        assert session.get(Claim, claim_ids[0]).status == status
        assert session.get(FactSheet, first.fact_sheet_id).claims_snapshot == old_snapshot


def test_unclassified_claim_cannot_create_new_fact_sheet():
    factory = _factory()
    event, _, claim_ids, _ = _seed_verified_story(factory)
    with factory() as session, session.begin():
        claim = session.get(Claim, claim_ids[0])
        claim.semantic_type = claim.semantic_state = None
        claim.semantic_policy_version = claim.semantic_ai_run_id = None
    with (
        factory() as session,
        session.begin(),
        pytest.raises(PermanentEventError, match="CLAIM_SEMANTICS_MISSING"),
    ):
        FactSheetGenerator().generate(session, event)


def test_historical_snapshot_absence_is_readable_and_not_fabricated():
    factory = _factory()
    event, _, _, _ = _seed_verified_story(factory)
    with factory() as session, session.begin():
        result = FactSheetGenerator().generate(session, event)
        row = session.get(FactSheet, result.fact_sheet_id)
        row.claims_snapshot = [
            {key: value for key, value in item.items() if key != "semantics"}
            for item in row.claims_snapshot
        ]
        original = row.claims_snapshot
        assert all(
            claim.semantics is None for claim in FactSheetGenerator.artifact_from_row(row).claims
        )
        assert row.claims_snapshot == original
