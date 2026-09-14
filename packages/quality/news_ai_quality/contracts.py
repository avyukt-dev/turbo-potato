"""Strict Stage-22 quality-assessment contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .semantic import SemanticValidationReport

Finding = Annotated[str, Field(min_length=1, max_length=1000)]


class CertaintyEscalationCode(StrEnum):
    AFFIRMATION_OF_UNCERTAIN_CLAIM = "AFFIRMATION_OF_UNCERTAIN_CLAIM"
    QUALIFICATION_OMITTED = "QUALIFICATION_OMITTED"
    DISPUTE_OMITTED = "DISPUTE_OMITTED"
    REFUTED_CLAIM_AFFIRMED = "REFUTED_CLAIM_AFFIRMED"
    PROSE_FRAME_MISMATCH = "PROSE_FRAME_MISMATCH"


class CertaintyEscalation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: UUID
    artifact_path: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^(title|caption|slides\[[0-9]{1,2}\]\.(heading|body))$",
    )
    reason_code: CertaintyEscalationCode


class ClaimSemanticEscalationCode(StrEnum):
    ANNOUNCEMENT_AS_COMPLETED = "ANNOUNCEMENT_AS_COMPLETED"
    PLAN_AS_COMPLETED = "PLAN_AS_COMPLETED"
    EXPECTATION_AS_OBSERVED = "EXPECTATION_AS_OBSERVED"
    PREDICTION_AS_OUTCOME = "PREDICTION_AS_OUTCOME"
    ATTRIBUTION_DROPPED = "ATTRIBUTION_DROPPED"
    SEMANTIC_TYPE_RECAST = "SEMANTIC_TYPE_RECAST"


class ClaimSemanticEscalation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: UUID
    artifact_path: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^(title|caption|slides\[[0-9]{1,2}\]\.(heading|body))$",
    )
    reason_code: ClaimSemanticEscalationCode


class ValueEscalationCode(StrEnum):
    PROSE_VALUE_MISMATCH = "PROSE_VALUE_MISMATCH"
    MAGNITUDE_MISMATCH = "MAGNITUDE_MISMATCH"
    PERCENTAGE_POINT_CONFUSION = "PERCENTAGE_POINT_CONFUSION"
    RANGE_OR_BOUND_LOST = "RANGE_OR_BOUND_LOST"
    APPROXIMATION_LOST = "APPROXIMATION_LOST"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    DATE_TIME_MISMATCH = "DATE_TIME_MISMATCH"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    UNDECLARED_VALUE = "UNDECLARED_VALUE"
    DERIVED_VALUE_NOT_SUPPORTED = "DERIVED_VALUE_NOT_SUPPORTED"


class ValueEscalation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: UUID
    anchor_id: UUID | None
    artifact_path: str = Field(
        max_length=100, pattern=r"^(title|caption|slides\[[0-9]{1,2}\]\.(heading|body))$"
    )
    reason_code: ValueEscalationCode


class QualityAssessmentOutput(BaseModel):
    """Untrusted AI output; workflow decisions are deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content_variant_id: UUID
    certainty_escalations: tuple[CertaintyEscalation, ...] = Field(max_length=50)
    claim_semantic_escalations: tuple[ClaimSemanticEscalation, ...] = Field(max_length=50)
    value_escalations: tuple[ValueEscalation, ...] = Field(max_length=50)
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

    @field_validator("certainty_escalations")
    @classmethod
    def unique_escalations(
        cls, value: tuple[CertaintyEscalation, ...]
    ) -> tuple[CertaintyEscalation, ...]:
        if len(value) != len(set(value)):
            raise ValueError("certainty escalations must be unique")
        return tuple(sorted(value, key=lambda item: item.model_dump_json()))

    @field_validator("claim_semantic_escalations")
    @classmethod
    def unique_semantic_escalations(
        cls, value: tuple[ClaimSemanticEscalation, ...]
    ) -> tuple[ClaimSemanticEscalation, ...]:
        if len(value) != len(set(value)):
            raise ValueError("claim semantic escalations must be unique")
        return tuple(sorted(value, key=lambda item: item.model_dump_json()))

    @field_validator("value_escalations")
    @classmethod
    def unique_value_escalations(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("value escalations must be unique")
        return tuple(sorted(value, key=lambda item: item.model_dump_json()))


class QualityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content_variant_id: UUID
    certainty_escalations: tuple[CertaintyEscalation, ...]
    claim_semantic_escalations: tuple[ClaimSemanticEscalation, ...]
    value_escalations: tuple[ValueEscalation, ...]
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
    semantic_report: SemanticValidationReport,
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
            semantic_report.passed,
            not output.certainty_escalations,
            not output.claim_semantic_escalations,
            not output.value_escalations,
        )
    )
    return QualityDecision(
        **output.model_dump(exclude={"fabricated_quotes"}),
        fabricated_quotes=fabricated,
        passed=passed,
        review_required=review_required,
    )
