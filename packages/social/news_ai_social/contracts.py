"""Provider-neutral social adapter and immutable Instagram command contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from news_ai_content import ContentFormat, ContentPlatform
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator


class SocialMode(StrEnum):
    MOCK = "MOCK"
    LIVE = "LIVE"


class SocialMediaType(StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"


class SocialPublishStatus(StrEnum):
    CREATED = "CREATED"
    PUBLISHED = "PUBLISHED"


class PublicationVerificationStatus(StrEnum):
    PUBLISHED = "PUBLISHED"
    PROCESSING = "PROCESSING"
    FAILED = "FAILED"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"


class SocialCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: bool = False
    image: bool = False
    video: bool = False
    carousel: bool = False
    reel: bool = False
    scheduled_publish: bool = False


class InstagramMediaItem(BaseModel):
    """One ordered, externally deliverable media reference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position: int = Field(ge=1)
    public_url: AnyHttpUrl
    media_type: SocialMediaType


class InstagramCarouselArtifact(BaseModel):
    """Already-authorized presentation input; approval remains a caller responsibility."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content_variant_id: UUID
    content_variant_version: int = Field(ge=1)
    platform: ContentPlatform
    format: ContentFormat
    caption: str = Field(min_length=1)
    hashtags: tuple[str, ...] = ()
    media_items: tuple[InstagramMediaItem, ...] = Field(min_length=1)
    correlation_id: UUID | None = None
    operation_id: UUID | None = None

    @field_validator("hashtags")
    @classmethod
    def validate_hashtags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("hashtags must be unique")
        if any(len(item) < 2 or not item.startswith("#") for item in value):
            raise ValueError("hashtags must be non-empty and start with #")
        return value


class InstagramCarouselRequest(InstagramCarouselArtifact):
    """Validated transport-facing Instagram carousel request."""

    @model_validator(mode="after")
    def ordered_media(self) -> InstagramCarouselRequest:
        positions = tuple(item.position for item in self.media_items)
        if positions != tuple(range(1, len(self.media_items) + 1)):
            raise ValueError("media item positions must be consecutive from 1")
        return self

    @property
    def graph_caption(self) -> str:
        if not self.hashtags:
            return self.caption
        return f"{self.caption}\n\n{' '.join(self.hashtags)}"


class InstagramPublishResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: ContentPlatform = ContentPlatform.INSTAGRAM
    status: SocialPublishStatus
    external_post_id: str = Field(min_length=1)
    external_url: AnyHttpUrl | None = None
    mock: bool = False
    provider_metadata: dict[str, str | int | bool] = Field(default_factory=dict)


class PublicationVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: ContentPlatform = ContentPlatform.INSTAGRAM
    external_post_id: str = Field(min_length=1)
    status: PublicationVerificationStatus
    external_url: AnyHttpUrl | None = None
    mock: bool = False
    provider_metadata: dict[str, str | int | bool] = Field(default_factory=dict)


@runtime_checkable
class SocialPlatformAdapter(Protocol):
    @property
    def capabilities(self) -> SocialCapabilities: ...

    def validate_content(self, request: InstagramCarouselRequest) -> None: ...

    async def publish(self, request: InstagramCarouselRequest) -> InstagramPublishResult: ...

    async def verify_publication(self, external_post_id: str) -> PublicationVerificationResult: ...
