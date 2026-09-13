"""Versioned, bounded lifecycle policy owned by config/runtime/pipeline.yaml."""

from typing import Literal

from news_ai_common.config import ConfigLoader
from pydantic import BaseModel, ConfigDict, Field


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    collector_wake_interval_seconds: float = Field(default=30, ge=0.01, le=3600)
    dispatcher_interval_seconds: float = Field(default=1, ge=0.01, le=60)
    worker_idle_interval_seconds: float = Field(default=0.1, ge=0.01, le=60)
    consumer_block_ms: int = Field(default=1000, ge=1, le=5000)
    pending_recovery_interval_seconds: float = Field(default=30, ge=0.01, le=3600)
    pending_min_idle_ms: int = Field(default=900000, ge=1, le=86400000)
    component_error_backoff_seconds: float = Field(default=5, ge=0.01, le=300)
    max_consecutive_component_failures: int = Field(default=12, ge=1, le=100)
    startup_timeout_seconds: float = Field(default=30, ge=0.01, le=120)
    shutdown_timeout_seconds: float = Field(default=30, ge=0.01, le=300)

    @classmethod
    def load(cls, loader: ConfigLoader) -> "PipelineConfig":
        return cls.model_validate(loader.load_yaml("runtime/pipeline.yaml"))
