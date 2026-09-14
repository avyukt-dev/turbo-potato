"""Deterministic, provider-neutral projections for oversized AI stage inputs.

The canonical Fact Sheet, Editorial Brief, and content artifact remain the durable truth and
validation boundaries. These projections only remove duplicated or operational fields from the
provider-facing transport representation.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

AI_INPUT_PROJECTION_VERSION = "ai-input-projection-v1"

_CONTENT_GENERATION = "CONTENT_GENERATION"
_QUALITY_CHECKING = "QUALITY_CHECKING"


class AIInputProjectionError(ValueError):
    """The canonical stage input cannot be represented safely for an AI provider."""


def project_ai_input(task_type: str, payload: Any) -> Any:
    """Return a deterministic compact provider input for supported high-volume stages."""

    if not isinstance(payload, dict):
        return payload
    if task_type == _CONTENT_GENERATION:
        required = {"immutable_fact_sheet", "editorial_brief"}
        if not (required & payload.keys()):
            return payload
        if not required <= payload.keys():
            raise AIInputProjectionError(
                "content-generation projection requires Fact Sheet and Editorial Brief together"
            )
        return _project_content_generation(payload)
    if task_type == _QUALITY_CHECKING:
        required = {"immutable_fact_sheet", "editorial_brief", "content_artifact"}
        if not (required & payload.keys()):
            return payload
        if not required <= payload.keys():
            raise AIInputProjectionError(
                "quality-checking projection requires Fact Sheet, Editorial Brief and artifact"
            )
        return _project_quality_checking(payload)
    return payload


def _project_content_generation(payload: dict[str, Any]) -> dict[str, Any]:
    fact_sheet = _mapping(_required(payload, "immutable_fact_sheet"), "immutable_fact_sheet")
    brief = _mapping(_required(payload, "editorial_brief"), "editorial_brief")
    _require_same_identity(fact_sheet, brief)

    fact_claim_ids = _claim_ids(_list(_required(fact_sheet, "claims"), "Fact Sheet claims"))
    brief_claim_ids = _claim_ids(_list(_required(brief, "claims"), "Editorial Brief claims"))
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


def _project_quality_checking(payload: dict[str, Any]) -> dict[str, Any]:
    fact_sheet = _mapping(_required(payload, "immutable_fact_sheet"), "immutable_fact_sheet")
    brief = _mapping(_required(payload, "editorial_brief"), "editorial_brief")
    artifact = _mapping(_required(payload, "content_artifact"), "content_artifact")
    _require_same_identity(fact_sheet, brief)

    selected_claim_ids = frozenset(
        str(item) for item in _list(_required(artifact, "claim_ids_used"), "claim_ids_used")
    )
    if not selected_claim_ids:
        raise AIInputProjectionError("quality-checking requires at least one selected claim")

    fact_claim_ids = _claim_ids(_list(_required(fact_sheet, "claims"), "Fact Sheet claims"))
    brief_claim_ids = _claim_ids(_list(_required(brief, "claims"), "Editorial Brief claims"))
    if not selected_claim_ids <= fact_claim_ids or not selected_claim_ids <= brief_claim_ids:
        raise AIInputProjectionError("quality-checking references an unknown claim")

    selected_source_ids = frozenset(
        str(item) for item in _list(artifact.get("source_ids_used", []), "source_ids_used")
    )
    evidence = _list(_required(fact_sheet, "evidence"), "Fact Sheet evidence")
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
        for raw in _list(_required(fact_sheet, "claims"), "Fact Sheet claims")
        if str(_required((item := _mapping(raw, "Fact Sheet claim")), "claim_id"))
        in selected_claim_ids
    ]
    fact_checks = [
        _project_fact_check(item)
        for raw in _list(fact_sheet.get("fact_checks", []), "Fact Sheet fact checks")
        if (item := _mapping(raw, "Fact Sheet fact check")).get("claim_id") is not None
        and str(item["claim_id"]) in selected_claim_ids
    ]
    evidence = [
        _project_evidence(item)
        for raw in _list(_required(fact_sheet, "evidence"), "Fact Sheet evidence")
        if str(_required((item := _mapping(raw, "Fact Sheet evidence item")), "claim_id"))
        in selected_claim_ids
        and (
            selected_source_ids is None
            or item.get("source_id") is None
            or str(item["source_id"]) in selected_source_ids
        )
    ]
    evidence_source_ids = frozenset(
        str(item["source_id"]) for item in evidence if item.get("source_id") is not None
    )
    allowed_source_ids = (
        selected_source_ids if selected_source_ids is not None else evidence_source_ids
    )
    sources = [
        _project_source(item)
        for raw in _list(fact_sheet.get("sources", []), "Fact Sheet sources")
        if str(_required((item := _mapping(raw, "Fact Sheet source")), "source_id"))
        in allowed_source_ids
    ]
    counterclaims = [
        _project_claim(item)
        for raw in _list(fact_sheet.get("counterclaims", []), "Fact Sheet counterclaims")
        if str(_required((item := _mapping(raw, "Fact Sheet counterclaim")), "claim_id"))
        in selected_claim_ids
    ]

    return _pick(
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
        overrides={
            "claims": claims,
            "fact_checks": fact_checks,
            "evidence": evidence,
            "sources": sources,
            "counterclaims": counterclaims,
        },
    )


def _project_brief(brief: dict[str, Any], *, selected_claim_ids: frozenset[str]) -> dict[str, Any]:
    claims = [
        _pick(
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
        for raw in _list(_required(brief, "claims"), "Editorial Brief claims")
        if str(_required((item := _mapping(raw, "Editorial Brief claim")), "claim_id"))
        in selected_claim_ids
    ]
    return _pick(
        brief,
        (
            "story_id",
            "fact_sheet_id",
            "fact_sheet_version",
            "headline",
            "summary",
            "editorial_angle",
            "key_points",
            "exclusions",
            "tone",
            "audience_relevance",
            "risk_level",
            "sensitive_topics",
            "unresolved_questions",
            "priority_topics",
            "style_rules",
            "target",
        ),
        overrides={"claims": claims},
    )


def _project_claim(claim: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        claim,
        (
            "claim_id",
            "claim_text",
            "claim_type",
            "semantics",
            "values",
            "status",
            "confidence_score",
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
    return _pick(
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


def _project_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    return _pick(
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
            "independence_group",
            "directness",
            "origin_role",
            "provenance_state",
            "temporal_role",
        ),
    )


def _project_source(source: dict[str, Any]) -> dict[str, Any]:
    return _pick(
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


def _pick(
    value: dict[str, Any],
    keys: tuple[str, ...],
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    projected = {key: deepcopy(value[key]) for key in keys if key in value}
    if overrides:
        projected.update(deepcopy(overrides))
    return projected


def _required(value: dict[str, Any], key: str) -> Any:
    if key not in value:
        raise AIInputProjectionError(f"AI input projection requires {key}")
    return value[key]


def _mapping(value: Any, owner: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AIInputProjectionError(f"{owner} must be an object")
    return value


def _list(value: Any, owner: str) -> list[Any]:
    if not isinstance(value, list):
        raise AIInputProjectionError(f"{owner} must be an array")
    return value
