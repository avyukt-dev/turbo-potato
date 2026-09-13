import asyncio
import subprocess
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from news_ai_common.config import AppSettings
from news_ai_common.runtime import RuntimeDetector
from news_ai_common.runtime.base import CommandRunner
from news_ai_common.runtime.managers import (
    OpenRCServiceManager,
    SystemdServiceManager,
    SysVServiceManager,
)
from news_ai_database import (
    AuditLog,
    PublicationAttempt,
    PublicationAttemptPhase,
    PublicationAttemptStatus,
    RuntimeControl,
    SocialAccount,
    SocialAccountStatus,
)
from news_ai_runtime.cli import main
from news_ai_runtime.contracts import (
    HealthReport,
    HealthStatus,
    MonitoringConfig,
    ServiceRegistry,
)
from news_ai_runtime.controller import RuntimeController, RuntimeOperationError, build_controller
from news_ai_runtime.health import HealthMonitor, OperationalSnapshot, database_samples
from news_ai_runtime.metrics import render_metrics
from news_ai_runtime.publishing import DatabasePublishingControl, PublishingControlSnapshot
from pydantic import ValidationError
from sqlalchemy import func, select
from unit.review.test_review_service import _factory


def thread_factory():
    from news_ai_database import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


SENTINEL = "SUPER_SECRET_RUNTIME_TOKEN_123"


class Runner:
    def __init__(self, output="active", code=0, error=None):
        self.output, self.code, self.error = output, code, error
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(argv, self.code, self.output, "")


def controller(manager):
    registry = ServiceRegistry(
        schema_version=1,
        services=[
            {
                "name": "news-api",
                "native_name": "configured-api",
                "service_class": "application",
                "manageable": True,
                "critical": True,
                "expected": True,
            }
        ],
    )
    return RuntimeController(registry, detector=RuntimeDetector(candidates=[manager]))


@pytest.mark.parametrize(
    "output,state",
    [
        ("active", "active"),
        ("inactive", "inactive"),
        ("failed", "failed"),
        ("unknown", "unknown"),
        (SENTINEL, "unknown"),
    ],
)
def test_systemd_exact_status_and_no_verbose_or_secret_output(output, state):
    runner = Runner(output)
    manager = SystemdServiceManager(runner)
    result = manager.status("configured-api")
    assert result.state == state
    assert runner.calls == [["systemctl", "is-active", "configured-api"]]
    assert not result.stdout and not result.stderr and SENTINEL not in repr(result)


@pytest.mark.parametrize("adapter", [OpenRCServiceManager, SysVServiceManager])
@pytest.mark.parametrize(
    "output,state",
    [
        ("started", "active"),
        ("stopped", "inactive"),
        ("not running", "inactive"),
        ("inactive", "inactive"),
        ("crashed", "failed"),
        ("unexpected", "unknown"),
    ],
)
def test_native_minimum_status_parsing(adapter, output, state):
    assert adapter(Runner(output)).status("configured-api").state == state


@pytest.mark.parametrize(
    "adapter,utility",
    [
        (OpenRCServiceManager, "rc-service"),
        (SysVServiceManager, "service"),
        (SystemdServiceManager, "systemctl"),
    ],
)
@pytest.mark.parametrize("action", ["start", "stop", "restart"])
def test_adapter_mutations_use_explicit_safe_argv(adapter, utility, action):
    runner = Runner("started")
    getattr(adapter(runner), action)("configured-api")
    expected = (
        [utility, action, "configured-api"]
        if utility == "systemctl"
        else [utility, "configured-api", action]
    )
    assert runner.calls == [expected]


@pytest.mark.parametrize("action,native", [("enable", "add"), ("disable", "del")])
def test_openrc_boot_registration(monkeypatch, action, native):
    monkeypatch.setattr("news_ai_common.runtime.managers.shutil.which", lambda value: value)
    runner = Runner()
    getattr(OpenRCServiceManager(runner), action)("configured-api")
    assert runner.calls == [["rc-update", native, "configured-api", "default"]]


