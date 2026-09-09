"""Capability-based runtime detection."""

from dataclasses import dataclass
import os
from pathlib import Path
import platform
from typing import Iterable

from .base import ServiceManager
from .managers import ManualServiceManager, OpenRCServiceManager, SysVServiceManager, SystemdServiceManager


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    system: str
    machine: str
    os_name: str
    os_release: dict[str, str]
    service_manager: str


def _read_os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    except OSError:
        return {}
    return values


class RuntimeDetector:
    """Selects the first verified service-manager capability.

    OS identity may be reported as metadata but is not used as proof that a native utility works.
    """

    def __init__(
        self,
        candidates: Iterable[ServiceManager] | None = None,
        override: str | None = None,
    ) -> None:
        self._candidates = list(candidates) if candidates is not None else [
            OpenRCServiceManager(),
            SystemdServiceManager(),
            SysVServiceManager(),
        ]
        self._override = override or os.getenv("NEWS_AI_SERVICE_MANAGER")

    def detect_service_manager(self) -> ServiceManager:
        if self._override:
            normalized = self._override.strip().lower()
            for manager in self._candidates:
                if manager.name == normalized:
                    if not manager.probe():
                        raise RuntimeError(f"requested service manager is not usable: {normalized}")
                    return manager
            if normalized == "manual":
                return ManualServiceManager()
            raise RuntimeError(f"unknown service manager override: {normalized}")

        for manager in self._candidates:
            try:
                if manager.probe():
                    return manager
            except (OSError, RuntimeError, TimeoutError):
                continue
        return ManualServiceManager()

    def inspect(self) -> RuntimeInfo:
        manager = self.detect_service_manager()
        return RuntimeInfo(
            system=platform.system(),
            machine=platform.machine(),
            os_name=os.name,
            os_release=_read_os_release(),
            service_manager=manager.name,
        )
