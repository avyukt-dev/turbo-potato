"""Deterministic graph and span regressions, without AI or transport."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from helpers.claim_semantics import SEMANTICS, presentation
from news_ai_content import ContentGenerationOutput
from news_ai_evidence import FactSheetArtifact
from news_ai_quality.semantic import (
    SemanticFindingCategory as Category,
)
from news_ai_quality.semantic import (
    SemanticFindingCode as Code,
)
from news_ai_quality.semantic import (
    SemanticValidationReport,
    SemanticValidator,
    fabricated_quotes,
)
from pydantic import ValidationError


@pytest.fixture
def artifacts():
    story, sheet, claim, evidence, source, check = (uuid4() for _ in range(6))
    fact_sheet = FactSheetArtifact.model_validate(
        {
            "fact_sheet_id": sheet,
            "story_id": story,
            "version": 1,
            "headline": "Record",
            "summary": "Not a source of quotes.",
            "risk_level": "LOW",
            "created_at": datetime.now(UTC),
            "claims": [
                {
                    "claim_id": claim,
                    "story_id": story,
                    "claim_text": "The café recorded two metres.",
                    "claim_type": "MEASUREMENT",
                    "semantics": SEMANTICS,
                    "status": "PARTIALLY_SUPPORTED",
                    "risk_level": "LOW",
                    "evidence_ids": [evidence],
                }
            ],
            "evidence": [
                {
                    "evidence_id": evidence,
                    "claim_id": claim,
                    "source_id": source,
                    "relation": "DIRECT_SUPPORT",
                    "excerpt": "The café recorded two metres. The register is preliminary.",
                }
            ],
            "sources": [{"source_id": source, "name": "Register", "source_level": 1}],
            "fact_checks": [
                {
                    "fact_check_id": check,
                    "story_id": story,
                    "claim_id": claim,
                    "label": "PARTIALLY_TRUE",
                    "summary": "Preliminary",
                    "review_required": True,
                    "review_state": "NOT_READY",
                    "supporting_evidence_ids": [evidence],
                }
            ],
        }
    )
    content = ContentGenerationOutput.model_validate(
        {
            "story_id": story,
            "fact_sheet_id": sheet,
            "fact_sheet_version": 1,
            "platform": "INSTAGRAM",
            "format": "CAROUSEL",
            "language": "en",
            "title": "Register",
            "caption": "Preliminary",
            "claim_ids_used": [claim],
            "claim_presentations": [
                {
                    "claim_id": claim,
                    "source_status": "PARTIALLY_SUPPORTED",
                    "source_fact_check_label": "PARTIALLY_TRUE",
                    "assertion_strength": "MEDIUM",
                    "frame": "QUALIFIED",
                }
            ],
            "claim_semantic_presentations": [presentation(claim)],
            "slides": [
                {
                    "position": 1,
                    "heading": "Measurement",
                    "body": "A preliminary record.",
                    "claim_ids": [claim],
                },
                {"position": 2, "heading": "Limits", "body": "Not final.", "claim_ids": [claim]},
            ],
        }
    )
    return fact_sheet, content, (source,)


def validate(artifacts):
    return SemanticValidator().validate(artifacts[0], artifacts[1], source_ids_used=artifacts[2])


def quoted(content, text, *, field="body"):
    slide = content.slides[0].model_copy(update={field: text})
    return content.model_copy(update={"slides": (slide, content.slides[1])})


def test_valid_graph_and_no_independence_inference(artifacts):
    report = validate(artifacts)
    assert report.passed and not report.findings
    assert report.check_counts.dependencies == 0
    assert report.model_dump_json() == validate(artifacts).model_dump_json()
    assert "independence" not in report.model_dump_json()


def test_repeated_source_id_with_distinct_documents_is_valid(artifacts):
    sheet, content, source_ids = artifacts
    source = sheet.sources[0]
    first = source.model_copy(
        update={"url": "https://publisher.example/article-a", "language": "en"}
    )
    second = source.model_copy(
        update={
            "url": "https://publisher.example/article-b",
            "language": "hi",
            "published_at": datetime(2026, 1, 1, tzinfo=UTC),
            "retrieved_at": datetime(2026, 1, 2, tzinfo=UTC),
        }
    )
    sheet = sheet.model_copy(update={"sources": (first, second)})
    report = validate((sheet, content, source_ids))
    assert report.passed
    assert not report.findings  # Evidence and content still resolve this source ID.
    assert "independence" not in report.model_dump_json()
    assert report.model_dump_json() == validate((sheet, content, source_ids)).model_dump_json()


def test_duplicate_source_document_snapshot_is_an_error(artifacts):
    sheet, content, sources = artifacts
    source = sheet.sources[0].model_copy(update={"url": "https://publisher.example/article-a"})
    sheet = sheet.model_copy(update={"sources": (source, source)})
    report = validate((sheet, content, sources))
    assert not report.passed
    assert [f.code for f in report.findings] == [Code.DUPLICATE_REFERENCE]


def test_missing_optional_publisher_does_not_hide_later_identity_conflict(artifacts):
    sheet, content, sources = artifacts
    snapshots = tuple(
        sheet.sources[0].model_copy(
            update={"url": f"https://publisher.example/{i}", "publisher": publisher}
        )
        for i, publisher in enumerate((None, "Register", "Different publisher"))
    )
    report = validate((sheet.model_copy(update={"sources": snapshots}), content, sources))
    assert [f.code for f in report.findings] == [Code.SOURCE_IDENTITY_CONFLICT]


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "Different canonical name"),
        ("source_level", 4),
        ("publisher", "Different publisher"),
    ],
)
def test_same_source_id_cannot_conflict_on_source_identity(artifacts, field, value):
    sheet, content, sources = artifacts
    first = sheet.sources[0].model_copy(
        update={"url": "https://publisher.example/article-a", "publisher": "Register"}
    )
    second = first.model_copy(update={"url": "https://publisher.example/article-b", field: value})
    report = validate((sheet.model_copy(update={"sources": (first, second)}), content, sources))
    assert [f.code for f in report.findings] == [Code.SOURCE_IDENTITY_CONFLICT]


def test_same_evidence_can_have_distinct_claim_specific_relations(artifacts):
    sheet, content, sources = artifacts
    original = sheet.claims[0]
    other = original.model_copy(
        update={
            "claim_id": uuid4(),
            "claim_text": "Another proposition.",
            "contradictory_evidence_ids": original.evidence_ids,
        }
    )
    shared = sheet.evidence[0].model_copy(
        update={"claim_id": other.claim_id, "relation": "CONTRADICTS"}
    )
    sheet = sheet.model_copy(
        update={"claims": (original, other), "evidence": (*sheet.evidence, shared)}
    )
    assert validate((sheet, content, sources)).passed


def test_timeline_only_checks_explicit_supported_references(artifacts):
    sheet, content, sources = artifacts
    sheet = sheet.model_copy(
        update={"timeline": ({"description": "Event", "claim_ids": [str(uuid4())]},)}
    )
    report = validate((sheet, content, sources))
    assert Code.DANGLING_REFERENCE in {f.code for f in report.findings}


def test_word_fragment_is_not_a_quote_span(artifacts):
    sheet, content, sources = artifacts
    assert not validate((sheet, quoted(content, '"met"'), sources)).passed


def test_same_evidence_identity_cannot_change_its_reviewed_material(artifacts):
    sheet, content, sources = artifacts
    other = sheet.claims[0].model_copy(update={"claim_id": uuid4()})
    changed = sheet.evidence[0].model_copy(
        update={"claim_id": other.claim_id, "excerpt": "Different material."}
    )
    sheet = sheet.model_copy(
        update={"claims": (*sheet.claims, other), "evidence": (*sheet.evidence, changed)}
    )
    assert Code.EVIDENCE_IDENTITY_CONFLICT in {
        f.code for f in validate((sheet, content, sources)).findings
    }


@pytest.mark.parametrize(
    "text",
    [
        '"The café recorded two metres."',
        "“The café recorded two metres.”",
        "«The café recorded two metres.»",
        "„The café recorded two metres.“",
        "“The cafe\u0301   recorded\n two metres.”",
        '"The register is preliminary."',
    ],
)
def test_exact_mechanical_quotes_and_evidence_preference(artifacts, text):
    sheet, content, sources = artifacts
    report = validate((sheet, quoted(content, text), sources))
    assert report.passed
    assert report.quote_matches[0].evidence_id == sheet.evidence[0].evidence_id
    assert report.quote_matches[0].source_id == sources[0]


def test_claim_quote_without_evidence_excerpt(artifacts):
    sheet, content, sources = artifacts
    sheet = sheet.model_copy(
        update={"evidence": (sheet.evidence[0].model_copy(update={"excerpt": None}),)}
    )
    report = validate((sheet, quoted(content, '"The café recorded two metres."'), sources))
    assert report.passed
    assert report.quote_matches[0].evidence_id is None


@pytest.mark.parametrize(
    "text",
    [
        '"The café measured two metres."',
        '"the café recorded two metres."',
        '"Not a source of quotes."',
    ],
)
def test_paraphrase_case_change_and_summary_only_quote_fail(artifacts, text):
    sheet, content, sources = artifacts
    report = validate((sheet, quoted(content, text), sources))
    assert not report.passed
    assert report.findings[0].code == Code.UNMATCHED_QUOTE


def test_unrelated_claim_cannot_launder_slide_quote(artifacts):
    sheet, content, sources = artifacts
    other = sheet.claims[0].model_copy(
        update={
            "claim_id": uuid4(),
            "claim_text": "The unrelated witness arrived.",
            "evidence_ids": (),
        }
    )
    sheet = sheet.model_copy(update={"claims": (*sheet.claims, other)})
    report = validate((sheet, quoted(content, '"The unrelated witness arrived."'), sources))
    assert not report.passed
    assert report.findings[0].code == Code.UNMATCHED_QUOTE


def test_unrelated_evidence_cannot_launder_slide_quote(artifacts):
    sheet, content, sources = artifacts
    other_id, evidence_id = uuid4(), uuid4()
    other = sheet.claims[0].model_copy(
        update={
            "claim_id": other_id,
            "claim_text": "Another record.",
            "evidence_ids": (evidence_id,),
        }
    )
    evidence = sheet.evidence[0].model_copy(
        update={
            "evidence_id": evidence_id,
            "claim_id": other_id,
            "excerpt": "The unrelated witness arrived.",
        }
    )
    sheet = sheet.model_copy(
        update={"claims": (*sheet.claims, other), "evidence": (*sheet.evidence, evidence)}
    )
    report = validate((sheet, quoted(content, '"The unrelated witness arrived."'), sources))
    assert not report.passed
    assert {f.code for f in report.findings} == {Code.UNMATCHED_QUOTE}


def test_title_caption_use_artifact_scope(artifacts):
    sheet, content, sources = artifacts
    content = content.model_copy(
        update={
            "title": '"The café recorded two metres."',
            "caption": "“The register is preliminary.”",
        }
    )
    assert validate((sheet, content, sources)).passed


def test_ordinary_apostrophes_and_contractions_are_not_quotes(artifacts):
    sheet, content, sources = artifacts
    report = validate(
        (
            sheet,
            quoted(content, "It's the witness’s record; 'single quotation' is ambiguous."),
            sources,
        )
    )
    assert report.passed and report.check_counts.quotations == 0


def test_fabricated_quote_emitted_once_and_reports_stable(artifacts):
    sheet, content, sources = artifacts
    content = quoted(content, '"Invented statement" and "Invented statement"')
    report = validate((sheet, content, sources))
    assert fabricated_quotes(content, report) == ("Invented statement",)
    assert report.model_dump_json() == validate((sheet, content, sources)).model_dump_json()
    assert len({f.model_dump_json() for f in report.findings}) == len(report.findings)


@pytest.mark.parametrize(
    "kind", ["claim", "evidence", "source", "fact_check_claim", "fact_check_evidence"]
)
def test_dangling_references(artifacts, kind):
    sheet, content, sources = artifacts
    unknown = uuid4()
    if kind == "claim":
        content = content.model_copy(update={"claim_ids_used": (unknown,)})
    elif kind == "evidence":
        sheet = sheet.model_copy(
            update={"claims": (sheet.claims[0].model_copy(update={"evidence_ids": (unknown,)}),)}
        )
    elif kind == "source":
        sheet = sheet.model_copy(update={"sources": ()})
    else:
        field = "claim_id" if kind == "fact_check_claim" else "supporting_evidence_ids"
        value = unknown if field == "claim_id" else (unknown,)
        sheet = sheet.model_copy(
            update={"fact_checks": (sheet.fact_checks[0].model_copy(update={field: value}),)}
        )
    assert Code.DANGLING_REFERENCE in {f.code for f in validate((sheet, content, sources)).findings}


def test_evidence_wrong_claim_owner(artifacts):
    sheet, content, sources = artifacts
    sheet = sheet.model_copy(
        update={"evidence": (sheet.evidence[0].model_copy(update={"claim_id": uuid4()}),)}
    )
    assert Code.WRONG_REFERENCE_OWNER in {
        f.code for f in validate((sheet, content, sources)).findings
    }


@pytest.mark.parametrize("field", ["evidence_ids", "contradictory_evidence_ids"])
def test_duplicate_evidence_ids(artifacts, field):
    sheet, content, sources = artifacts
    identity = sheet.evidence[0].evidence_id
    sheet = sheet.model_copy(
        update={"claims": (sheet.claims[0].model_copy(update={field: (identity, identity)}),)}
    )
    assert Code.DUPLICATE_REFERENCE in {
        f.code for f in validate((sheet, content, sources)).findings
    }


def test_duplicate_source_and_claim_references(artifacts):
    sheet, content, sources = artifacts
    content = content.model_copy(update={"claim_ids_used": content.claim_ids_used * 2})
    report = validate((sheet, content, sources * 2))
    assert len([f for f in report.findings if f.code == Code.DUPLICATE_REFERENCE]) == 2


def test_contradiction_and_support_roles_are_not_interchangeable(artifacts):
    sheet, content, sources = artifacts
    sheet = sheet.model_copy(
        update={"evidence": (sheet.evidence[0].model_copy(update={"relation": "CONTRADICTS"}),)}
    )
    report = validate((sheet, content, sources))
    assert Code.RELATION_ROLE_MISMATCH in {f.code for f in report.findings}
    assert any(f.category == Category.STANCE_CONSISTENCY for f in report.findings)


def test_valid_contradictory_graph_is_not_factual_failure(artifacts):
    sheet, content, sources = artifacts
    eid = sheet.evidence[0].evidence_id
    sheet = sheet.model_copy(
        update={
            "evidence": (sheet.evidence[0].model_copy(update={"relation": "CONTRADICTS"}),),
            "claims": (sheet.claims[0].model_copy(update={"contradictory_evidence_ids": (eid,)}),),
            "fact_checks": (
                sheet.fact_checks[0].model_copy(
                    update={"supporting_evidence_ids": (), "contradicting_evidence_ids": (eid,)}
                ),
            ),
        }
    )
    assert validate((sheet, content, sources)).passed


def test_invalid_closed_relation_is_categorical_error(artifacts):
    sheet, content, sources = artifacts
    sheet = sheet.model_copy(
        update={"evidence": (sheet.evidence[0].model_copy(update={"relation": "AUTOMATIC_TRUTH"}),)}
    )
    assert Code.INVALID_EVIDENCE_RELATION in {
        f.code for f in validate((sheet, content, sources)).findings
    }


@pytest.mark.parametrize("end_offset,passes", [(1, True), (0, True), (-1, False)])
def test_explicit_temporal_ranges(artifacts, end_offset, passes):
    sheet, content, sources = artifacts
    start = datetime(2026, 1, 1, tzinfo=UTC)
    claim = sheet.claims[0].model_copy(
        update={"temporal_start": start, "temporal_end": start + timedelta(days=end_offset)}
    )
    sheet = sheet.model_copy(update={"claims": (claim,)})
    assert validate((sheet, content, sources)).passed == passes


def test_unknown_optional_timeline_and_future_publication_are_not_guessed(artifacts):
    sheet, content, sources = artifacts
    sheet = sheet.model_copy(
        update={
            "timeline": ({"arbitrary_historical_metadata": "not chronology"},),
            "evidence": (
                sheet.evidence[0].model_copy(
                    update={
                        "published_at": datetime(2030, 1, 1, tzinfo=UTC),
                        "retrieved_at": datetime(2026, 1, 1, tzinfo=UTC),
                    }
                ),
            ),
        }
    )
    assert validate((sheet, content, sources)).passed


def test_report_cannot_claim_pass_with_errors_or_duplicate_findings(artifacts):
    sheet, content, sources = artifacts
    report = validate((sheet, quoted(content, '"Fabricated"'), sources))
    with pytest.raises(ValidationError):
        SemanticValidationReport.model_validate({**report.model_dump(), "passed": True})
    with pytest.raises(ValidationError):
        SemanticValidationReport.model_validate(
            {**report.model_dump(), "findings": report.findings * 2}
        )


def test_report_and_findings_are_frozen_and_machine_codes_are_closed(artifacts):
    sheet, content, sources = artifacts
    report = validate((sheet, quoted(content, '"Fabricated"'), sources))
    with pytest.raises(ValidationError):
        report.passed = True
    finding = report.findings[0]
    with pytest.raises(ValidationError):
        finding.code = "PROVIDER_OVERRIDE"
    with pytest.raises(ValidationError):
        type(finding).model_validate({**finding.model_dump(), "code": "PROVIDER_OVERRIDE"})
    with pytest.raises(ValidationError):
        type(finding).model_validate({**finding.model_dump(), "raw_provider_response": "secret"})


def test_unknown_metadata_does_not_leak_into_semantic_report(artifacts):
    sheet, content, sources = artifacts
    sentinel = "SEMANTIC_SECRET_SENTINEL_123"
    sheet = sheet.model_copy(update={"timeline": ({"api_key": sentinel},)})
    report = validate((sheet, content, sources))
    assert sentinel not in report.model_dump_json()
