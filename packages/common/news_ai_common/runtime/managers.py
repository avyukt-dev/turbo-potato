"""Native service-manager adapters.

This is the only layer allowed to know native utility command syntax. Application/domain packages
must depend on ServiceManager instead.
"""

from pathlib import Path
import shutil

from .base import ServiceManager, ServiceResult, ServiceState, UnsupportedOperation, validate_service_name


def _state_from_text(text: str, returncode: int) -> ServiceState:
    normalized = text.lower()
    if returncode == 0 and any(word in normalized for word in ("started", "running", "active")):
        return ServiceState.ACTIVE
    if any(word in normalized for word in ("stopped", "inactive", "not running", "not started")):
        return ServiceState.INACTIVE
    if any(word in normalized for word in ("failed", "crashed")):
        return ServiceState.FAILED
    return ServiceState.UNKNOWN


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
        return ServiceResult(_state_from_text(text, result.returncode), result.returncode, result.stdout, result.stderr)

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
            raise UnsupportedOperation("OpenRC boot registration requires rc-update")
        name = validate_service_name(service)
        result = self.runner.run(["rc-update", action, name, "default"])
        return ServiceResult(ServiceState.UNKNOWN, result.returncode, result.stdout, result.stderr)

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
        result = self.runner.run(["systemctl", action, name])
        text = f"{result.stdout}\n{result.stderr}"
        state = _state_from_text(text, result.returncode)
        if action == "status":
            active = self.runner.run(["systemctl", "is-active", name])
            if active.stdout.strip() == "active":
                state = ServiceState.ACTIVE
            elif active.stdout.strip() == "inactive":
                state = ServiceState.INACTIVE
            elif active.stdout.strip() == "failed":
                state = ServiceState.FAILED
        return ServiceResult(state, result.returncode, result.stdout, result.stderr)

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
        return ServiceResult(_state_from_text(text, result.returncode), result.returncode, result.stdout, result.stderr)

    def status(self, service: str) -> ServiceResult:
        return self._call(service, "status")

    def start(self, service: str) -> ServiceResult:
        return self._call(service, "start")

    def stop(self, service: str) -> ServiceResult:
        return self._call(service, "stop")

    def restart(self, service: str) -> ServiceResult:
        return self._call(service, "restart")

    def enable(self, service: str) -> ServiceResult:
        raise UnsupportedOperation("SysV enablement is distribution-specific and intentionally not guessed")

    def disable(self, service: str) -> ServiceResult:
        raise UnsupportedOperation("SysV disablement is distribution-specific and intentionally not guessed")


class ManualServiceManager(ServiceManager):
    name = "manual"

    def probe(self) -> bool:
        return True

    def _unsupported(self, service: str) -> ServiceResult:
        validate_service_name(service)
        raise UnsupportedOperation("no supported native service manager was detected")

    status = start = stop = restart = enable = disable = _unsupported
