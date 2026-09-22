from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from news_ai_content import (
    CONTENT_GENERATION_MAX_CLAIMS,
    AIInputProjectionError,
    project_content_generation_input,
    project_quality_assessment_input,
)


def _payload(claim_count: int = 6) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    brief_claims: list[dict[str, Any]] = []
    fact_checks: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []

    for index in range(claim_count):
        claim_id = f"claim-{index}"
        evidence_id = f"evidence-{index}"
        source_id = f"source-{index}"
        semantics = {
            "policy_version": "claim-semantics-policy-v1",
            "semantic_type": "QUANTITATIVE",
            "semantic_state": "OBSERVED",
        }
        values = {
            "policy_version": "value-integrity-policy-v1",
            "anchors": [
                {
                    "anchor_id": f"anchor-{index}",
                    "source_text": f"{index + 4}.82%",
                    "value": {
                        "kind": "PERCENT",
                        "quantity": {
                            "relation": "EXACT",
                            "amount": f"{index + 4}.82",
                            "upper": None,
                        },
                    },
                }
            ],
        }
        claims.append(
            {
                "claim_id": claim_id,
                "story_id": "story-1",
                "claim_text": f"Claim {index} measured {index + 4}.82%.",
                "claim_type": "MEASUREMENT",
                "semantics": deepcopy(semantics),
                "values": deepcopy(values),
                "status": "SUPPORTED",
                "confidence_score": 0.9,
                "importance_score": 0.8,
                "risk_level": "LOW",
                "sensitive_topics": [],
                "evidence_ids": [evidence_id],
                "contradictory_evidence_ids": [],
                "temporal_start": "2026-09-01T00:00:00Z",
                "temporal_end": None,
                "location_ids": [],
            }
        )
        brief_claims.append(
            {
                "claim_id": claim_id,
                "text": f"Claim {index} measured {index + 4}.82%.",
                "status": "SUPPORTED",
                "fact_check_id": f"check-{index}",
                "label": "TRUE",
                "semantics": deepcopy(semantics),
                "values": deepcopy(values),
                "confidence_score": 0.9,
                "evidence_ids": [evidence_id],
                "evidence_excerpts": [f"Evidence excerpt {index}."],
            }
        )
        fact_checks.append(
            {
                "fact_check_id": f"check-{index}",
                "story_id": "story-1",
                "claim_id": claim_id,
                "label": "TRUE",
                "confidence_score": 0.9,
                "summary": f"Fact-check summary {index}.",
                "supporting_evidence_ids": [evidence_id],
                "contradicting_evidence_ids": [],
                "review_required": True,
                "review_state": "NOT_READY",
            }
        )
        evidence.append(
            {
                "evidence_id": evidence_id,
                "claim_id": claim_id,
                "source_id": source_id,
                "article_id": f"article-{index}",
                "article_version_id": f"version-{index}",
                "title": f"Primary record {index}",
                "url": f"https://example.test/{index}",
                "published_at": "2026-09-01T00:00:00Z",
                "retrieved_at": "2026-09-02T00:00:00Z",
                "relation": "DIRECT_SUPPORT",
                "strength_score": 0.95,
                "excerpt": f"Evidence excerpt {index}.",
                "provenance_note": "operational provenance " + "p" * 1200,
                "content_hash": "a" * 64,
                "source_level": 1,
                "source_policy_basis": "primary-record",
                "lineage_status": "INDEPENDENT",
                "lineage_basis": "document-id",
                "independence_group": f"group-{index}",
                "originating_reference": f"ref-{index}",
                "assessment_ai_run_ids": [f"run-{index}"],
                "directness": "DIRECT",
                "origin_role": "ORIGINAL",
                "provenance_state": "DURABLE_VERSION_PRESERVED",
                "temporal_role": "CONTEMPORARY",
                "semantics_policy_version": "evidence-semantics-v1",
                "graph_relations": [
                    {
                        "relation_type": "REFERENCES",
                        "target_evidence_id": None,
                        "external_reference": f"document:{index}",
                        "basis": "explicit citation",
                        "policy_version": "evidence-graph-policy-v1",
                        "research_run_id": f"research-{index}",
                        "research_generation": 1,
                        "operational_blob": "g" * 1000,
                    }
                ],
            }
        )
        sources.append(
            {
                "source_id": source_id,
                "name": f"Source {index}",
                "source_level": 1,
                "url": f"https://example.test/source/{index}",
                "publisher": f"Publisher {index}",
                "published_at": "2026-09-01T00:00:00Z",
                "retrieved_at": "2026-09-02T00:00:00Z",
                "language": "en",
                "operational_blob": "s" * 1000,
            }
        )

    fact_sheet = {
        "fact_sheet_id": "sheet-1",
        "story_id": "story-1",
        "version": 3,
        "headline": "Inflation rose in August",
        "summary": "Verified measurements with preserved uncertainty.",
        "claims": claims,
        "fact_checks": fact_checks,
        "evidence": evidence,
        "sources": sources,
        "timeline": [{"at": "2026-09-01", "event": "Release"}],
        "entities": [{"name": "Reserve Bank"}],
        "locations": ["India"],
        "context": ["Monthly inflation release"],
        "counterclaims": [],
        "unresolved_questions": ["Whether policy will change remains uncertain."],
        "confidence_score": 0.88,
        "risk_level": "LOW",
        "sensitive_topics": [],
        "created_at": "2026-09-02T00:00:00Z",
    }
    brief = {
        "story_id": "story-1",
        "fact_sheet_id": "sheet-1",
        "fact_sheet_version": 3,
        "headline": "Inflation rose in August",
        "summary": "Verified measurements with preserved uncertainty.",
        "editorial_angle": "Lead with verified measurements and preserve uncertainty.",
        "key_points": [item["claim_text"] for item in claims],
        "exclusions": ["unsupported motive attribution"],
        "tone": "measured",
        "audience_relevance": 0.8,
        "claims": brief_claims,
        "risk_level": "LOW",
        "sensitive_topics": [],
        "unresolved_questions": ["Whether policy will change remains uncertain."],
        "priority_topics": [],
        "style_rules": {"avoid_sensationalism": True},
        "target": {"platform": "INSTAGRAM", "format": "CAROUSEL"},
        "human_review_required": True,
    }
    return {
        "immutable_fact_sheet": fact_sheet,
        "editorial_brief": brief,
        "generation_language": "en",
        "value_integrity_policy_version": "value-integrity-policy-v1",
        "claim_semantics_policy_version": "claim-semantics-policy-v1",
        "certainty_ceilings": {item["claim_id"]: {"maximum_strength": "HIGH"} for item in claims},
    }


