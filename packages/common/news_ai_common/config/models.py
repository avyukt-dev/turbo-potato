"""Shared configuration models."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigDomain(StrEnum):
    """Canonical configuration ownership domains."""

    SOURCES = "sources"
    RESEARCH = "research"
    EDITORIAL = "editorial"
    MODELS = "models"
    PROMPTS = "prompts"
    PLATFORMS = "platforms"
    RUNTIME = "runtime"


class GeneratedMediaStorageBackend(StrEnum):
    LOCAL = "local"
    S3 = "s3"


class S3CompatibleProvider(StrEnum):
    AWS = "aws"
    CLOUDFLARE_R2 = "cloudflare-r2"
    COMPATIBLE = "compatible"


class AppSettings(BaseSettings):
    """Process-level settings supplied by environment or deployment configuration."""

    model_config = SettingsConfigDict(env_prefix="NEWS_AI_", extra="ignore")

    environment: str = "development"
    config_dir: Path = Path("config")
    log_level: str = "INFO"
    service_manager: str | None = None
    database_url: str | None = None
    redis_url: str | None = None
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    review_api_token: SecretStr | None = None
    reviewer_id: UUID | None = None
    review_capabilities: str = "view,review,approve"
    telegram_review_enabled: bool = False
    telegram_bot_token: SecretStr | None = Field(default=None, min_length=1, max_length=256)
    telegram_webhook_secret: SecretStr | None = Field(default=None, min_length=1, max_length=256)
    telegram_review_chat_id: int | None = None
    telegram_reviewer_user_id: int | None = Field(default=None, gt=0)
    media_generation_enabled: bool = False
    generated_media_storage_backend: GeneratedMediaStorageBackend = (
        GeneratedMediaStorageBackend.LOCAL
    )
    generated_media_directory: Path = Path("var/generated-media")
    generated_media_public_base_url: str | None = None
    generated_media_key_prefix: str = ""
    generated_media_s3_provider: S3CompatibleProvider = S3CompatibleProvider.AWS
    generated_media_s3_bucket: str | None = None
    generated_media_s3_region: str | None = None
    generated_media_s3_endpoint_url: str | None = None
    generated_media_s3_access_key_id: SecretStr | None = None
    generated_media_s3_secret_access_key: SecretStr | None = None
    generated_media_s3_session_token: SecretStr | None = None
    generated_media_s3_connect_timeout_seconds: float = Field(default=5, gt=0, le=30)
    generated_media_s3_read_timeout_seconds: float = Field(default=30, gt=0, le=120)
    generated_media_s3_max_attempts: int = Field(default=3, ge=1, le=10)
    generated_media_watermark_text: str = Field(
        default="News AI • AI-generated", min_length=1, max_length=96
    )

    @field_validator("generated_media_public_base_url")
    @classmethod
    def validate_generated_media_public_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("generated media public base URL must use HTTPS")
        return normalized

    @field_validator("generated_media_s3_endpoint_url")
    @classmethod
    def validate_generated_media_s3_endpoint_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("generated media S3 endpoint URL must use HTTPS")
        return normalized

    @field_validator("generated_media_s3_bucket", "generated_media_s3_region")
    @classmethod
    def validate_generated_media_s3_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or "/" in normalized or "\\" in normalized:
            raise ValueError("generated media S3 identity is invalid")
        return normalized

    @field_validator(
        "generated_media_s3_access_key_id",
        "generated_media_s3_secret_access_key",
        "generated_media_s3_session_token",
    )
    @classmethod
    def validate_generated_media_s3_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("generated media S3 credentials must not be blank")
        return value

    @field_validator("generated_media_key_prefix")
    @classmethod
    def validate_generated_media_key_prefix(cls, value: str) -> str:
        normalized = value.strip().strip("/")
        if any(part in {"", ".", ".."} for part in normalized.split("/")) and normalized:
            raise ValueError("generated media key prefix contains an unsafe path component")
        return normalized

    @field_validator("telegram_webhook_secret")
    @classmethod
    def validate_telegram_webhook_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            raw = value.get_secret_value()
            if not all(
                character.isascii() and (character.isalnum() or character in "_-")
                for character in raw
            ):
                raise ValueError("Telegram webhook secret contains unsupported characters")
        return value

    @field_validator("telegram_review_chat_id")
    @classmethod
    def validate_telegram_chat_id(cls, value: int | None) -> int | None:
        if value == 0:
            raise ValueError("Telegram review chat id must not be zero")
        return value

    @model_validator(mode="after")
    def validate_cross_field_configuration(self) -> AppSettings:
        if self.telegram_review_enabled and any(
            item is None
            for item in (
                self.telegram_bot_token,
                self.telegram_webhook_secret,
                self.telegram_review_chat_id,
                self.telegram_reviewer_user_id,
                self.reviewer_id,
            )
        ):
            raise ValueError("Telegram review configuration is incomplete")
        if self.media_generation_enabled and self.generated_media_public_base_url is None:
            raise ValueError(
                "generated media public base URL is required when generation is enabled"
            )
        explicit_access_key = self.generated_media_s3_access_key_id is not None
        explicit_secret = self.generated_media_s3_secret_access_key is not None
        if explicit_access_key != explicit_secret:
            raise ValueError("generated media S3 access key and secret must be configured together")
        if self.generated_media_s3_session_token is not None and not explicit_access_key:
            raise ValueError("generated media S3 session token requires explicit credentials")
        if (
            self.media_generation_enabled
            and self.generated_media_storage_backend is GeneratedMediaStorageBackend.S3
        ):
            if not self.generated_media_s3_bucket or not self.generated_media_s3_region:
                raise ValueError("generated media S3 bucket and region are required")
            if self.generated_media_s3_provider is S3CompatibleProvider.CLOUDFLARE_R2:
                if self.generated_media_s3_region != "auto":
                    raise ValueError("Cloudflare R2 generated media region must be 'auto'")
                if self.generated_media_s3_endpoint_url is None:
                    raise ValueError("Cloudflare R2 generated media endpoint is required")
                if not explicit_access_key:
                    raise ValueError("Cloudflare R2 generated media credentials are required")
            elif self.generated_media_s3_provider is S3CompatibleProvider.AWS:
                if self.generated_media_s3_endpoint_url is not None:
                    raise ValueError("AWS S3 generated media must use the SDK-managed endpoint")
            elif self.generated_media_s3_endpoint_url is None:
                raise ValueError("compatible S3 generated media endpoint is required")
        return self
