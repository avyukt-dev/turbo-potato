"""Deterministic compact AI projections owned by the content/quality factual boundary."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

AI_INPUT_PROJECTION_VERSION = "ai-input-projection-v1"


class AIInputProjectionError(ValueError):
    """Canonical content inputs cannot be represented safely for an AI provider."""


def project_content_generation_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Compact one already-validated content-generation input without changing factual meaning."""

    fact_sheet = _mapping(_required(payload, "immutable_fact_sheet"), "immutable_fact_sheet")
    brief = _mapping(_required(payload, "editorial_brief"), "editorial_brief")
    _require_same_identity(fact_sheet, brief)

    fact_claim_ids = _claim_ids(_array(_required(fact_sheet, "claims"), "Fact Sheet claims"))
    brief_claim_ids = _claim_ids(_array(_required(brief, "claims"), "Editorial Brief claims"))
    if fact_claim_ids != brief_claim_ids:
        raise AIInputProjectionError(
            "content-generation Fact Sheet and Editorial Brief claim sets do not match"
        )

    projected = deepcopy(payload)
    projected["immutable_fact_sheet"] = _project_fact_sheet(
        fact_sheet,
        selected_claim_ids=fact_claim_ids,
    )
    projected["editorial_brief"] = _project_brief(
        brief,
        selected_claim_ids=fact_claim_ids,
    )
    return projected