def _quality_payload() -> dict[str, Any]:
    payload = _payload()
    selected = {"claim-1", "claim-4"}
    source_ids = [
        item["source_id"]
        for item in payload["immutable_fact_sheet"]["evidence"]
        if item["claim_id"] in selected
    ]
    payload.update(
        {
            "content_artifact": {
                "content_variant_id": "variant-1",
                "content_variant_version": 2,
                "platform": "INSTAGRAM",
                "format": "CAROUSEL",
                "language": "en",
                "title": "What the data shows",
                "body": "Variant body " + "b" * 3000,
                "caption": "Uncertainty preserved.",
                "structured_payload": {"slides": []},
                "claim_ids_used": sorted(selected),
                "source_ids_used": source_ids,
                "media_asset_ids": ["media-1"],
                "media_provenance": [{"opaque": "m" * 3000}],
            },
            "risk_level": "LOW",
            "sensitive_topics": [],
            "content_style": {"tone": "measured"},
            "quality_methodology_version": "quality-gate-methodology-v7",
        }
    )
    payload.pop("generation_language")
    payload.pop("certainty_ceilings")
    return payload


def _size(value: object) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str))


def test_content_projection_preserves_factual_contract_and_removes_operational_bulk() -> None:
    original = _payload()
    snapshot = deepcopy(original)

    projected = project_content_generation_input(original)

    assert original == snapshot
    selected = {f"claim-{index}" for index in range(CONTENT_GENERATION_MAX_CLAIMS)}
    assert {item["claim_id"] for item in projected["editorial_brief"]["claims"]} == selected
    assert {item["claim_id"] for item in projected["immutable_fact_sheet"]["claims"]} == selected
    assert set(projected["certainty_ceilings"]) == selected
    first = projected["editorial_brief"]["claims"][0]
    assert first["text"] == snapshot["editorial_brief"]["claims"][0]["text"]
    assert first["status"] == "SUPPORTED"
    assert first["label"] == "TRUE"
    assert first["semantics"] == snapshot["editorial_brief"]["claims"][0]["semantics"]
    assert first["values"] == snapshot["editorial_brief"]["claims"][0]["values"]
    assert (
        projected["editorial_brief"]["unresolved_questions"]
        == snapshot["editorial_brief"]["unresolved_questions"]
    )
    projected_evidence = projected["immutable_fact_sheet"]["evidence"][0]
    assert projected_evidence["excerpt"] == "Evidence excerpt 0."
    assert projected_evidence["graph_relations"][0]["policy_version"] == "evidence-graph-policy-v1"
    assert "url" not in projected_evidence
    assert "content_hash" not in projected_evidence
    assert "assessment_ai_run_ids" not in projected_evidence
    assert "research_run_id" not in projected_evidence["graph_relations"][0]
    assert _size(projected) < _size(original) * 0.65
    assert _size(projected) < 26_000