@pytest.mark.parametrize(
    "error,code",
    [
        (FileNotFoundError(SENTINEL), "UTILITY_MISSING"),
        (PermissionError(SENTINEL), "PERMISSION_DENIED"),
        (subprocess.TimeoutExpired(SENTINEL, 1), "COMMAND_TIMEOUT"),
    ],
)
def test_controller_normalizes_exception_without_operation_fallback(error, code):
    manager = SystemdServiceManager(Runner(error=error))
    runtime = controller(manager)
    runtime._manager = manager
    with pytest.raises(RuntimeOperationError) as caught:
        runtime.operation("start", "news-api")
    assert str(caught.value) == code
    assert SENTINEL not in str(caught.value)
    assert len(manager.runner.calls) == 1


def test_controller_registry_rejects_arbitrary_native_input():
    runtime = controller(SystemdServiceManager(Runner()))
    with pytest.raises(RuntimeOperationError, match="SERVICE_UNKNOWN"):
        runtime.operation("start", "../../foo")
    assert main(["service", "start", SENTINEL], controller=runtime) == 2


def test_command_runner_bounds_output_and_timeout():
    result = CommandRunner().run([sys.executable, "-c", "print('x'*100000)"])
    assert len(result.stdout) == 8192 and result.returncode == 0
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        CommandRunner().run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.02)
    assert caught.value.cmd == "native command"


def test_profile_remains_hint_and_manual_is_safe(monkeypatch):
    monkeypatch.setenv("NEWS_AI_RUNTIME_PROFILE", "poco-beryllium")
    runtime = build_controller(AppSettings(config_dir="config", service_manager="manual"))
    assert runtime.profile.hints.thermal_monitoring_required
    assert runtime.manager.name == "manual"
    services = {entry.name: entry for entry in runtime.registry.services}
    assert services["news-api"].expected and services["news-api"].critical
    assert services["news-scheduler"].expected and not services["news-scheduler"].critical
    assert services["news-publisher"].expected and not services["news-publisher"].critical
    assert not services["news-collector"].expected
    assert runtime.operation("status", "news-publisher")["state"] == "unknown"
    with pytest.raises(RuntimeOperationError, match="UNSUPPORTED_OPERATION"):
        runtime.operation("start", "news-publisher")
    monkeypatch.setenv("NEWS_AI_RUNTIME_PROFILE", "../../foo")
    with pytest.raises(RuntimeOperationError, match="INVALID_PROFILE"):
        build_controller(AppSettings(config_dir="config"))


def test_explicit_unusable_native_override_does_not_fall_back(monkeypatch):
    from news_ai_common.runtime import ServiceManager, ServiceResult, ServiceState

    class Manager(ServiceManager):
        def __init__(self, name, available):
            super().__init__()
            self.name, self.available = name, available

        def probe(self):
            return self.available

        def status(self, service):
            return ServiceResult(ServiceState.ACTIVE, 0)

        start = stop = status

    monkeypatch.delenv("NEWS_AI_RUNTIME_PROFILE", raising=False)
    monkeypatch.setattr(
        "news_ai_runtime.controller.OpenRCServiceManager", lambda: Manager("openrc", False)
    )
    monkeypatch.setattr(
        "news_ai_runtime.controller.SystemdServiceManager", lambda: Manager("systemd", True)
    )
    monkeypatch.setattr(
        "news_ai_runtime.controller.SysVServiceManager", lambda: Manager("sysv", True)
    )
    runtime = build_controller(AppSettings(config_dir="config", service_manager="openrc"))

    with pytest.raises(RuntimeOperationError, match="UNSUPPORTED_MANAGER"):
        _ = runtime.manager


@pytest.mark.parametrize(
    "model,value",
    [
        (MonitoringConfig, {"schema_version": 2}),
        (MonitoringConfig, {"schema_version": 1, "unknown": True}),
        (
            ServiceRegistry,
            {
                "schema_version": 1,
                "services": [
                    {"name": "../unsafe", "native_name": "x", "service_class": "application"}
                ],
            },
        ),
    ],
)
def test_operational_config_is_closed_and_versioned(model, value):
    with pytest.raises(ValidationError):
        model.model_validate(value)


