from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from news_ai_ai import (
    AI_INPUT_PROJECTION_VERSION,
    AIInputProjectionError,
    AIRequest,
    AITaskType,
    project_ai_input,
)


def _payload(claim_count: int = 6) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    brief_claims: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []

    for index in range(claim_count):
        claim_id = f"claim-{index}"
        supporting_id = f"evidence-{index}-support"
        contradicting_id = f"evidence-{index}-contradict"
        support_source = f"source-{index}-support"
        contradict_source = f"source-{index}-contradict"
        amount = f"{index + 4}.82"
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
                    "source_text": f"{amount}%",
                    "value": {
                        "kind": "PERCENT",
                        "quantity": {
                            "relation": "EXACT",
                            "amount": amount,
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
                "claim_text": f"Claim {index} reports a measured percentage.",
                "claim_type": "MEASUREMENT",
                "semantics": semantics,
                "values": values,
                "status": "SUPPORTED",
                "confidence_score": 0.9,
                "importance_score": 0.8,
                "risk_level": "LOW",
                "sensitive_topics": [],
                "evidence_ids": [supporting_id, contradicting_id],
                "contradictory_evidence_ids": [contradicting_id],
                "temporal_start": "2026-09-01T00:00:00Z",
                "temporal_end": None,
                "location_ids": [],
            }
        )
        brief_claims.append(
            {
                "claim_id": claim_id,
                "text": f"Claim {index} reports a measured percentage.",
                "status": "SUPPORTED",
                "fact_check_id": f"check-{index}",
                "label": "TRUE",
                "semantics": deepcopy(semantics),
                "values": deepcopy(values),
                "confidence_score": 0.9,
                "evidence_ids": [supporting_id, contradicting_id],
                "evidence_excerpts": [
                    f"Primary excerpt for claim {index}.",
                    f"Contradicting excerpt for claim {index}.",
                ],
            }
        )
        checks.append(
            {
                "fact_check_id": f"check-{index}",
                "story_id": "story-1",
                "claim_id": claim_id,
                "label": "TRUE",
                "confidence_score": 0.9,
                "summary": f"Fact-check summary {index}.",
                "supporting_evidence_ids": [supporting_id],
                "contradicting_evidence_ids": [contradicting_id],
                "review_required": True,
                "review_state": "NOT_READY",
            }
        )
        evidence.extend(
            [
                {
                    "evidence_id": supporting_id,
                    "claim_id": claim_id,
                    "source_id": support_source,
                    "article_id": f"article-{index}-support",
                    "article_version_id": f"version-{index}-support",
                    "title": f"Primary record {index}",
                    "url": f"https://example.test/{index}/support",
                    "published_at": "2026-09-01T00:00:00Z",
                    "retrieved_at": "2026-09-02T00:00:00Z",
                    "relation": "DIRECT_SUPPORT",
                    "strength_score": 0.95,
                    "excerpt": f"Primary excerpt for claim {index}.",
                    "provenance_note": "operational provenance " + "p" * 1200,
                    "content_hash": "a" * 64,
                    "source_level": 1,
                    "source_policy_basis": "primary-record",
                    "lineage_status": "INDEPENDENT",
                    "lineage_basis": "document-id",
                    "independence_group": f"group-{index}-support",
                    "originating_reference": f"ref-{index}-support",
                    "assessment_ai_run_ids": [f"run-{index}-support"],
                    "directness": "DIRECT",
                    "origin_role": "ORIGINAL",
                    "provenance_state": "PRIMARY",
                    "temporal_role": "CONTEMPORANEOUS",
                    "semantics_policy_version": "evidence-semantics-v1",
                    "graph_relations": [
                        {
                            "policy_version": "graph-v1",
                            "relation": "SUPPORTS",
                            "opaque": "g" * 1200,
                        }
                    ],
                },
                {
                    "evidence_id": contradicting_id,
                    "claim_id": claim_id,
                    "source_id": contradict_source,
                    "article_id": f"article-{index}-contradict",
                    "article_version_id": f"version-{index}-contradict",
                    "title": f"Contradicting record {index}",
                    "url": f"https://example.test/{index}/contradict",
                    "published_at": "2026-09-01T00:00:00Z",
                    "retrieved_at": "2026-09-02T00:00:00Z",
                    "relation": "CONTRADICTS",
                    "strength_score": 0.6,
                    "excerpt": f"Contradicting excerpt for claim {index}.",
                    "provenance_note": "operational provenance " + "q" * 1200,
                    "content_hash": "b" * 64,
                    "source_level": 2,
                    "source_policy_basis": "independent-report",
                    "lineage_status": "INDEPENDENT",
                    "lineage_basis": "publisher-id",
                    "independence_group": f"group-{index}-contradict",
                    "originating_reference": f"ref-{index}-contradict",
                    "assessment_ai_run_ids": [f"run-{index}-contradict"],
                    "directness": "INDIRECT",
                    "origin_role": "REPORTING",
                    "provenance_state": "SECONDARY",
                    "temporal_role": "CONTEMPORANEOUS",
                    "semantics_policy_version": "evidence-semantics-v1",
                    "graph_relations": [
                        {
                            "policy_version": "graph-v1",
                            "relation": "CONTRADICTS",
                            "opaque": "h" * 1200,
                        }
                    ],
                },
            ]
        )
        for source_id, level in ((support_source, 1), (contradict_source, 2)):
            sources.append(
                {
                    "source_id": source_id,
                    "name": f"Source {source_id}",
                    "source_level": level,
                    "url": f"https://example.test/source/{source_id}",
                    "publisher": f"Publisher {index}",
                    "published_at": "2026-09-01T00:00:00Z",
                    "retrieved_at": "2026-09-02T00:00:00Z",
                    "language": "en",
                    "operational_blob": "s" * 1200,
                }
            )

    fact_sheet = {
        "fact_sheet_id": "sheet-1",
        "story_id": "story-1",
        "version": 3,
        "headline": "Inflation rose in August",
        "summary": "The measured rate rose while uncertainty remains visible.",
        "claims": claims,
        "fact_checks": checks,
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
        "summary": "The measured rate rose while uncertainty remains visible.",
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


def _quality_payload(*, include_media_provenance: bool = False) -> dict[str, Any]:
    payload = _payload()
    selected_claim_ids = {"claim-1", "claim-4"}
    evidence = payload["immutable_fact_sheet"]["evidence"]
    source_ids = [item["source_id"] for item in evidence if item["claim_id"] in selected_claim_ids]
    artifact = {
        "content_variant_id": "variant-1",
        "content_variant_version": 2,
        "platform": "INSTAGRAM",
        "format": "CAROUSEL",
        "language": "en",
        "title": "What the data shows",
        "body": "Derived body retained for exact quality review " + "b" * 4000,
        "caption": "The figures rose, with uncertainty preserved.",
        "structured_payload": {
            "slides": [
                {
                    "position": 1,
                    "heading": "Measured change",
                    "body": "The reported percentage rose.",
                    "claim_ids": sorted(selected_claim_ids),
                },
                {
                    "position": 2,
                    "heading": "Context",
                    "body": "The policy consequence remains uncertain.",
                    "claim_ids": sorted(selected_claim_ids),
                },
            ],
            "hashtags": [],
            "claim_ids_used": sorted(selected_claim_ids),
            "claim_presentations": [],
            "claim_semantic_presentations": [],
            "claim_value_presentations": [],
        },
        "claim_ids_used": sorted(selected_claim_ids),
        "source_ids_used": source_ids,
        "media_asset_ids": ["media-1", "media-2"],
    }
    if include_media_provenance:
        artifact["media_provenance"] = [{"id": "media-1", "opaque": "m" * 4000}]

    payload.update(
        {
            "content_artifact": artifact,
            "risk_level": "LOW",
            "sensitive_topics": [],
            "content_style": {"tone": "measured"},
            "quality_methodology_version": "quality-gate-methodology-v7",
        }
    )
    payload.pop("generation_language")
    payload.pop("certainty_ceilings")
    return payload


def _encoded_size(value: object) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str))