def project_quality_assessment_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Compact one already-validated quality input to the exact variant factual boundary."""

    fact_sheet = _mapping(_required(payload, "immutable_fact_sheet"), "immutable_fact_sheet")
    brief = _mapping(_required(payload, "editorial_brief"), "editorial_brief")
    artifact = _mapping(_required(payload, "content_artifact"), "content_artifact")
    _require_same_identity(fact_sheet, brief)

    selected_claim_ids = _unique_ids(
        _array(_required(artifact, "claim_ids_used"), "claim_ids_used"),
        owner="quality-checking claim_ids_used",
    )
    if not selected_claim_ids:
        raise AIInputProjectionError("quality-checking requires at least one selected claim")

    fact_claim_ids = _claim_ids(_array(_required(fact_sheet, "claims"), "Fact Sheet claims"))
    brief_claim_ids = _claim_ids(_array(_required(brief, "claims"), "Editorial Brief claims"))
    if not selected_claim_ids <= fact_claim_ids or not selected_claim_ids <= brief_claim_ids:
        raise AIInputProjectionError("quality-checking references an unknown claim")

    selected_source_ids = _unique_ids(
        _array(_required(artifact, "source_ids_used"), "source_ids_used"),
        owner="quality-checking source_ids_used",
    )
    evidence = _array(_required(fact_sheet, "evidence"), "Fact Sheet evidence")
    expected_source_ids = frozenset(
        str(item["source_id"])
        for raw in evidence
        if str(_required((item := _mapping(raw, "Fact Sheet evidence item")), "claim_id"))
        in selected_claim_ids
        and item.get("source_id") is not None
    )
    if selected_source_ids != expected_source_ids:
        raise AIInputProjectionError(
            "quality-checking source provenance does not match selected-claim evidence"
        )

    projected = deepcopy(payload)
    projected["immutable_fact_sheet"] = _project_fact_sheet(
        fact_sheet,
        selected_claim_ids=selected_claim_ids,
        selected_source_ids=selected_source_ids,
    )
    projected["editorial_brief"] = _project_brief(
        brief,
        selected_claim_ids=selected_claim_ids,
    )
    projected["content_artifact"] = _project_content_artifact(artifact)
    return projected


def _project_fact_sheet(
    fact_sheet: dict[str, Any],
    *,
    selected_claim_ids: frozenset[str],
    selected_source_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    claims = [
        _project_claim(item)
        for raw in _array(_required(fact_sheet, "claims"), "Fact Sheet claims")
        if str(_required((item := _mapping(raw, "Fact Sheet claim")), "claim_id"))
        in selected_claim_ids
    ]
    if {str(item["claim_id"]) for item in claims} != selected_claim_ids:
        raise AIInputProjectionError("projected Fact Sheet claims do not cover selected claims")

    fact_checks = [
        _project_fact_check(item)
        for raw in _array(_required(fact_sheet, "fact_checks"), "Fact Sheet fact checks")
        if (item := _mapping(raw, "Fact Sheet fact check")).get("claim_id") is not None
        and str(item["claim_id"]) in selected_claim_ids
    ]
    checked_claim_ids = {str(item["claim_id"]) for item in fact_checks}
    if checked_claim_ids != selected_claim_ids:
        raise AIInputProjectionError("selected claims require exactly one FactCheck each")

    selected_evidence = [
        item
        for raw in _array(_required(fact_sheet, "evidence"), "Fact Sheet evidence")
        if str(_required((item := _mapping(raw, "Fact Sheet evidence item")), "claim_id"))
        in selected_claim_ids
        and (
            selected_source_ids is None
            or item.get("source_id") is None
            or str(item["source_id"]) in selected_source_ids
        )
    ]
    selected_evidence_ids = frozenset(
        str(_required(item, "evidence_id")) for item in selected_evidence
    )
    evidence = [
        _project_evidence(item, selected_evidence_ids=selected_evidence_ids)
        for item in selected_evidence
    ]
    evidence_source_ids = frozenset(
        str(item["source_id"]) for item in evidence if item.get("source_id") is not None
    )
    allowed_source_ids = (
        selected_source_ids if selected_source_ids is not None else evidence_source_ids
    )
    if selected_source_ids is not None and evidence_source_ids != selected_source_ids:
        raise AIInputProjectionError("projected evidence does not cover selected sources")

    sources = [
        _project_source(item)
        for raw in _array(_required(fact_sheet, "sources"), "Fact Sheet sources")
        if str(_required((item := _mapping(raw, "Fact Sheet source")), "source_id"))
        in allowed_source_ids
    ]
    if {str(item["source_id"]) for item in sources} != allowed_source_ids:
        raise AIInputProjectionError("projected Fact Sheet sources do not cover selected sources")

    counterclaims = [
        _project_claim(item)
        for raw in _array(_required(fact_sheet, "counterclaims"), "Fact Sheet counterclaims")
        if str(_required((item := _mapping(raw, "Fact Sheet counterclaim")), "claim_id"))
        in selected_claim_ids
    ]

    projected = _pick_required(
        fact_sheet,
        (
            "fact_sheet_id",
            "story_id",
            "version",
            "headline",
            "summary",
            "timeline",
            "entities",
            "locations",
            "context",
            "unresolved_questions",
            "confidence_score",
            "risk_level",
            "sensitive_topics",
        ),
    )
    projected.update(
        {
            "claims": claims,
            "fact_checks": fact_checks,
            "evidence": evidence,
            "sources": sources,
            "counterclaims": counterclaims,
        }
    )
    return projected


def _project_brief(brief: dict[str, Any], *, selected_claim_ids: frozenset[str]) -> dict[str, Any]:
    claims = [
        _pick_required(
            item,
            (
                "claim_id",
                "text",
                "status",
                "fact_check_id",
                "label",
                "semantics",
                "values",
                "confidence_score",
            ),
        )
        for raw in _array(_required(brief, "claims"), "Editorial Brief claims")
        if str(_required((item := _mapping(raw, "Editorial Brief claim")), "claim_id"))
        in selected_claim_ids
    ]
    if {str(item["claim_id"]) for item in claims} != selected_claim_ids:
        raise AIInputProjectionError(
            "projected Editorial Brief claims do not cover selected claims"
        )

    projected = _pick_required(
        brief,
        (
            "story_id",
            "fact_sheet_id",
            "fact_sheet_version",
            "editorial_angle",
            "exclusions",
            "tone",
            "audience_relevance",
            "unresolved_questions",
            "priority_topics",
            "style_rules",
            "target",
        ),
    )
    projected["claims"] = claims
    return projected


def _project_claim(claim: dict[str, Any]) -> dict[str, Any]:
    return _pick_required(
        claim,
        (
            "claim_id",
            "claim_type",
            "importance_score",
            "risk_level",
            "sensitive_topics",
            "evidence_ids",
            "contradictory_evidence_ids",
            "temporal_start",
            "temporal_end",
            "location_ids",
        ),
    )


def _project_fact_check(fact_check: dict[str, Any]) -> dict[str, Any]:
    return _pick_required(
        fact_check,
        (
            "fact_check_id",
            "claim_id",
            "label",
            "confidence_score",
            "summary",
            "supporting_evidence_ids",
            "contradicting_evidence_ids",
        ),
    )


def _project_evidence(
    evidence: dict[str, Any], *, selected_evidence_ids: frozenset[str]
) -> dict[str, Any]:
    projected = _pick_required(
        evidence,
        (
            "evidence_id",
            "claim_id",
            "source_id",
            "title",
            "published_at",
            "relation",
            "strength_score",
            "excerpt",
            "source_level",
            "source_policy_basis",
            "lineage_status",
            "lineage_basis",
            "independence_group",
            "directness",
            "origin_role",
            "provenance_state",
            "temporal_role",
            "semantics_policy_version",
            "graph_relations",
        ),
    )
    projected["graph_relations"] = _project_graph_relations(
        projected["graph_relations"], selected_evidence_ids=selected_evidence_ids
    )
    return projected


def _project_graph_relations(
    value: Any, *, selected_evidence_ids: frozenset[str]
) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    relations: list[dict[str, Any]] = []
    for raw in _array(value, "Fact Sheet graph relations"):
        relation = _pick_required(
            _mapping(raw, "Fact Sheet graph relation"),
            (
                "relation_type",
                "target_evidence_id",
                "external_reference",
                "basis",
                "policy_version",
            ),
        )
        target = relation["target_evidence_id"]
        if target is None or str(target) in selected_evidence_ids:
            relations.append(relation)
    return relations


def _project_source(source: dict[str, Any]) -> dict[str, Any]:
    return _pick_required(
        source,
        ("source_id", "name", "source_level", "publisher", "published_at", "language"),
    )


def _project_content_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    projected = deepcopy(artifact)
    projected.pop("media_provenance", None)
    return projected


def _require_same_identity(fact_sheet: dict[str, Any], brief: dict[str, Any]) -> None:
    if (
        str(_required(fact_sheet, "story_id")) != str(_required(brief, "story_id"))
        or str(_required(fact_sheet, "fact_sheet_id")) != str(_required(brief, "fact_sheet_id"))
        or int(_required(fact_sheet, "version")) != int(_required(brief, "fact_sheet_version"))
    ):
        raise AIInputProjectionError("Fact Sheet and Editorial Brief identity do not match")


def _claim_ids(claims: list[Any]) -> frozenset[str]:
    result = [str(_required(_mapping(item, "claim"), "claim_id")) for item in claims]
    if len(result) != len(set(result)):
        raise AIInputProjectionError("claim identifiers must be unique")
    return frozenset(result)


def _unique_ids(values: list[Any], *, owner: str) -> frozenset[str]:
    result = [str(item) for item in values]
    if len(result) != len(set(result)):
        raise AIInputProjectionError(f"{owner} must not contain duplicates")
    return frozenset(result)


def _pick_required(value: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    missing = [key for key in keys if key not in value]
    if missing:
        raise AIInputProjectionError(
            "AI input projection requires " + ", ".join(sorted(missing))
        )
    return {key: deepcopy(value[key]) for key in keys}


def _required(value: dict[str, Any], key: str) -> Any:
    if key not in value:
        raise AIInputProjectionError(f"AI input projection requires {key}")
    return value[key]


def _mapping(value: Any, owner: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AIInputProjectionError(f"{owner} must be an object")
    return value


def _array(value: Any, owner: str) -> list[Any]:
    if not isinstance(value, list):
        raise AIInputProjectionError(f"{owner} must be an array")
    return value
