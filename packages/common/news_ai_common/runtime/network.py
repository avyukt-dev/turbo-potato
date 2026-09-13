"""Bounded read-only network diagnostics behind the native runtime boundary."""

import json
import shutil
import sys

from .base import CommandRunner


def dns_available(target, *, timeout=5, runner=None):
    result = (runner or CommandRunner()).run(
        [sys.executable, "-c", "import socket,sys; socket.getaddrinfo(sys.argv[1], None)", target],
        timeout=timeout,
    )
    return result.returncode == 0


def tailscale_state(*, timeout=5, runner=None):
    if not shutil.which("tailscale"):
        return "unavailable"
    result = (runner or CommandRunner()).run(["tailscale", "status", "--json"], timeout=timeout)
    if result.returncode:
        return "unavailable"
    try:
        state = json.loads(result.stdout).get("BackendState")
        return "running" if state == "Running" else "not_running"
    except (ValueError, AttributeError):
        return "unknown"