def test_content_projection_keeps_all_claims_semantics_values_and_contradictions() -> None:
    original = _payload()
    snapshot = deepcopy(original)

    projected = project_ai_input(AITaskType.CONTENT_GENERATION.value, original)

    assert original == snapshot
    assert projected is not original
    fact_sheet = projected["immutable_fact_sheet"]
    brief = projected["editorial_brief"]
    assert [item["claim_id"] for item in fact_sheet["claims"]] == [
        f"claim-{index}" for index in range(6)
    ]
    assert [item["claim_id"] for item in brief["claims"]] == [
        f"claim-{index}" for index in range(6)
    ]
    assert (
        fact_sheet["claims"][0]["semantics"]
        == snapshot["immutable_fact_sheet"]["claims"][0]["semantics"]
    )
    assert (
        fact_sheet["claims"][0]["values"] == snapshot["immutable_fact_sheet"]["claims"][0]["values"]
    )
    assert fact_sheet["claims"][0]["contradictory_evidence_ids"] == ["evidence-0-contradict"]
    assert {item["relation"] for item in fact_sheet["evidence"]} == {
        "DIRECT_SUPPORT",
        "CONTRADICTS",
    }
    assert fact_sheet["locations"] == ["India"]
    assert fact_sheet["context"] == ["Monthly inflation release"]
    assert "text" not in brief["claims"][0]
    assert "evidence_excerpts" not in brief["claims"][0]
    assert "human_review_required" not in brief
    assert "url" not in fact_sheet["evidence"][0]
    assert "content_hash" not in fact_sheet["evidence"][0]
    assert "graph_relations" not in fact_sheet["evidence"][0]
    assert "created_at" not in fact_sheet


