"""Read-only operational snapshots with bounded checks and enum-bucketed aggregates."""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psutil
from news_ai_common.config import ConfigDomain, ConfigLoader
from news_ai_common.runtime.network import dns_available, tailscale_state
from news_ai_database import (
    EventOutbox,
    Job,
    Publication,
    PublicationAttempt,
    SocialAccount,
    create_database_engine,
    create_session_factory,
)
from sqlalchemy import case, func, select, text

from .contracts import HealthCheck, HealthReport, HealthStatus, MonitoringConfig
from .controller import build_controller
from .dependencies import run_dependency_checks
from .publishing import DatabasePublishingControl

PLATFORMS = ("INSTAGRAM", "X", "FACEBOOK", "TELEGRAM")
JOB_STATUSES = (
    "PENDING",
    "RUNNING",
    "FAILED",
    "RETRYING",
    "COMPLETED",
    "SUCCEEDED",
    "SUPERSEDED",
    "CANCELLED",
)
PUBLICATION_STATUSES = (
    "DRAFT",
    "READY_FOR_REVIEW",
    "APPROVED",
    "SCHEDULED",
    "PUBLISHING",
    "RETRYING",
    "BLOCKED",
    "PUBLISHED",
    "FAILED",
    "CANCELLED",
)
ATTEMPT_STATUSES = (
    "IN_PROGRESS",
    "SUCCEEDED",
    "RETRYABLE_FAILED",
    "TERMINAL_FAILED",
    "AMBIGUOUS",
    "BLOCKED",
)
ERROR_CLASSES = (
    "TRANSIENT",
    "PERMANENT",
    "AMBIGUOUS",
)
ACCOUNT_STATUSES = ("ACTIVE", "PAUSED", "AUTH_ERROR", "RATE_LIMITED", "DISABLED", "REAUTH_REQUIRED")


@dataclass(frozen=True)
class MetricSample:
    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OperationalSnapshot:
    report: HealthReport
    samples: tuple[MetricSample, ...]


def system_values(config):
    values = {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage(config.disk_path).percent,
        "uptime_seconds": max(0, time.time() - psutil.boot_time()),
    }
    try:
        temperatures = getattr(psutil, "sensors_temperatures", lambda: {})()
    except (OSError, NotImplementedError):
        temperatures = {}
    readings = [
        sensor.current
        for sensors in temperatures.values()
        for sensor in sensors
        if sensor.current is not None
    ]
    if readings:
        values["temperature_celsius"] = max(readings)
    try:
        battery = getattr(psutil, "sensors_battery", lambda: None)()
    except (OSError, NotImplementedError):
        battery = None
    if battery is not None:
        values["battery_percent"] = battery.percent
        values["power_plugged"] = float(battery.power_plugged)
    return values


def _bucket(column, allowed):
    return case((column.in_(allowed), column), else_="OTHER")


def database_samples(factory, config, now):
    samples = []
    with factory() as session:
        if session.bind.dialect.name == "postgresql":
            session.execute(
                text("SELECT set_config('statement_timeout', :value, true)"),
                {"value": f"{int(config.health_check_timeout_seconds * 1000)}ms"},
            )
        aggregates = (
            ("jobs", Job, (("status", Job.status, JOB_STATUSES),)),
            (
                "outbox_events",
                EventOutbox,
                (("status", EventOutbox.status, ("PENDING", "PUBLISHING", "PUBLISHED", "FAILED")),),
            ),
            (
                "publications",
                Publication,
                (
                    ("status", Publication.status, PUBLICATION_STATUSES),
                    ("platform", Publication.platform, PLATFORMS),
                ),
            ),
            (
                "publication_attempts",
                PublicationAttempt,
                (
                    ("status", PublicationAttempt.status, ATTEMPT_STATUSES),
                    ("error_class", PublicationAttempt.error_class, ERROR_CLASSES),
                ),
            ),
            (
                "social_accounts",
                SocialAccount,
                (
                    ("platform", SocialAccount.platform, PLATFORMS),
                    ("status", SocialAccount.status, ACCOUNT_STATUSES),
                ),
            ),
        )
        for name, model, fields in aggregates:
            buckets = [_bucket(column, allowed) for _, column, allowed in fields]
            for row in session.execute(
                select(*buckets, func.count()).select_from(model).group_by(*buckets)
            ):
                samples.append(
                    MetricSample(
                        name, row[-1], {spec[0]: row[index] for index, spec in enumerate(fields)}
                    )
                )
        overdue = session.scalar(
            select(func.count())
            .select_from(Publication)
            .where(Publication.status == "SCHEDULED", Publication.scheduled_at < now)
        )
        retry_due = session.scalar(
            select(func.count())
            .select_from(Publication)
            .where(Publication.status == "RETRYING", Publication.next_retry_at <= now)
        )
        samples.extend(
            (
                MetricSample("scheduler_overdue_publications", overdue),
                MetricSample("retry_due_publications", retry_due),
            )
        )
    return samples


