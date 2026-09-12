"""Strict Stage-22 quality-assessment contracts."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Finding = Annotated[str, Field(min_length=1, max_length=1000)]


class QualityAssessmentOutput(BaseModel):
    """Untrusted AI output; workflow decisions are deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content_variant_id: UUID
    factual_accuracy_passed: bool
    source_alignment_passed: bool
    citation_alignment_passed: bool
    style_passed: bool
    unsupported_claims: tuple[Finding, ...] = Field(default=(), max_length=50)
    fabricated_quotes: tuple[Finding, ...] = Field(default=(), max_length=50)
    incorrect_names: tuple[Finding, ...] = Field(default=(), max_length=50)
    incorrect_dates: tuple[Finding, ...] = Field(default=(), max_length=50)
    incorrect_numbers: tuple[Finding, ...] = Field(default=(), max_length=50)
    missing_context: tuple[Finding, ...] = Field(default=(), max_length=50)
    defamation_risk: bool
    sensitive_topic_error: bool
    notes: tuple[Finding, ...] = Field(default=(), max_length=50)

    @field_validator(
        "unsupported_claims",
        "fabricated_quotes",
        "incorrect_names",
        "incorrect_dates",
        "incorrect_numbers",
        "missing_context",
        "notes",
    )
    @classmethod
    def unique_findings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("quality findings must be unique")
        return normalized


class QualityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content_variant_id: UUID
    factual_accuracy_passed: bool
    source_alignment_passed: bool
    citation_alignment_passed: bool
    style_passed: bool
    unsupported_claims: tuple[str, ...]
    fabricated_quotes: tuple[str, ...]
    incorrect_names: tuple[str, ...]
    incorrect_dates: tuple[str, ...]
    incorrect_numbers: tuple[str, ...]
    missing_context: tuple[str, ...]
    defamation_risk: bool
    sensitive_topic_error: bool
    passed: bool
    review_required: bool = True
    notes: tuple[str, ...]


def decide_quality(
    output: QualityAssessmentOutput,
    *,
    deterministic_quotes: tuple[str, ...],
    review_required: bool,
) -> QualityDecision:
    fabricated = tuple(dict.fromkeys((*deterministic_quotes, *output.fabricated_quotes)))
    passed = all(
        (
            output.factual_accuracy_passed,
            output.source_alignment_passed,
            output.citation_alignment_passed,
            output.style_passed,
            not output.unsupported_claims,
            not fabricated,
            not output.incorrect_names,
            not output.incorrect_dates,
            not output.incorrect_numbers,
            not output.missing_context,
            not output.defamation_risk,
            not output.sensitive_topic_error,
        )
    )
    return QualityDecision(
        **output.model_dump(exclude={"fabricated_quotes"}),
        fabricated_quotes=fabricated,
        passed=passed,
        review_required=review_required,
    )
