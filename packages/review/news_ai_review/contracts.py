"""Closed Stage-23 human-review application and HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from news_ai_domain import ReviewState, RiskLevel
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ArtifactType(StrEnum):
    CONTENT_VARIANT = "content_variant"


class ReviewCapability(StrEnum):
    VIEW = "view"
    REVIEW = "review"
    APPROVE = "approve"
    PUBLISH = "publish"


TERMINAL_DECISIONS = frozenset(
    {ReviewState.APPROVED, ReviewState.REJECTED, ReviewState.CHANGES_REQUESTED}
)


class ReviewerPrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reviewer_id: UUID
    capabilities: frozenset[ReviewCapability]

    def has_any(self, *capabilities: ReviewCapability) -> bool:
        return bool(self.capabilities.intersection(capabilities))


class ReviewActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: ArtifactType
    artifact_id: UUID
    artifact_version: int = Field(ge=1)
    decision: ReviewState
    reviewer_id: UUID
    reason: str | None = Field(default=None, max_length=2000)
    decided_at: datetime

    @model_validator(mode="after")
    def terminal_decision_only(self) -> ReviewDecision:
        if self.decision not in TERMINAL_DECISIONS:
            raise ValueError("human review decision must be terminal")
        if self.decision in {ReviewState.REJECTED, ReviewState.CHANGES_REQUESTED} and (
            not self.reason or not self.reason.strip()
        ):
            raise ValueError("reject and request-changes decisions require a reason")
        return self


class ReviewActionResult(ReviewDecision):
    review_decision_id: UUID
    current_review_state: ReviewState


class SafeAIProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ai_run_id: UUID
    provider: str
    model: str
    task: str
    prompt_id: str | None = None
    prompt_version: str | None = None
    prompt_checksum: str | None = None
    validation_status: str


class ReviewQueueItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: ArtifactType = ArtifactType.CONTENT_VARIANT
    artifact_id: UUID
    artifact_version: int
    content_draft_id: UUID
    story_id: UUID
    fact_sheet_id: UUID
    fact_sheet_version: int
    title: str
    platform: str
    format: str
    language: str
    risk_level: RiskLevel
    sensitive_topics: tuple[str, ...]
    review_state: ReviewState
    review_ready_at: datetime


class ReviewQueuePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ReviewQueueItem, ...]
    offset: int
    limit: int
    total: int


class ReviewDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: ArtifactType = ArtifactType.CONTENT_VARIANT
    artifact_id: UUID
    artifact_version: int
    current_review_state: ReviewState
    current_reviewable: bool
    content_variant: dict[str, Any]
    content_draft: dict[str, Any]
    fact_sheet: dict[str, Any]
    quality_check: dict[str, Any] | None
    editorial_brief: dict[str, Any]
    generation_ai_provenance: SafeAIProvenance | None
    quality_ai_provenance: SafeAIProvenance | None
    existing_decision: ReviewActionResult | None
    risk_level: RiskLevel
    sensitive_topics: tuple[str, ...]
