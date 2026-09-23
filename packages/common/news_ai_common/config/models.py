"""Shared configuration models."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
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
    def require_complete_telegram_review_configuration(self) -> AppSettings:
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
        return self
