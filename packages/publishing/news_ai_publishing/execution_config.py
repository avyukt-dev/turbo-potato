"""Typed platform-owned publisher policy; no secret or host-command configuration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PublisherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    consumer_group: Literal["publisher"] = "publisher"
    consumer_name_prefix: str = Field(default="publisher", min_length=1, max_length=64)
    batch_size: int = Field(default=10, ge=1, le=1000)
    block_ms: int = Field(default=5000, ge=0, le=60000)
    pending_reclaim_idle_ms: int = Field(default=7200000, ge=1)
    lease_seconds: int = Field(default=7200, ge=1, le=86400)
    max_attempts: int = Field(default=6, ge=1, le=100)
    retry_delays_seconds: tuple[int, ...] = (30, 120, 600, 1800, 7200)
    jitter_ratio: float = Field(default=0.2, ge=0, le=1)
    retry_batch_size: int = Field(default=50, ge=1, le=1000)
    verification_poll_interval_seconds: int = Field(default=60, ge=1, le=3600)
    verification_max_attempts: int = Field(default=5, ge=1, le=100)
    verification_timeout_seconds: int = Field(default=300, ge=1, le=86400)

    @model_validator(mode="after")
    def valid_delays(self):
        if len(self.retry_delays_seconds) != self.max_attempts - 1:
            raise ValueError("one retry delay is required for each retry before exhaustion")
        if any(value < 1 for value in self.retry_delays_seconds):
            raise ValueError("retry delays must be positive")
        return self
