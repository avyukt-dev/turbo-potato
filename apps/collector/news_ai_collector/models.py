"""Typed contracts for feed collection."""

from datetime import UTC, datetime
from uuid import UUID

from news_ai_common.feed_media import MAX_FEED_MEDIA_CANDIDATES, CollectedMediaCandidate
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator


class FeedDefinition(BaseModel):
    source_id: UUID
    source_feed_id: UUID
    name: str = Field(min_length=1, max_length=255)
    url: AnyHttpUrl
    poll_interval_seconds: int = Field(default=900, ge=60, le=86_400)
    last_polled_at: datetime | None = None
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    max_response_bytes: int = Field(default=2_000_000, ge=1_024, le=20_000_000)
    etag: str | None = None
    last_modified: str | None = None

    @field_validator("url")
    @classmethod
    def reject_embedded_credentials(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username or value.password:
            raise ValueError("feed URL must not contain embedded credentials")
        return value

    @field_validator("last_polled_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("feed timestamps must be timezone-aware")
        return value.astimezone(UTC) if value is not None else None


class CollectedArticle(BaseModel):
    source_id: UUID
    source_feed_id: UUID
    url: AnyHttpUrl
    title: str = Field(min_length=1)
    author: str | None = None
    published_at: datetime | None = None
    language: str | None = None
    summary: str | None = None
    body: str | None = None
    external_id: str | None = None
    media_candidates: tuple[CollectedMediaCandidate, ...] = Field(
        default=(), max_length=MAX_FEED_MEDIA_CANDIDATES
    )


class FeedFetchResult(BaseModel):
    source_feed_id: UUID
    articles: list[CollectedArticle] = Field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    warnings: list[str] = Field(default_factory=list)


class CollectionFailure(BaseModel):
    source_feed_id: UUID
    error_type: str
    message: str


class CollectionBatchResult(BaseModel):
    results: list[FeedFetchResult] = Field(default_factory=list)
    failures: list[CollectionFailure] = Field(default_factory=list)

    @property
    def article_count(self) -> int:
        return sum(len(result.articles) for result in self.results)