def test_control_idempotency_environment_pause_and_redacted_audit(monkeypatch, capsys):
    factory = _factory()
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")
    control = DatabasePublishingControl(factory)
    assert control.snapshot().effective_pause and not control.snapshot().available
    control.set_paused(False, reason="resume " + SENTINEL)
    control.set_paused(False, reason="same no-op")
    assert control.snapshot().revision == 1 and not control.paused()
    monkeypatch.delenv("NEWS_AI_PUBLISHING_PAUSED")
    assert not control.paused()
    control.set_paused(True, reason="maintenance")
    control.set_paused(True, reason="no-op")
    assert control.snapshot().revision == 2
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "true")
    assert main(["publish", "resume", "--reason", "complete"], control=control) == 0
    assert control.paused() and not control.snapshot().database_pause
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 3
        assert SENTINEL not in str(session.scalar(select(RuntimeControl)).reason)
        assert SENTINEL not in str([row.reason for row in session.scalars(select(AuditLog))])
    assert SENTINEL not in capsys.readouterr().out


@pytest.mark.parametrize("failed", [None, "postgres", "redis", "ai_router"])
def test_health_aggregation_metrics_and_no_secret_leakage(monkeypatch, failed):
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")
    factory = thread_factory()
    control = DatabasePublishingControl(factory)
    control.set_paused(False, reason="ready")
    runtime = build_controller(AppSettings(service_manager="manual"))

    async def dependencies(settings):
        return {name: name != failed for name in ("postgres", "redis", "ai_router")}

    monitor = HealthMonitor(
        AppSettings(environment="test"),
        MonitoringConfig(schema_version=1),
        runtime,
        control,
        factory=factory,
        dependency_probe=dependencies,
        system_probe=lambda config: {"cpu_percent": 1, "memory_percent": 2, "disk_percent": 3},
        dns_probe=lambda *args, **kwargs: True,
    )
    snapshot = asyncio.run(monitor.collect())
    assert snapshot.report.status == (HealthStatus.UNHEALTHY if failed else HealthStatus.DEGRADED)
    # Manual expected services have unknown state, rather than a guessed host command.
    services = {
        check.name: check for check in snapshot.report.checks if check.category == "SERVICES"
    }
    assert {
        "news-api",
        "news-scheduler",
        "news-publisher",
        "postgres",
        "redis",
    } <= services.keys()
    assert all(check.status == HealthStatus.UNKNOWN for check in services.values())
    assert "news-collector" not in services
    assert SENTINEL not in snapshot.report.model_dump_json()
    exposition = render_metrics(snapshot).decode()
    assert 'news_ai_component_ready{component="postgres"}' in exposition
    assert SENTINEL not in exposition


def test_publication_attempt_error_classes_and_platforms_are_bounded():
    factory = thread_factory()
    now = datetime.now(UTC)
    with factory() as session, session.begin():
        for index, error_class in enumerate(
            ("TRANSIENT", "PERMANENT", "AMBIGUOUS", SENTINEL), start=1
        ):
            session.add(
                PublicationAttempt(
                    publication_id=uuid4(),
                    attempt_number=index,
                    status=PublicationAttemptStatus.BLOCKED,
                    phase=PublicationAttemptPhase.COMPLETE,
                    execution_request_hash=f"{index:x}" * 64,
                    error_code="AUTHENTICATION",
                    error_class=error_class,
                    error_message=SENTINEL,
                    retryable=False,
                    ambiguous=error_class == "AMBIGUOUS",
                    started_at=now,
                    completed_at=now,
                    lease_token=uuid4(),
                    lease_expires_at=now,
                )
            )
        for platform in ("TELEGRAM", SENTINEL):
            session.add(
                SocialAccount(
                    platform=platform,
                    account_name=platform,
                    account_identifier=platform,
                    status=SocialAccountStatus.ACTIVE,
                )
            )

    samples = database_samples(factory, MonitoringConfig(schema_version=1), now)
    report = HealthReport(
        status=HealthStatus.HEALTHY,
        timestamp=now.isoformat(),
        runtime_profile=None,
        service_manager="manual",
        checks=(),
    )
    payload = render_metrics(OperationalSnapshot(report, tuple(samples))).decode()

    for error_class in ("TRANSIENT", "PERMANENT", "AMBIGUOUS", "OTHER"):
        assert f'error_class="{error_class}"' in payload
    assert 'error_class="AUTHENTICATION"' not in payload
    assert 'platform="TELEGRAM"' in payload
    assert 'platform="OTHER"' in payload
    assert SENTINEL not in payload


