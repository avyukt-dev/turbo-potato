"""Typed contracts for deterministic article normalization."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from .acquisition import ContentAcquisitionProvenance


class ArticleNormalizationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: UUID
    source_feed_id: UUID | None = None
    url: AnyHttpUrl
    title: str = Field(min_length=1)
    author: str | None = None
    published_at: datetime | None = None
    language: str | None = None
    summary: str | None = None
    body: str | None = None
    content_acquisition: ContentAcquisitionProvenance | None = None
    external_id: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("url")
    @classmethod
    def reject_embedded_credentials(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username or value.password:
            raise ValueError("article URL must not contain embedded credentials")
        return value

    @field_validator("published_at", "retrieved_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("article timestamps must be timezone-aware")
        return value


class NormalizedArticle(BaseModel):
    source_id: UUID
    source_feed_id: UUID | None = None
    canonical_url: str
    title: str
    author: str | None = None
    published_at: datetime | None = None
    language: str | None = None
    summary: str | None = None
    body: str | None = None
    content_acquisition: ContentAcquisitionProvenance | None = None
    external_id: str | None = None
    retrieved_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