class HealthMonitor:
    def __init__(
        self,
        settings,
        config,
        controller,
        control,
        *,
        factory=None,
        redis_client=None,
        dependency_probe=run_dependency_checks,
        system_probe=system_values,
        dns_probe=dns_available,
        tailscale_probe=tailscale_state,
    ):
        self.settings, self.config, self.controller, self.control = (
            settings,
            config,
            controller,
            control,
        )
        self.factory, self.redis_client = factory, redis_client
        self.dependency_probe, self.system_probe = dependency_probe, system_probe
        self.dns_probe, self.tailscale_probe = dns_probe, tailscale_probe

    async def collect(self, *, deep=True):
        checks, samples = [], []
        timeout = self.config.health_check_timeout_seconds

        async def bounded(call):
            return await asyncio.wait_for(asyncio.to_thread(call), timeout=timeout)

        def add(name, category, status, summary, *, critical=False, values=None, latency=0):
            checks.append(
                HealthCheck(
                    name=name,
                    category=category,
                    status=status,
                    summary=summary,
                    critical=critical,
                    values=values or {},
                    latency_ms=latency,
                )
            )

        started = time.monotonic()
        try:
            dependencies = await asyncio.wait_for(
                self.dependency_probe(self.settings), timeout=timeout
            )
        except Exception:
            dependencies = {}
        for component in ("postgres", "redis", "ai_router"):
            ready = bool(dependencies.get(component, False))
            samples.append(MetricSample("component_ready", float(ready), {"component": component}))
            add(
                component,
                "DEPENDENCIES",
                HealthStatus.HEALTHY if ready else HealthStatus.UNHEALTHY,
                "available" if ready else "unavailable",
                critical=component in self.config.critical_dependencies,
                latency=(time.monotonic() - started) * 1000,
            )
        try:
            values = await bounded(lambda: self.system_probe(self.config))
            degraded = (
                values.get("memory_percent", 0) >= self.config.memory_warning_percent
                or values.get("disk_percent", 0) >= self.config.disk_warning_percent
            )
            add(
                "resources",
                "SYSTEM",
                HealthStatus.DEGRADED if degraded else HealthStatus.HEALTHY,
                "resource snapshot",
                values=values,
            )
            samples.extend(MetricSample(f"system_{name}", value) for name, value in values.items())
            thermal_required = (
                self.controller.profile
                and self.controller.profile.hints.thermal_monitoring_required
            )
            temperature = values.get("temperature_celsius")
            thermal_status = (
                (HealthStatus.DEGRADED if thermal_required else HealthStatus.NOT_APPLICABLE)
                if temperature is None
                else (
                    HealthStatus.DEGRADED
                    if temperature >= self.config.temperature_warning_celsius
                    else HealthStatus.HEALTHY
                )
            )
            add(
                "temperature",
                "SYSTEM",
                thermal_status,
                "sensor unavailable" if temperature is None else "sensor available",
            )
            add(
                "battery",
                "SYSTEM",
                HealthStatus.HEALTHY
                if "battery_percent" in values
                else HealthStatus.NOT_APPLICABLE,
                "portable sensor availability",
            )
        except Exception:
            add("resources", "SYSTEM", HealthStatus.UNKNOWN, "resource checks unavailable")
        if self.factory:
            try:
                aggregates = await bounded(
                    lambda: database_samples(self.factory, self.config, datetime.now(UTC))
                )
                samples.extend(aggregates)
                for category in ("JOBS", "EVENTS", "PUBLISHING", "SOCIAL"):
                    problem = any(
                        sample.value > 0
                        and sample.labels.get("status")
                        in {"FAILED", "BLOCKED", "AUTH_ERROR", "RATE_LIMITED", "REAUTH_REQUIRED"}
                        for sample in aggregates
                        if sample.name.startswith(
                            {
                                "JOBS": "jobs",
                                "EVENTS": "outbox",
                                "PUBLISHING": "publication",
                                "SOCIAL": "social",
                            }[category]
                        )
                    )
                    add(
                        category.lower(),
                        category,
                        HealthStatus.DEGRADED if problem else HealthStatus.HEALTHY,
                        "durable aggregate snapshot",
                    )
            except Exception:
                add(
                    "durable_aggregates",
                    "JOBS",
                    HealthStatus.UNKNOWN,
                    "durable aggregates unavailable",
                )
        try:
            control = await bounded(self.control.snapshot)
            samples.append(MetricSample("publishing_paused", float(control.effective_pause)))
            add(
                "publishing_control",
                "PUBLISHING",
                HealthStatus.HEALTHY if control.available else HealthStatus.UNHEALTHY,
                "paused" if control.effective_pause else "resumed",
                critical=True,
            )
        except Exception:
            samples.append(MetricSample("publishing_paused", 1))
            add(
                "publishing_control",
                "PUBLISHING",
                HealthStatus.UNHEALTHY,
                "control unavailable; publication denied",
                critical=True,
            )
        if self.redis_client:
            for entry in self.config.streams:
                try:
                    length = await asyncio.wait_for(
                        self.redis_client.xlen(entry.stream), timeout=timeout
                    )
                    samples.append(MetricSample("stream_length", length, {"stream": entry.stream}))
                    groups = await asyncio.wait_for(
                        self.redis_client.xinfo_groups(entry.stream), timeout=timeout
                    )
                    known = {group["name"]: group["pending"] for group in groups}
                    for group in entry.groups:
                        if group in known:
                            samples.append(
                                MetricSample(
                                    "stream_pending",
                                    known[group],
                                    {"stream": entry.stream, "group": group},
                                )
                            )
                except Exception:
                    add("stream", "EVENTS", HealthStatus.UNKNOWN, "stream/group unavailable")
        if deep:
            try:
                ready = await bounded(
                    lambda: self.dns_probe(self.config.dns_target, timeout=timeout)
                )
                add(
                    "dns",
                    "NETWORK",
                    HealthStatus.HEALTHY if ready else HealthStatus.DEGRADED,
                    "DNS resolution check",
                )
            except Exception:
                add("dns", "NETWORK", HealthStatus.DEGRADED, "DNS check unavailable")
            expected = self.config.tailscale_expected or bool(
                self.controller.profile and self.controller.profile.hints.tailscale_expected
            )
            if expected:
                try:
                    state = await bounded(lambda: self.tailscale_probe(timeout=timeout))
                    add(
                        "tailscale",
                        "NETWORK",
                        HealthStatus.HEALTHY if state == "running" else HealthStatus.DEGRADED,
                        "management network availability",
                    )
                except Exception:
                    add(
                        "tailscale",
                        "NETWORK",
                        HealthStatus.DEGRADED,
                        "management network unavailable",
                    )
            if self.config.check_services:
                for entry in self.controller.registry.services:
                    if not entry.enabled or not entry.expected:
                        continue
                    try:
                        service = await bounded(
                            lambda entry=entry: self.controller.operation("status", entry.name)
                        )
                        status = {
                            "active": HealthStatus.HEALTHY,
                            "inactive": HealthStatus.UNHEALTHY,
                            "failed": HealthStatus.UNHEALTHY,
                        }.get(service["state"], HealthStatus.UNKNOWN)
                        if service.get("error_code"):
                            status = HealthStatus.UNKNOWN
                        add(
                            entry.name,
                            "SERVICES",
                            status,
                            "normalized service state",
                            critical=entry.critical,
                        )
                    except Exception:
                        add(
                            entry.name,
                            "SERVICES",
                            HealthStatus.UNKNOWN,
                            "service status unavailable",
                            critical=entry.critical,
                        )
        overall = HealthStatus.HEALTHY
        if any(check.critical and check.status == HealthStatus.UNHEALTHY for check in checks):
            overall = HealthStatus.UNHEALTHY
        elif any(
            check.status in {HealthStatus.UNHEALTHY, HealthStatus.DEGRADED, HealthStatus.UNKNOWN}
            for check in checks
        ):
            overall = HealthStatus.DEGRADED
        return OperationalSnapshot(
            HealthReport(
                status=overall,
                timestamp=datetime.now(UTC).isoformat(),
                runtime_profile=self.controller.profile.profile_id
                if self.controller.profile
                else None,
                service_manager=self.controller.manager.name if deep else "not_probed",
                checks=tuple(checks),
            ),
            tuple(samples),
        )


def build_monitor(settings, *, controller=None, factory=None, redis_client=None):
    config = ConfigLoader(settings.config_dir).load_domain_file(
        ConfigDomain.RUNTIME, "monitoring.yaml", MonitoringConfig
    )
    controller = controller or build_controller(settings)
    factory = factory or (
        create_session_factory(create_database_engine(settings.database_url))
        if settings.database_url
        else None
    )
    if redis_client is None and settings.redis_url:
        from redis.asyncio import Redis

        redis_client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=config.health_check_timeout_seconds,
            socket_timeout=config.health_check_timeout_seconds,
        )
    control = DatabasePublishingControl(
        factory, timeout_seconds=config.health_check_timeout_seconds
    )
    return HealthMonitor(
        settings, config, controller, control, factory=factory, redis_client=redis_client
    )