def test_expected_application_services_are_health_checked_read_only():
    registry = ServiceRegistry.model_validate(
        {
            "schema_version": 1,
            "services": [
                {
                    "name": "news-api",
                    "native_name": "news-api",
                    "service_class": "application",
                    "expected": True,
                    "critical": True,
                },
                {
                    "name": "news-scheduler",
                    "native_name": "news-scheduler",
                    "service_class": "application",
                    "expected": True,
                },
                {
                    "name": "news-publisher",
                    "native_name": "news-publisher",
                    "service_class": "application",
                    "expected": True,
                },
                {
                    "name": "news-collector",
                    "native_name": "news-collector",
                    "service_class": "application",
                    "expected": False,
                    "critical": True,
                },
            ],
        }
    )
    calls = []

    class ReadOnlyController:
        profile = None
        manager = SimpleNamespace(name="fake")

        def __init__(self):
            self.registry = registry

        def operation(self, action, name):
            calls.append((action, name))
            return {
                "state": {
                    "news-api": "active",
                    "news-scheduler": "inactive",
                    "news-publisher": "failed",
                }[name],
                "error_code": None,
            }

    async def dependencies(settings):
        return {name: True for name in ("postgres", "redis", "ai_router")}

    control = SimpleNamespace(
        snapshot=lambda: PublishingControlSnapshot(
            environment_pause=False,
            database_pause=False,
            effective_pause=False,
            available=True,
        )
    )
    monitor = HealthMonitor(
        AppSettings(),
        MonitoringConfig(schema_version=1),
        ReadOnlyController(),
        control,
        dependency_probe=dependencies,
        system_probe=lambda config: {},
        dns_probe=lambda *args, **kwargs: True,
    )

    report = asyncio.run(monitor.collect()).report
    services = {check.name: check for check in report.checks if check.category == "SERVICES"}

    assert services["news-api"].status == HealthStatus.HEALTHY
    assert services["news-scheduler"].status == HealthStatus.UNHEALTHY
    assert services["news-publisher"].status == HealthStatus.UNHEALTHY
    assert "news-collector" not in services
    assert report.status == HealthStatus.DEGRADED
    assert calls == [
        ("status", "news-api"),
        ("status", "news-scheduler"),
        ("status", "news-publisher"),
    ]


def test_critical_expected_application_failure_is_unhealthy():
    manager = SystemdServiceManager(Runner("failed", code=3))
    runtime = controller(manager)
    runtime._manager = manager

    async def dependencies(settings):
        return {name: True for name in ("postgres", "redis", "ai_router")}

    control = SimpleNamespace(
        snapshot=lambda: PublishingControlSnapshot(
            environment_pause=False,
            database_pause=False,
            effective_pause=False,
            available=True,
        )
    )
    monitor = HealthMonitor(
        AppSettings(),
        MonitoringConfig(schema_version=1),
        runtime,
        control,
        dependency_probe=dependencies,
        system_probe=lambda config: {},
        dns_probe=lambda *args, **kwargs: True,
    )

    report = asyncio.run(monitor.collect()).report

    assert next(check for check in report.checks if check.name == "news-api").status == (
        HealthStatus.UNHEALTHY
    )
    assert report.status == HealthStatus.UNHEALTHY


def test_health_timeout_and_optional_thermal_requirement(monkeypatch):
    monkeypatch.setenv("NEWS_AI_RUNTIME_PROFILE", "poco-beryllium")
    runtime = build_controller(AppSettings(service_manager="manual"))

    async def dependencies(settings):
        return {name: True for name in ("postgres", "redis", "ai_router")}

    control = SimpleNamespace(snapshot=lambda: (_ for _ in ()).throw(RuntimeError(SENTINEL)))
    monitor = HealthMonitor(
        AppSettings(),
        MonitoringConfig(schema_version=1),
        runtime,
        control,
        dependency_probe=dependencies,
        system_probe=lambda config: {},
        dns_probe=lambda *a, **kw: False,
    )
    snapshot = asyncio.run(monitor.collect())
    assert snapshot.report.status == HealthStatus.UNHEALTHY
    assert (
        next(check for check in snapshot.report.checks if check.name == "temperature").status
        == HealthStatus.DEGRADED
    )
    assert SENTINEL not in snapshot.report.model_dump_json()
    assert main(["health"], monitor=monitor) == 2


