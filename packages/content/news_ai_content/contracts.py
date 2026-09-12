"""Strict Stage-21 content contracts owned by the content domain."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from news_ai_domain import ClaimVerificationStatus, FactCheckLabel, ReviewState, RiskLevel
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContentPlatform(StrEnum):
    INSTAGRAM = "INSTAGRAM"


class ContentFormat(StrEnum):
    CAROUSEL = "CAROUSEL"


class ContentTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: ContentPlatform
    format: ContentFormat


class BriefClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: UUID
    text: str = Field(min_length=1)
    status: ClaimVerificationStatus
    fact_check_id: UUID
    label: FactCheckLabel
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    evidence_ids: tuple[UUID, ...] = ()
    evidence_excerpts: tuple[str, ...] = ()


class EditorialBrief(BaseModel):
    """Deterministic editorial framing built only from one immutable Fact Sheet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    story_id: UUID
    fact_sheet_id: UUID
    fact_sheet_version: int = Field(ge=1)
    headline: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    editorial_angle: str = Field(min_length=1)
    key_points: tuple[str, ...] = Field(min_length=1)
    exclusions: tuple[str, ...]
    tone: str = Field(min_length=1)
    audience_relevance: float | None = Field(default=None, ge=0, le=1)
    claims: tuple[BriefClaim, ...] = Field(min_length=1)
    risk_level: RiskLevel
    sensitive_topics: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    priority_topics: tuple[str, ...] = ()
    style_rules: dict[str, bool]
    target: ContentTarget
    human_review_required: bool


class CarouselSlide(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    position: int = Field(ge=1, le=20)
    heading: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=1200)
    claim_ids: tuple[UUID, ...] = Field(min_length=1)

    @field_validator("claim_ids")
    @classmethod
    def unique_claims(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(value) != len(set(value)):
            raise ValueError("slide claim_ids must be unique")
        return value


class ContentGenerationOutput(BaseModel):
    """Untrusted AI output; application-owned workflow fields are intentionally absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    story_id: UUID
    fact_sheet_id: UUID
    fact_sheet_version: int = Field(ge=1)
    platform: ContentPlatform
    format: ContentFormat
    language: str = Field(min_length=2, max_length=32)
    title: str = Field(min_length=1, max_length=300)
    slides: tuple[CarouselSlide, ...] = Field(min_length=2, max_length=20)
    caption: str = Field(min_length=1, max_length=4000)
    hashtags: tuple[str, ...] = Field(default=(), max_length=30)
    claim_ids_used: tuple[UUID, ...] = Field(min_length=1)

    @field_validator("hashtags")
    @classmethod
    def validate_hashtags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("hashtags must be unique")
        if any(not item.startswith("#") or len(item) < 2 for item in value):
            raise ValueError("hashtags must be non-empty and start with #")
        return value

    @model_validator(mode="after")
    def sequential_slides(self) -> ContentGenerationOutput:
        if tuple(slide.position for slide in self.slides) != tuple(range(1, len(self.slides) + 1)):
            raise ValueError("carousel slide positions must be consecutive from 1")
        if len(self.claim_ids_used) != len(set(self.claim_ids_used)):
            raise ValueError("claim_ids_used must be unique")
        slide_claims = {claim_id for slide in self.slides for claim_id in slide.claim_ids}
        if slide_claims != set(self.claim_ids_used):
            raise ValueError("claim_ids_used must exactly match the claims cited by slides")
        return self

    def structured_payload(self) -> dict[str, Any]:
        return {
            "slides": [slide.model_dump(mode="json") for slide in self.slides],
            "hashtags": list(self.hashtags),
            "claim_ids_used": [str(item) for item in self.claim_ids_used],
        }


class InstagramCarouselContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    headline: str = Field(min_length=1)
    slides: tuple[CarouselSlide, ...] = Field(min_length=2)
    caption: str = Field(min_length=1)
    hashtags: tuple[str, ...]
    claim_ids_used: tuple[UUID, ...] = Field(min_length=1)


class ContentDraftArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content_draft_id: UUID
    story_id: UUID
    fact_sheet_id: UUID
    fact_sheet_version: int = Field(ge=1)
    editorial_brief_id: UUID | None = None
    risk_level: RiskLevel
    sensitive_topics: tuple[str, ...]
    review_state: ReviewState
    variant_ids: tuple[UUID, ...]
    created_by_ai_run_id: UUID
    version: int = Field(ge=1)


class ContentVariantArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content_variant_id: UUID
    content_draft_id: UUID
    story_id: UUID
    fact_sheet_id: UUID
    fact_sheet_version: int = Field(ge=1)
    platform: ContentPlatform
    format: ContentFormat
    language: str = Field(min_length=2, max_length=32)
    title: str
    body: str
    caption: str
    slides: tuple[CarouselSlide, ...]
    thread: tuple[str, ...] = ()
    hashtags: tuple[str, ...]
    media_asset_ids: tuple[UUID, ...]
    claim_ids_used: tuple[UUID, ...]
    source_ids_used: tuple[UUID, ...]
    risk_level: RiskLevel
    sensitive_topics: tuple[str, ...]
    review_state: ReviewState
    version: int = Field(ge=1)
