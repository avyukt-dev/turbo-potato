"""Quality remains application-owned when AI and deterministic checks disagree."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from news_ai_database import (
    ContentDraft,
    ContentQualityCheck,
    ContentVariant,
    EventOutbox,
    FactSheet,
)
from news_ai_domain import ReviewState
from news_ai_events.outbox import envelope_from_outbox
from news_ai_quality import SemanticFindingCode, SemanticValidationReport
from sqlalchemy import func, select
from unit.quality.test_quality_gate import QualityAI, _factory, _run, _seed, _service


def check_semantic_error(factory):
    event, draft_id, variant_id = _seed(factory)
    with factory() as session, session.begin():
        sheet = session.scalar(select(FactSheet))
        entry = dict(sheet.evidence_snapshot[0])
        entry["relation"] = "CONTRADICTS"
        sheet.evidence_snapshot = [entry]
    ai = QualityAI()
    service = _service(ai)
    context, result = _run(factory, service, event)
    assert ai.calls == 1  # Still obtain the full AI report for diagnosis.
    assert not result.passed
    report = context.variants[0].semantic_report
    assert not report.passed
    assert SemanticFindingCode.RELATION_ROLE_MISMATCH in {f.code for f in report.findings}
    with factory() as session:
        check = session.scalar(select(ContentQualityCheck))
        assert check.semantic_validation_passed is False
        assert check.semantic_methodology_version == "semantic-validator-v1"
        assert SemanticValidationReport.model_validate(check.semantic_findings) == report
        assert check.factual_accuracy_passed is True  # AI cannot override semantic failure.
        assert session.get(ContentVariant, variant_id).review_state == ReviewState.NOT_READY
        assert session.get(ContentDraft, draft_id).review_state == ReviewState.NOT_READY
        emitted = envelope_from_outbox(session.scalar(select(EventOutbox)))
        assert emitted.payload["passed"] is False
        assert emitted.payload["fact_check_passed"] is False
        assert emitted.causation_id == event.event_id
        assert emitted.correlation_id == event.correlation_id
    _, replay = _run(factory, service, event)
    assert not replay.created
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContentQualityCheck)) == 1


def test_semantic_error_overrides_ai_and_persists_idempotently():
    check_semantic_error(_factory())


def test_warning_only_does_not_fail_otherwise_good_quality():
    factory = _factory()
    event, draft_id, variant_id = _seed(factory)
    with factory() as session, session.begin():
        sheet = session.scalar(select(FactSheet))
        claim = dict(sheet.claims_snapshot[0])
        claim["temporal_start"] = datetime(2026, 1, 1).isoformat()
        claim["temporal_end"] = datetime(2026, 1, 2, tzinfo=UTC).isoformat()
        sheet.claims_snapshot = [claim]
    context, result = _run(factory, _service(QualityAI()), event)
    assert result.passed
    assert context.variants[0].semantic_report.findings[0].severity == "WARNING"
    with factory() as session:
        assert session.get(ContentDraft, draft_id).review_state == ReviewState.READY_FOR_REVIEW
        assert session.get(ContentVariant, variant_id).review_state == ReviewState.READY_FOR_REVIEW


def test_semantic_methodology_version_invalidates_existing_result():
    factory = _factory()
    event, _, _ = _seed(factory)
    service = _service(QualityAI())
    before, first = _run(factory, service, event)
    service.semantic_validator.methodology_version = "semantic-validator-v2"
    after, second = _run(factory, service, event)
    assert first.created and second.created
    assert before.event_semantic_key != after.event_semantic_key
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ContentQualityCheck)) == 2


def test_historical_v3_result_is_not_reused_as_semantically_checked():
    factory = _factory()
    event, _, _ = _seed(factory)
    service = _service(QualityAI())
    _run(factory, service, event)
    with factory() as session, session.begin():
        check = session.scalar(select(ContentQualityCheck))
        check.methodology_version = "quality-gate-methodology-v3"
        check.semantic_key = f"legacy:{uuid4()}"
        check.semantic_validation_passed = None
        check.semantic_methodology_version = None
        check.semantic_findings = None
        outbox = session.scalar(select(EventOutbox))
        outbox.idempotency_key = f"legacy:{uuid4()}"
        old_id = check.id
    _, result = _run(factory, service, event)
    assert result.created
    with factory() as session:
        old = session.get(ContentQualityCheck, old_id)
        assert old.methodology_version == "quality-gate-methodology-v3"
        assert old.semantic_validation_passed is None
        assert old.semantic_methodology_version is None


def test_quote_findings_persist_and_compatibility_field_stays_available():
    factory = _factory()
    event, _, variant_id = _seed(factory)
    with factory() as session, session.begin():
        variant = session.get(ContentVariant, variant_id)
        variant.title = '"Invented quotation" and "Invented quotation"'
    _, result = _run(factory, _service(QualityAI()), event)
    assert not result.passed
    with factory() as session:
        check = session.scalar(select(ContentQualityCheck))
        assert check.fabricated_quotes == ["Invented quotation"]
        report = SemanticValidationReport.model_validate(check.semantic_findings)
        assert all(f.code == SemanticFindingCode.UNMATCHED_QUOTE for f in report.findings)


@pytest.mark.parametrize(
    "field,value", [("fact_check_id", str(uuid4())), ("status", "SUPPORTED"), ("label", "TRUE")]
)
def test_brief_references_and_categorical_copies_cannot_diverge(field, value):
    factory = _factory()
    event, draft_id, _ = _seed(factory)
    with factory() as session, session.begin():
        draft = session.get(ContentDraft, draft_id)
        brief = dict(draft.editorial_brief_snapshot)
        brief["claims"] = [{**brief["claims"][0], field: value}]
        draft.editorial_brief_snapshot = brief
    ai = QualityAI()
    context, result = _run(factory, _service(ai), event)
    assert ai.calls == 1
    assert not result.passed
    codes = {f.code for f in context.variants[0].semantic_report.findings}
    assert codes & {
        SemanticFindingCode.DANGLING_REFERENCE,
        SemanticFindingCode.BRIEF_FACTUAL_METADATA_MISMATCH,
    }