@pytest.mark.parametrize("action", ["enable", "disable"])
def test_systemd_boot_registration_and_sysv_unsupported(action):
    runner = Runner()
    getattr(SystemdServiceManager(runner), action)("configured-api")
    assert runner.calls == [["systemctl", action, "configured-api"]]
    from news_ai_common.runtime import UnsupportedOperation

    with pytest.raises(UnsupportedOperation):
        getattr(SysVServiceManager(runner), action)("configured-api")


@pytest.mark.parametrize(
    "manager", [OpenRCServiceManager, SystemdServiceManager, SysVServiceManager]
)
def test_missing_utilities_do_not_select_native_manager(monkeypatch, manager):
    monkeypatch.setattr("news_ai_common.runtime.managers.shutil.which", lambda name: None)
    runner = Runner()
    assert not manager(runner).probe()
    assert not runner.calls


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_cli_rejects_missing_reason(action, capsys):
    assert main(["publish", action], control=SimpleNamespace()) == 2
    assert "REASON_REQUIRED" in capsys.readouterr().out


def test_runtime_alias_service_list_and_unknown_service_are_safe(capsys):
    runtime = build_controller(AppSettings(service_manager="manual"))
    assert main(["runtime"], controller=runtime) == 0
    assert main(["runtime", "detect"], controller=runtime) == 0
    assert main(["service", "list"], controller=runtime) == 0
    assert main(["service", "status", "news-api"], controller=runtime) == 0
    assert main(["service", "start", "news-api"], controller=runtime) == 2
    assert SENTINEL not in capsys.readouterr().out


@pytest.mark.parametrize("memory,status,exit_code", [(20, "HEALTHY", 0), (95, "DEGRADED", 1)])
def test_health_cli_healthy_and_degraded_exit_codes(monkeypatch, memory, status, exit_code):
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")
    factory = thread_factory()
    control = DatabasePublishingControl(factory)
    control.set_paused(False, reason="ready")

    async def dependencies(settings):
        return {name: True for name in ("postgres", "redis", "ai_router")}

    monitor = HealthMonitor(
        AppSettings(),
        MonitoringConfig(schema_version=1, check_services=False),
        build_controller(AppSettings(service_manager="manual")),
        control,
        dependency_probe=dependencies,
        system_probe=lambda config: {"memory_percent": memory},
        dns_probe=lambda *a, **kw: True,
    )
    assert asyncio.run(monitor.collect()).report.status == status
    assert main(["health"], monitor=monitor) == exit_code


def test_metrics_outage_is_valid_and_http_invokes_no_native_command(monkeypatch):
    from fastapi.testclient import TestClient
    from news_ai_api.main import create_app

    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP must not execute host utilities")

    monkeypatch.setattr(CommandRunner, "run", forbidden)
    app = create_app(AppSettings(environment="test", database_url=None, redis_url=None))
    with TestClient(app) as client:
        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert 'news_ai_component_ready{component="postgres"} 0.0' in metrics.text
        assert 'news_ai_component_ready{component="redis"} 0.0' in metrics.text
        assert "news_ai_publishing_paused 1.0" in metrics.text
        assert SENTINEL not in metrics.text
        assert client.get("/health").json() == {"status": "ok"}
        ready = client.get("/ready")
        assert ready.status_code == 503
        assert set(ready.json()) == {"status", "postgres", "redis", "ai_router"}


def test_health_check_timeout_is_sanitized():
    from news_ai_runtime.publishing import PublishingControlSnapshot

    async def unavailable(settings):
        await asyncio.sleep(1)
        raise RuntimeError(SENTINEL)

    control = SimpleNamespace(
        snapshot=lambda: PublishingControlSnapshot(
            environment_pause=False, database_pause=False, effective_pause=False, available=True
        )
    )
    monitor = HealthMonitor(
        AppSettings(),
        MonitoringConfig(schema_version=1, health_check_timeout_seconds=0.02, check_services=False),
        build_controller(AppSettings(service_manager="manual")),
        control,
        dependency_probe=unavailable,
        system_probe=lambda config: {},
        dns_probe=lambda *a, **kw: True,
    )
    snapshot = asyncio.run(monitor.collect())
    assert snapshot.report.status == "UNHEALTHY"
    assert SENTINEL not in snapshot.report.model_dump_json()