def test_quality_projection_scopes_fact_boundary_to_exact_used_claims_and_sources() -> None:
    original = _quality_payload()

    projected = project_ai_input(AITaskType.QUALITY_CHECKING.value, original)

    fact_sheet = projected["immutable_fact_sheet"]
    brief = projected["editorial_brief"]
    artifact = projected["content_artifact"]
    assert {item["claim_id"] for item in fact_sheet["claims"]} == {"claim-1", "claim-4"}
    assert {item["claim_id"] for item in brief["claims"]} == {"claim-1", "claim-4"}
    assert {item["claim_id"] for item in fact_sheet["fact_checks"]} == {"claim-1", "claim-4"}
    assert {item["claim_id"] for item in fact_sheet["evidence"]} == {"claim-1", "claim-4"}
    assert {item["source_id"] for item in fact_sheet["sources"]} == set(
        original["content_artifact"]["source_ids_used"]
    )
    assert artifact == original["content_artifact"]
    assert fact_sheet["locations"] == ["India"]
    assert fact_sheet["context"] == ["Monthly inflation release"]


def test_quality_projection_defensively_removes_media_provenance_only() -> None:
    original = _quality_payload(include_media_provenance=True)
    original_artifact = deepcopy(original["content_artifact"])

    projected = project_ai_input(AITaskType.QUALITY_CHECKING.value, original)

    artifact = projected["content_artifact"]
    assert "media_provenance" not in artifact
    original_artifact.pop("media_provenance")
    assert artifact == original_artifact


def test_projection_fails_closed_for_identity_claim_and_source_mismatches() -> None:
    content = _payload()
    content["editorial_brief"]["fact_sheet_version"] = 99
    with pytest.raises(AIInputProjectionError, match="identity do not match"):
        project_ai_input(AITaskType.CONTENT_GENERATION.value, content)

    quality = _quality_payload()
    quality["content_artifact"]["claim_ids_used"] = ["claim-not-known"]
    with pytest.raises(AIInputProjectionError, match="unknown claim"):
        project_ai_input(AITaskType.QUALITY_CHECKING.value, quality)

    quality = _quality_payload()
    quality["content_artifact"]["source_ids_used"] = ["source-not-owned"]
    with pytest.raises(AIInputProjectionError, match="source provenance"):
        project_ai_input(AITaskType.QUALITY_CHECKING.value, quality)


def test_projection_is_deterministic_and_materially_smaller_for_e2e_shaped_input() -> None:
    content = _payload()
    first = project_ai_input(AITaskType.CONTENT_GENERATION.value, content)
    second = project_ai_input(AITaskType.CONTENT_GENERATION.value, deepcopy(content))

    assert json.dumps(first, sort_keys=True, default=str) == json.dumps(
        second, sort_keys=True, default=str
    )
    assert _encoded_size(first) < _encoded_size(content) * 0.65

    quality = _quality_payload()
    compact_quality = project_ai_input(AITaskType.QUALITY_CHECKING.value, quality)
    assert _encoded_size(compact_quality) < _encoded_size(quality) * 0.60


def test_ai_request_applies_projection_without_changing_canonical_input_hash() -> None:
    payload = _payload()
    request = AIRequest(
        task_type=AITaskType.CONTENT_GENERATION,
        system_prompt="Return JSON.",
        input=payload,
        input_hash="canonical-full-context-hash",
    )

    assert request.input_hash == "canonical-full-context-hash"
    assert request.metadata["input_projection_version"] == AI_INPUT_PROJECTION_VERSION
    assert _encoded_size(request.input) < _encoded_size(payload) * 0.65
    assert "url" not in request.input["immutable_fact_sheet"]["evidence"][0]

    reconstructed = AIRequest.model_validate(request.model_dump(mode="python"))
    assert reconstructed.input == request.input
    assert reconstructed.input_hash == request.input_hash
    assert reconstructed.metadata == request.metadata


def test_generic_requests_for_content_stages_remain_unchanged() -> None:
    payload = {"synthetic": "stage route validation"}
    request = AIRequest(
        task_type=AITaskType.CONTENT_GENERATION,
        system_prompt="Validate route.",
        input=payload,
    )

    assert request.input is payload
    assert request.metadata == {}
    assert project_ai_input(AITaskType.QUALITY_CHECKING.value, {}) == {}
