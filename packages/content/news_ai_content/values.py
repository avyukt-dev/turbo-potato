"""Bind exact upstream value semantics to declared publication-visible occurrences."""

from enum import StrEnum
from uuid import UUID

from news_ai_domain.values import (
    VALUE_INTEGRITY_POLICY_VERSION,
    CanonicalValue,
    ClaimValues,
    FrozenValue,
    ValueTransformation,
    mechanical_text,
    value_violations,
)
from pydantic import Field, field_validator


class ClaimValueOccurrence(FrozenValue):
    artifact_path: str = Field(
        max_length=100, pattern=r"^(title|caption|slides\[[0-9]{1,2}\]\.(heading|body))$"
    )
    rendered_text: str = Field(min_length=1, max_length=300)
    presented_value: CanonicalValue
    transformation: ValueTransformation

    @field_validator("rendered_text")
    @classmethod
    def nonblank_rendered_span(cls, value):
        normalized = mechanical_text(value)
        if not normalized:
            raise ValueError("rendered value span must not be blank")
        return normalized


class ClaimValuePresentation(FrozenValue):
    claim_id: UUID
    anchor_id: UUID
    occurrences: tuple[ClaimValueOccurrence, ...] = Field(max_length=50)


class ValuePresentationCode(StrEnum):
    POLICY_MISMATCH = "POLICY_MISMATCH"
    PRESENTATION_MISSING = "PRESENTATION_MISSING"
    DUPLICATE_PRESENTATION = "DUPLICATE_PRESENTATION"
    UNKNOWN_ANCHOR = "UNKNOWN_ANCHOR"
    WRONG_ANCHOR_OWNER = "WRONG_ANCHOR_OWNER"
    OCCURRENCE_SCOPE_MISMATCH = "OCCURRENCE_SCOPE_MISMATCH"
    RENDERED_TEXT_MISSING = "RENDERED_TEXT_MISSING"
    KIND_MISMATCH = "KIND_MISMATCH"
    RELATION_MISMATCH = "RELATION_MISMATCH"
    MAGNITUDE_MISMATCH = "MAGNITUDE_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    CONVERSION_NOT_ALLOWED = "CONVERSION_NOT_ALLOWED"
    TEMPORAL_MISMATCH = "TEMPORAL_MISMATCH"
    TEMPORAL_PRECISION_MISMATCH = "TEMPORAL_PRECISION_MISMATCH"
    RENDERED_TEXT_MISMATCH = "RENDERED_TEXT_MISMATCH"


def presentation_errors(
    content, claims: dict[UUID, ClaimValues | None]
) -> tuple[tuple[ValuePresentationCode, str, UUID], ...]:
    """Return only bounded codes, paths and claim IDs, never source/provider text."""
    findings = []
    anchors = {}
    for claim_id in content.claim_ids_used:
        block = claims.get(claim_id)
        if block is None or block.policy_version != VALUE_INTEGRITY_POLICY_VERSION:
            findings.append(("POLICY_MISMATCH", "content.claim_value_presentations", claim_id))
            continue
        for anchor in block.anchors:
            if anchor.anchor_id in anchors:
                findings.append(
                    ("WRONG_ANCHOR_OWNER", "content.claim_value_presentations", claim_id)
                )
            anchors[anchor.anchor_id] = (claim_id, anchor)
    presentations = getattr(content, "claim_value_presentations", ())
    seen = set()
    for i, presentation in enumerate(presentations):
        path = f"content.claim_value_presentations[{i}]"
        if presentation.anchor_id in seen:
            findings.append(("DUPLICATE_PRESENTATION", path, presentation.claim_id))
        seen.add(presentation.anchor_id)
        original = anchors.get(presentation.anchor_id)
        if original is None:
            findings.append(("UNKNOWN_ANCHOR", path, presentation.claim_id))
            continue
        owner, anchor = original
        if owner != presentation.claim_id:
            findings.append(("WRONG_ANCHOR_OWNER", path, presentation.claim_id))
            continue
        occurrence_ids = set()
        for j, occurrence in enumerate(presentation.occurrences):
            location = f"{path}.occurrences[{j}]"
            identity = occurrence.model_dump_json()
            if identity in occurrence_ids:
                findings.append(("DUPLICATE_PRESENTATION", location, owner))
            occurrence_ids.add(identity)
            if occurrence.artifact_path in ("title", "caption"):
                text = getattr(content, occurrence.artifact_path)
            else:
                index = int(occurrence.artifact_path.split("[")[1].split("]")[0])
                if index >= len(content.slides):
                    findings.append(("RENDERED_TEXT_MISSING", location, owner))
                    continue
                slide = content.slides[index]
                if owner not in slide.claim_ids:
                    findings.append(("OCCURRENCE_SCOPE_MISMATCH", location, owner))
                text = getattr(slide, occurrence.artifact_path.rsplit(".", 1)[1])
            if mechanical_text(occurrence.rendered_text) not in mechanical_text(text):
                findings.append(("RENDERED_TEXT_MISSING", location, owner))
            findings.extend(
                (violation.value, location, owner)
                for violation in value_violations(
                    anchor,
                    occurrence.presented_value,
                    occurrence.rendered_text,
                    occurrence.transformation,
                )
            )
    for identity, (owner, _) in anchors.items():
        if identity not in seen:
            findings.append(("PRESENTATION_MISSING", "content.claim_value_presentations", owner))
    return tuple(
        (ValuePresentationCode(code), path, claim_id)
        for code, path, claim_id in dict.fromkeys(findings)
    )
