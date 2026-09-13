"""Closed, typed, runtime-owned operational configuration."""

from enum import StrEnum
from typing import Literal

from news_ai_common.runtime.base import validate_service_name
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServiceEntry(ClosedModel):
    name: str = Field(max_length=64)
    native_name: str = Field(max_length=64)
    service_class: Literal["application", "infrastructure"]
    critical: bool = False
    manageable: bool = False
    expected: bool = False
    enabled: bool = True

    @field_validator("name", "native_name")
    @classmethod
    def safe_identifier(cls, value):
        return validate_service_name(value)


class ServiceRegistry(ClosedModel):
    schema_version: Literal[1]
    services: tuple[ServiceEntry, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def unique_names(self):
        if len({entry.name for entry in self.services}) != len(self.services):
            raise ValueError("duplicate logical services")
        return self


class ProfileHints(ClosedModel):
    architecture: str
    preferred_service_managers: tuple[Literal["openrc", "systemd", "sysv", "manual"], ...]
    battery_available: bool = False
    thermal_monitoring_required: bool = False
    tailscale_expected: bool = False


class ProfileRules(ClosedModel):
    capability_detection_is_authoritative: Literal[True]
    profile_must_not_force_unavailable_utility: Literal[True]


class Device(ClosedModel):
    family: str
    codename: str


class RuntimeProfile(ClosedModel):
    schema_version: Literal[1]
    profile_id: str
    device: Device
    resource_class: str
    hints: ProfileHints
    rules: ProfileRules


class StreamCheck(ClosedModel):
    stream: Literal[
        "news:articles",
        "news:stories",
        "news:evidence",
        "news:content",
        "news:publishing",
        "news:jobs",
    ]
    groups: tuple[
        Literal[
            "normalizer",
            "processor",
            "claim-worker",
            "research-worker",
            "factcheck-worker",
            "research-planner",
            "evidence-collector",
            "fact-checker",
            "story-verifier",
            "fact-sheet-builder",
            "content-worker",
            "quality-worker",
            "publisher",
        ],
        ...,
    ]


class MonitoringConfig(ClosedModel):
    schema_version: Literal[1]
    critical_dependencies: tuple[Literal["postgres", "redis", "ai_router"], ...] = (
        "postgres",
        "redis",
        "ai_router",
    )
    health_check_timeout_seconds: float = Field(default=5, gt=0, le=30)
    disk_path: str = "."
    disk_warning_percent: float = Field(default=90, gt=0, le=100)
    memory_warning_percent: float = Field(default=90, gt=0, le=100)
    temperature_warning_celsius: float = Field(default=75, ge=0, le=150)
    dns_target: str = Field(default="localhost", pattern=r"^[A-Za-z0-9.-]{1,253}$")
    tailscale_expected: bool = False
    check_services: bool = True
    streams: tuple[StreamCheck, ...] = Field(default=(), max_length=6)

    @model_validator(mode="after")
    def unique_streams(self):
        if len({entry.stream for entry in self.streams}) != len(self.streams):
            raise ValueError("duplicate streams")
        return self


class HealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class HealthCheck(ClosedModel):
    name: str
    category: str
    status: HealthStatus
    critical: bool = False
    latency_ms: float = Field(default=0, ge=0)
    summary: str
    values: dict[str, float] = Field(default_factory=dict)


class HealthReport(ClosedModel):
    status: HealthStatus
    timestamp: str
    runtime_profile: str | None
    service_manager: str
    checks: tuple[HealthCheck, ...]
