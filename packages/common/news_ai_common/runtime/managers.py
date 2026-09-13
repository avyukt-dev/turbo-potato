"""Native service-manager adapters.

This is the only layer allowed to know native utility command syntax. Application/domain packages
must depend on ServiceManager instead.
"""

import re
import shutil
from pathlib import Path

from .base import (
    ServiceManager,
    ServiceResult,
    ServiceState,
    UnsupportedOperation,
    validate_service_name,
)


def _state_from_text(text: str, returncode: int) -> ServiceState:
    normalized = text.strip().lower()
    if re.search(r"\b(stopped|inactive|not running|not started|not active)\b", normalized):
        return ServiceState.INACTIVE
    if re.search(r"\b(failed|crashed)\b", normalized):
        return ServiceState.FAILED
    if returncode == 0 and re.search(r"\b(started|running|active)\b", normalized):
        return ServiceState.ACTIVE
    return ServiceState.UNKNOWN


def _result_from_process(result: object, text: str) -> ServiceResult:
    return ServiceResult(
        _state_from_text(text, result.returncode),
        result.returncode,
        error_code=_error_from_process(result),
    )


def _error_from_process(result):
    if not result.returncode:
        return None
    output = f"{result.stdout}\n{result.stderr}".lower()
    if any(
        term in output for term in ("permission denied", "access denied", "authentication required")
    ):
        return "PERMISSION_DENIED"
    if any(term in output for term in ("not found", "does not exist", "unrecognized service")):
        return "SERVICE_UNKNOWN"
    return None


class OpenRCServiceManager(ServiceManager):
    name = "openrc"

    def probe(self) -> bool:
        if not shutil.which("rc-service") or not shutil.which("rc-status"):
            return False
        result = self.runner.run(["rc-status", "--all"])
        return result.returncode == 0

    def _call(self, service: str, action: str) -> ServiceResult:
        name = validate_service_name(service)
        result = self.runner.run(["rc-service", name, action])
        text = f"{result.stdout}\n{result.stderr}"
        return _result_from_process(result, text)

    def status(self, service: str) -> ServiceResult:
        return self._call(service, "status")

    def start(self, service: str) -> ServiceResult:
        return self._call(service, "start")

    def stop(self, service: str) -> ServiceResult:
        return self._call(service, "stop")

    def restart(self, service: str) -> ServiceResult:
        return self._call(service, "restart")

    def _boot_registration(self, service: str, action: str) -> ServiceResult:
        if not shutil.which("rc-update"):
            raise FileNotFoundError("native boot registration utility missing")
        name = validate_service_name(service)
        result = self.runner.run(["rc-update", action, name, "default"])
        return ServiceResult(
            ServiceState.UNKNOWN,
            result.returncode,
            error_code=_error_from_process(result),
        )

    def enable(self, service: str) -> ServiceResult:
        return self._boot_registration(service, "add")

    def disable(self, service: str) -> ServiceResult:
        return self._boot_registration(service, "del")


class SystemdServiceManager(ServiceManager):
    name = "systemd"

    def probe(self) -> bool:
        if not shutil.which("systemctl"):
            return False
        result = self.runner.run(["systemctl", "is-system-running"])
        state = result.stdout.strip().lower()
        return state in {"running", "degraded", "starting", "maintenance", "stopping"}

    def _call(self, service: str, action: str) -> ServiceResult:
        name = validate_service_name(service)
        if action == "status":
            active = self.runner.run(["systemctl", "is-active", name])
            state = {
                "active": ServiceState.ACTIVE,
                "inactive": ServiceState.INACTIVE,
                "failed": ServiceState.FAILED,
            }.get(active.stdout.strip(), ServiceState.UNKNOWN)
            return ServiceResult(state, active.returncode, error_code=_error_from_process(active))
        result = self.runner.run(["systemctl", action, name])
        return ServiceResult(
            ServiceState.UNKNOWN, result.returncode, error_code=_error_from_process(result)
        )

    def status(self, service: str) -> ServiceResult:
        return self._call(service, "status")

    def start(self, service: str) -> ServiceResult:
        return self._call(service, "start")

    def stop(self, service: str) -> ServiceResult:
        return self._call(service, "stop")

    def restart(self, service: str) -> ServiceResult:
        return self._call(service, "restart")

    def enable(self, service: str) -> ServiceResult:
        return self._call(service, "enable")

    def disable(self, service: str) -> ServiceResult:
        return self._call(service, "disable")


class SysVServiceManager(ServiceManager):
    name = "sysv"

    def probe(self) -> bool:
        return bool(shutil.which("service") and Path("/etc/init.d").is_dir())

    def _call(self, service: str, action: str) -> ServiceResult:
        name = validate_service_name(service)
        result = self.runner.run(["service", name, action])
        text = f"{result.stdout}\n{result.stderr}"
        return _result_from_process(result, text)

    def status(self, service: str) -> ServiceResult:
        return self._call(service, "status")

    def start(self, service: str) -> ServiceResult:
        return self._call(service, "start")

    def stop(self, service: str) -> ServiceResult:
        return self._call(service, "stop")

    def restart(self, service: str) -> ServiceResult:
        return self._call(service, "restart")

    def enable(self, service: str) -> ServiceResult:
        raise UnsupportedOperation(
            "SysV enablement is distribution-specific and intentionally not guessed"
        )

    def disable(self, service: str) -> ServiceResult:
        raise UnsupportedOperation(
            "SysV disablement is distribution-specific and intentionally not guessed"
        )


class ManualServiceManager(ServiceManager):
    name = "manual"

    def probe(self) -> bool:
        return True

    def _unsupported(self, service: str) -> ServiceResult:
        validate_service_name(service)
        raise UnsupportedOperation("no supported native service manager was detected")

    status = start = stop = restart = enable = disable = _unsupported
