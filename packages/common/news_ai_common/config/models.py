"""Shared configuration models."""

from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pydantic import Field, SecretStr
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
