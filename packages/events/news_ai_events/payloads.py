"""Closed, versioned payload contracts for canonical application events."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, TypeVar
from uuid import UUID

from news_ai_domain import FactCheckLabel, RiskLevel
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .types import EventType


class EventPayload(BaseModel):
    """Base for closed event payloads.

    Event schemas are intentionally strict: a producer must opt into a new schema version rather
    than silently adding or changing fields in an existing contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    _uuid_list_fields: ClassVar[frozenset[str]] = frozenset()

    @field_validator("*", mode="after")
    @classmethod
    def validate_uuid_list_uniqueness(cls, value: Any, info: Any) -> Any:
        if info.field_name in cls._uuid_list_fields and len(value) != len(set(value)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError(f"{info.field_name} must be timezone-aware")
        return value


class ArticleDiscoveredV1(EventPayload):
    article_id: UUID
    source_id: UUID
    source_feed_id: UUID
    canonical_url: str = Field(min_length=1, max_length=4096)
    title: str = Field(min_length=1)
    published_at: datetime | None


class ArticleNormalizedV1(EventPayload):
    article_id: UUID
    article_version_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    language: str | None = Field(min_length=2, max_length=32)
    title: str = Field(min_length=1)


class StoryCreatedV1(EventPayload):
    story_id: UUID
    article_id: UUID
    cluster_key: str = Field(min_length=1, max_length=255)


class StoryClusteredV1(EventPayload):
    _uuid_list_fields = frozenset({"article_ids"})

    story_id: UUID
    article_ids: tuple[UUID, ...] = Field(min_length=1)
    similarity_score: float = Field(ge=0, le=1)
    cluster_method: str = Field(min_length=1, max_length=128)


class ClaimsExtractedV1(EventPayload):
    _uuid_list_fields = frozenset({"claim_ids"})

    story_id: UUID
    claim_ids: tuple[UUID, ...] = Field(min_length=1)
    ai_run_id: UUID
    model_id: UUID


class ResearchScopeV1(EventPayload):
    primary_sources: bool
    independent_corroboration: bool
    contradiction_search: bool


class EvidenceRequestedV1(EventPayload):
    _uuid_list_fields = frozenset({"claim_ids"})

    story_id: UUID
    claim_ids: tuple[UUID, ...] = Field(min_length=1)
    research_scope: ResearchScopeV1


class EvidenceCollectedV1(EventPayload):
    _uuid_list_fields = frozenset({"claim_ids", "evidence_ids"})

    story_id: UUID
    claim_ids: tuple[UUID, ...] = Field(min_length=1)
    evidence_ids: tuple[UUID, ...]
    research_run_id: UUID


class FactCheckCompletedV1(EventPayload):
    story_id: UUID
    fact_check_id: UUID
    label: FactCheckLabel
    confidence_score: float | None = Field(ge=0, le=1)
    review_required: bool


class StoryVerifiedV1(EventPayload):
    _uuid_list_fields = frozenset({"fact_check_ids"})

    story_id: UUID
    fact_check_ids: tuple[UUID, ...] = Field(min_length=1)
    confidence_score: float | None = Field(ge=0, le=1)
    risk_level: RiskLevel
    review_required: bool


class ContentRequestedV1(EventPayload):
    story_id: UUID
    fact_sheet_id: UUID
    requested_platforms: tuple[str, ...]
    requested_formats: tuple[str, ...]


class ContentGeneratedV1(EventPayload):
    _uuid_list_fields = frozenset({"content_variant_ids"})

    story_id: UUID
    content_draft_id: UUID
    content_variant_ids: tuple[UUID, ...] = Field(min_length=1)
    ai_run_id: UUID


class ContentQualityCheckedV1(EventPayload):
    content_draft_id: UUID
    passed: bool
    fact_check_passed: bool
    source_check_passed: bool
    style_check_passed: bool
    risk_level: RiskLevel
    review_required: bool


class PublicationScheduledV1(EventPayload):
    publication_id: UUID
    content_variant_id: UUID
    social_account_id: UUID
    scheduled_at: datetime


class PublicationExecutedV1(EventPayload):
    publication_id: UUID
    attempt_id: UUID
    platform: str = Field(min_length=1, max_length=64)
    external_post_id: str = Field(min_length=1, max_length=1024)
    external_url: str = Field(min_length=1, max_length=4096)
    published_at: datetime


class PublicationFailedV1(EventPayload):
    publication_id: UUID
    attempt_id: UUID
    platform: str = Field(min_length=1, max_length=64)
    error_code: str = Field(min_length=1, max_length=128)
    retryable: bool


class AnalyticsRequestedV1(EventPayload):
    publication_id: UUID
    platform: str = Field(min_length=1, max_length=64)


class AnalyticsCollectedV1(EventPayload):
    publication_id: UUID
    analytics_snapshot_id: UUID
    captured_at: datetime


PayloadModel = type[EventPayload]
PayloadT = TypeVar("PayloadT", bound=EventPayload)

_PAYLOAD_MODELS: dict[tuple[EventType, int], PayloadModel] = {
    (EventType.ARTICLE_DISCOVERED, 1): ArticleDiscoveredV1,
    (EventType.ARTICLE_NORMALIZED, 1): ArticleNormalizedV1,
    (EventType.STORY_CREATED, 1): StoryCreatedV1,
    (EventType.STORY_CLUSTERED, 1): StoryClusteredV1,
    (EventType.CLAIMS_EXTRACTED, 1): ClaimsExtractedV1,
    (EventType.EVIDENCE_REQUESTED, 1): EvidenceRequestedV1,
    (EventType.EVIDENCE_COLLECTED, 1): EvidenceCollectedV1,
    (EventType.FACT_CHECK_COMPLETED, 1): FactCheckCompletedV1,
    (EventType.STORY_VERIFIED, 1): StoryVerifiedV1,
    (EventType.CONTENT_REQUESTED, 1): ContentRequestedV1,
    (EventType.CONTENT_GENERATED, 1): ContentGeneratedV1,
    (EventType.CONTENT_QUALITY_CHECKED, 1): ContentQualityCheckedV1,
    (EventType.PUBLICATION_SCHEDULED, 1): PublicationScheduledV1,
    (EventType.PUBLICATION_EXECUTED, 1): PublicationExecutedV1,
    (EventType.PUBLICATION_FAILED, 1): PublicationFailedV1,
    (EventType.ANALYTICS_REQUESTED, 1): AnalyticsRequestedV1,
    (EventType.ANALYTICS_COLLECTED, 1): AnalyticsCollectedV1,
}


def payload_model_for(event_type: EventType, schema_version: int) -> PayloadModel:
    try:
        return _PAYLOAD_MODELS[(event_type, schema_version)]
    except KeyError as exc:
        raise ValueError(
            f"unsupported schema version {schema_version} for event {event_type.value}"
        ) from exc


def validate_event_payload(
    event_type: EventType,
    schema_version: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    model = payload_model_for(event_type, schema_version).model_validate(payload)
    return model.model_dump(mode="json")


def parse_event_payload(
    event_type: EventType,
    schema_version: int,
    payload: dict[str, Any],
    expected: type[PayloadT],
) -> PayloadT:
    model = payload_model_for(event_type, schema_version)
    if model is not expected:
        raise ValueError(
            f"event {event_type.value} version {schema_version} does not use {expected.__name__}"
        )
    return expected.model_validate(payload)
