"""Typed Instagram platform configuration and environment-only social secrets."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from news_ai_common.config import ConfigDomain, ConfigLoader
from news_ai_content import ContentFormat
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from .contracts import (
    SocialCapabilities,
    SocialMediaFormat,
    SocialMediaMimeType,
    SocialMediaType,
    SocialMode,
)


class InstagramPollingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interval_seconds: float = Field(gt=0, le=60)
    max_attempts: int = Field(ge=1, le=300)


class InstagramConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_carousel_items: int = Field(ge=2)
    max_carousel_items: int = Field(ge=2, le=20)
    caption_max_characters: int = Field(ge=1, le=10000)
    allowed_media_types: frozenset[SocialMediaType]
    allowed_image_formats: frozenset[SocialMediaFormat]
    allowed_image_mime_types: frozenset[SocialMediaMimeType]

    @model_validator(mode="after")
    def valid_range(self) -> InstagramConstraints:
        if self.min_carousel_items > self.max_carousel_items:
            raise ValueError("minimum carousel items exceeds maximum")
        if not self.allowed_media_types:
            raise ValueError("at least one media type must be allowed")
        if self.allowed_image_formats != frozenset({SocialMediaFormat.JPEG}):
            raise ValueError("Stage 24 supports only ordinary JPEG image format")
        if self.allowed_image_mime_types != frozenset({SocialMediaMimeType.JPEG}):
            raise ValueError("Stage 24 supports only image/jpeg MIME type")
        return self


class InstagramPlatformConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    enabled: bool
    graph_base_url: AnyHttpUrl
    api_version: str = Field(pattern=r"^v[1-9][0-9]*\.0$")
    supported_formats: frozenset[ContentFormat]
    capabilities: SocialCapabilities
    required_permissions: frozenset[str] = Field(min_length=1)
    request_timeout_seconds: float = Field(gt=0, le=120)
    live_environments: frozenset[str] = Field(min_length=1)
    polling: InstagramPollingConfig
    constraints: InstagramConstraints

    @field_validator("live_environments", mode="after")
    @classmethod
    def normalized_environments(cls, value: frozenset[str]) -> frozenset[str]:
        normalized = frozenset(item.strip().lower() for item in value)
        if any(not item or re.fullmatch(r"[a-z][a-z0-9_-]*", item) is None for item in normalized):
            raise ValueError("live environments must be simple non-empty names")
        return normalized

    @model_validator(mode="after")
    def canonical_stage_24_capabilities(self) -> InstagramPlatformConfig:
        if self.graph_base_url.scheme != "https":
            raise ValueError("Instagram Graph base URL must use HTTPS")
        if self.graph_base_url.host != "graph.instagram.com":
            raise ValueError("Instagram Graph base URL must use the official Graph host")
        if self.graph_base_url.query is not None or self.graph_base_url.fragment is not None:
            raise ValueError("Instagram Graph base URL cannot contain query or fragment")
        if self.supported_formats != frozenset({ContentFormat.CAROUSEL}):
            raise ValueError("Stage 24 supports only Instagram carousel format")
        if not self.capabilities.carousel or not self.capabilities.image:
            raise ValueError("Stage 24 requires carousel and image capabilities")
        if self.capabilities.video or self.capabilities.reel or self.capabilities.scheduled_publish:
            raise ValueError("configuration advertises unimplemented Instagram capability")
        required = {"instagram_business_basic", "instagram_business_content_publish"}
        if not required.issubset(self.required_permissions):
            raise ValueError("Instagram Login publishing permissions are incomplete")
        return self


class SocialSettings(BaseSettings):
    """Process configuration; the token remains redacted by Pydantic's secret type."""

    model_config = SettingsConfigDict(env_prefix="NEWS_AI_", extra="ignore")

    environment: str = "development"
    social_mode: SocialMode = SocialMode.MOCK
    publishing_enabled: bool = False
    instagram_account_id: str | None = Field(default=None, pattern=r"^[0-9]+$")
    instagram_access_token: SecretStr | None = None


def load_instagram_config(root: str | Path) -> InstagramPlatformConfig:
    return ConfigLoader(root).load_domain_file(
        ConfigDomain.PLATFORMS, "instagram.yaml", InstagramPlatformConfig
    )
