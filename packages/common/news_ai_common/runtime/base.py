"""Runtime service-manager contracts and safe subprocess execution."""

import re
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from threading import Thread

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
    error_code: str | None = None


class UnsupportedOperation(RuntimeError):
    pass


def validate_service_name(name: str) -> str:
    if not name or name.startswith("-") or not _SERVICE_NAME.fullmatch(name):
        raise ValueError("invalid service name")
    return name


class CommandRunner:
    """Small subprocess boundary that can be replaced by tests."""

    def run(self, argv: Sequence[str], *, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
        # Drain both pipes continuously, retaining at most 8 KiB per stream.
        # This bounds memory even when a native utility emits unlimited diagnostics.
        buffers = [bytearray(), bytearray()]
        process = subprocess.Popen(  # noqa: S603
            list(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False
        )

        def drain(pipe, buffer):
            with pipe:
                while chunk := pipe.read(4096):
                    buffer.extend(chunk[: max(0, 8192 - len(buffer))])

        threads = [
            Thread(target=drain, args=(pipe, buffer), daemon=True)
            for pipe, buffer in zip((process.stdout, process.stderr), buffers, strict=True)
        ]
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise subprocess.TimeoutExpired("native command", timeout) from None
        finally:
            for thread in threads:
                thread.join(timeout=0.1)
        return subprocess.CompletedProcess(
            [],
            process.returncode,
            *(buffer.decode("utf-8", errors="replace") for buffer in buffers),
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
