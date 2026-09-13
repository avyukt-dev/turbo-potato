"""Logical-service orchestration; no native command syntax or operation fallback."""

import os
import subprocess

from news_ai_common.config import ConfigDomain, ConfigLoader
from news_ai_common.diagnostics import safe_text
from news_ai_common.runtime import RuntimeDetector, UnsupportedOperation
from news_ai_common.runtime.managers import (
    ManualServiceManager,
    OpenRCServiceManager,
    SystemdServiceManager,
    SysVServiceManager,
)

from .contracts import RuntimeProfile, ServiceRegistry


class RuntimeOperationError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class RuntimeController:
    def __init__(self, registry, *, detector=None, profile=None):
        self.registry = registry
        self.profile = profile
        self.detector = detector or RuntimeDetector()
        self._manager = None

    @property
    def manager(self):
        if self._manager is None:
            try:
                self._manager = self.detector.detect_service_manager()
            except Exception:
                raise RuntimeOperationError("UNSUPPORTED_MANAGER") from None
        return self._manager

    def inspect(self):
        import platform

        from news_ai_common.runtime.detector import _read_os_release

        release = _read_os_release()
        return {
            "system": platform.system(),
            "machine": platform.machine(),
            "os_name": os.name,
            "os_release": {
                key: safe_text(release[key], 100) for key in ("ID", "VERSION_ID") if key in release
            },
            "service_manager": self.manager.name,
            "runtime_profile": self.profile.profile_id if self.profile else None,
            "resource_class": self.profile.resource_class if self.profile else None,
        }

    def operation(self, action, logical_name):
        entry = next(
            (entry for entry in self.registry.services if entry.name == logical_name), None
        )
        if entry is None or not entry.enabled:
            raise RuntimeOperationError("SERVICE_UNKNOWN")
        if action not in {"status", "start", "stop", "restart", "enable", "disable"}:
            raise RuntimeOperationError("UNSUPPORTED_OPERATION")
        if not entry.manageable or self.manager.name == "manual":
            if action != "status":
                raise RuntimeOperationError("UNSUPPORTED_OPERATION")
            return {
                "service": entry.name,
                "manager": self.manager.name,
                "state": "unknown",
                "manageable": False,
                "critical": entry.critical,
                "error_code": None,
            }
        try:
            result = getattr(self.manager, action)(entry.native_name)
        except FileNotFoundError:
            raise RuntimeOperationError("UTILITY_MISSING") from None
        except PermissionError:
            raise RuntimeOperationError("PERMISSION_DENIED") from None
        except subprocess.TimeoutExpired:
            raise RuntimeOperationError("COMMAND_TIMEOUT") from None
        except UnsupportedOperation:
            raise RuntimeOperationError("UNSUPPORTED_OPERATION") from None
        except Exception:
            raise RuntimeOperationError("COMMAND_FAILED") from None
        error = result.error_code
        if not error and action == "status" and result.state == "unknown":
            error = "UNEXPECTED_OUTPUT"
        elif not error and action != "status" and result.returncode != 0:
            error = "COMMAND_FAILED"
        return {
            "service": entry.name,
            "manager": self.manager.name,
            "state": result.state,
            "manageable": entry.manageable,
            "critical": entry.critical,
            "error_code": error,
        }

    def list_services(self):
        results = []
        for entry in self.registry.services:
            if not entry.enabled:
                continue
            try:
                result = self.operation("status", entry.name)
            except RuntimeOperationError as exc:
                result = {"service": entry.name, "state": "unknown", "error_code": exc.code}
            results.append({**result, "class": entry.service_class})
        return results


def build_controller(settings, *, detector=None):
    loader = ConfigLoader(settings.config_dir)
    registry = loader.load_domain_file(ConfigDomain.RUNTIME, "services.yaml", ServiceRegistry)
    profile_id = os.getenv("NEWS_AI_RUNTIME_PROFILE")
    profile = None
    if profile_id:
        import re

        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile_id):
            raise RuntimeOperationError("INVALID_PROFILE")
        profile = ConfigLoader(loader.root.parent / "infra/runtime/profiles").load_model(
            f"{profile_id}.yaml", RuntimeProfile
        )
        if profile.profile_id != profile_id:
            raise RuntimeOperationError("INVALID_PROFILE")
    if detector is None:
        candidates = {
            manager.name: manager
            for manager in (
                OpenRCServiceManager(),
                SystemdServiceManager(),
                SysVServiceManager(),
                ManualServiceManager(),
            )
        }
        order = (
            profile.hints.preferred_service_managers if profile else ("openrc", "systemd", "sysv")
        )
        detector = RuntimeDetector(
            candidates=[candidates[name] for name in order], override=settings.service_manager
        )
    return RuntimeController(registry, detector=detector, profile=profile)