def test_content_projection_selects_highest_importance_with_stable_ties() -> None:
    original = _payload()
    scores = [0.1, 0.9, 0.5, 0.9, 0.2, 0.7]
    for claim, score in zip(original["immutable_fact_sheet"]["claims"], scores, strict=True):
        claim["importance_score"] = score

    projected = project_content_generation_input(original)

    assert [item["claim_id"] for item in projected["immutable_fact_sheet"]["claims"]] == [
        "claim-1",
        "claim-3",
        "claim-5",
    ]
    assert [item["claim_id"] for item in projected["editorial_brief"]["claims"]] == [
        "claim-1",
        "claim-3",
        "claim-5",
    ]
    assert set(projected["certainty_ceilings"]) == {"claim-1", "claim-3", "claim-5"}


def test_content_projection_treats_unknown_importance_conservatively() -> None:
    content = _payload()
    content["immutable_fact_sheet"]["claims"][0]["importance_score"] = None

    projected = project_content_generation_input(content)

    assert "claim-0" not in {
        item["claim_id"] for item in projected["immutable_fact_sheet"]["claims"]
    }


def test_content_projection_fails_closed_on_invalid_importance_or_ceilings() -> None:
    content = _payload()
    content["immutable_fact_sheet"]["claims"][0]["importance_score"] = "high"
    with pytest.raises(AIInputProjectionError, match="importance_score must be numeric"):
        project_content_generation_input(content)

    content = _payload()
    content["certainty_ceilings"].pop("claim-0")
    with pytest.raises(AIInputProjectionError, match="certainty ceilings do not match"):
        project_content_generation_input(content)


def test_quality_projection_is_exactly_claim_and_source_scoped() -> None:
    original = _quality_payload()

    projected = project_quality_assessment_input(original)

    sheet = projected["immutable_fact_sheet"]
    brief = projected["editorial_brief"]
    assert {item["claim_id"] for item in sheet["claims"]} == {"claim-1", "claim-4"}
    assert {item["claim_id"] for item in brief["claims"]} == {"claim-1", "claim-4"}
    assert {item["claim_id"] for item in sheet["fact_checks"]} == {"claim-1", "claim-4"}
    assert {item["claim_id"] for item in sheet["evidence"]} == {"claim-1", "claim-4"}
    assert {item["source_id"] for item in sheet["sources"]} == set(
        original["content_artifact"]["source_ids_used"]
    )
    assert "media_provenance" not in projected["content_artifact"]
    assert projected["content_artifact"]["body"] == original["content_artifact"]["body"]
    assert _size(projected) < _size(original) * 0.60
    assert _size(projected) < 22_000


def test_projection_fails_closed_on_identity_provenance_duplicates_and_missing_semantics() -> None:
    content = _payload()
    content["editorial_brief"]["fact_sheet_version"] = 99
    with pytest.raises(AIInputProjectionError, match="identity do not match"):
        project_content_generation_input(content)

    content = _payload()
    del content["editorial_brief"]["claims"][0]["semantics"]
    with pytest.raises(AIInputProjectionError, match="semantics"):
        project_content_generation_input(content)

    quality = _quality_payload()
    quality["content_artifact"]["claim_ids_used"] = ["claim-1", "claim-1"]
    with pytest.raises(AIInputProjectionError, match="must not contain duplicates"):
        project_quality_assessment_input(quality)

    quality = _quality_payload()
    quality["content_artifact"]["source_ids_used"] = ["source-not-owned"]
    with pytest.raises(AIInputProjectionError, match="source provenance"):
        project_quality_assessment_input(quality)


def test_projection_is_deterministic() -> None:
    content = _payload()
    assert project_content_generation_input(content) == project_content_generation_input(
        deepcopy(content)
    )

    quality = _quality_payload()
    assert project_quality_assessment_input(quality) == project_quality_assessment_input(
        deepcopy(quality)
    )
