"""Typed feed-media discovery contracts shared by collection and normalization."""

from enum import StrEnum

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

MAX_FEED_MEDIA_CANDIDATES = 20


class FeedMediaType(StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"


class FeedMediaOrigin(StrEnum):
    MEDIA_CONTENT = "MEDIA_CONTENT"
    MEDIA_THUMBNAIL = "MEDIA_THUMBNAIL"
    RSS_ENCLOSURE = "RSS_ENCLOSURE"
    ATOM_ENCLOSURE = "ATOM_ENCLOSURE"


class MediaReuseStatus(StrEnum):
    """Collection status only; feed assertions never grant publication rights."""

    UNASSESSED = "UNASSESSED"


class CollectedMediaCandidate(BaseModel):
    """Bounded feed-declared metadata, not evidence or a publishable media asset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: AnyHttpUrl
    media_type: FeedMediaType
    origin: FeedMediaOrigin
    mime_type: str | None = Field(default=None, max_length=128)
    width: int | None = Field(default=None, ge=1, le=100_000)
    height: int | None = Field(default=None, ge=1, le=100_000)
    duration_seconds: int | None = Field(default=None, ge=0, le=604_800)
    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=2_000)
    credit: str | None = Field(default=None, max_length=500)
    copyright_notice: str | None = Field(default=None, max_length=500)
    license_url: AnyHttpUrl | None = None
    license_text: str | None = Field(default=None, max_length=500)
    reuse_status: MediaReuseStatus = MediaReuseStatus.UNASSESSED

    @field_validator("url", "license_url")
    @classmethod
    def reject_embedded_credentials(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is not None and (value.username or value.password):
            raise ValueError("media URLs must not contain embedded credentials")
        return value
