"""Runtime service-manager contracts and safe subprocess execution."""

import re
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

_SERVICE_NAME = re.compile(r"^[A-Za-z0-9_.@:+-]+$")


class ServiceState(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ServiceResult:
    state: ServiceState
    returncode: int
    stdout: str = ""
    stderr: str = ""


class UnsupportedOperation(RuntimeError):
    pass


def validate_service_name(name: str) -> str:
    if not name or name.startswith("-") or not _SERVICE_NAME.fullmatch(name):
        raise ValueError(f"invalid service name: {name!r}")
    return name


class CommandRunner:
    """Small subprocess boundary that can be replaced by tests."""

    def run(self, argv: Sequence[str], *, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - argv is always a sequence, never a shell string
            list(argv),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )


class ServiceManager(ABC):
    name: str

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or CommandRunner()

    @abstractmethod
    def probe(self) -> bool:
        """Return True only when this manager appears usable on the current host."""

    @abstractmethod
    def status(self, service: str) -> ServiceResult:
        pass

    @abstractmethod
    def start(self, service: str) -> ServiceResult:
        pass

    @abstractmethod
    def stop(self, service: str) -> ServiceResult:
        pass

    def restart(self, service: str) -> ServiceResult:
        self.stop(service)
        return self.start(service)

    def enable(self, service: str) -> ServiceResult:
        raise UnsupportedOperation(f"{self.name} does not implement enable")

    def disable(self, service: str) -> ServiceResult:
        raise UnsupportedOperation(f"{self.name} does not implement disable")
