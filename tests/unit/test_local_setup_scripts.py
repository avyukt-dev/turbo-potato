from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_setup_is_valid_bash_and_clean_is_valid_posix_sh() -> None:
    subprocess.run(["bash", "-n", str(ROOT / "setup.sh")], check=True)
    subprocess.run(["sh", "-n", str(ROOT / "clean.sh")], check=True)


def test_setup_contract_is_restartable_and_non_live() -> None:
    script = (ROOT / "setup.sh").read_text(encoding="utf-8")
    assert script.count("--restart unless-stopped") == 2
    assert "-p 127.0.0.1::5432" in script
    assert "-p 127.0.0.1::6379" in script
    assert "unset NEWS_AI_PUBLISHING_PAUSED" in script
    assert "printf 'export NEWS_AI_RUN_LIVE_GROQ=%q\\n' \"0\"" in script
    assert "unset GROQ_API_KEY" in script


def test_setup_covers_common_host_families() -> None:
    script = (ROOT / "setup.sh").read_text(encoding="utf-8")
    for capability in (
        "apt-get",
        "apk",
        "dnf",
        "yum",
        "pacman",
        "zypper",
        "xbps-install",
        "emerge",
        "brew",
        "winget.exe",
        "choco.exe",
        "systemctl",
        "rc-update",
        "sv",
        "launchctl",
    ):
        assert capability in script


def test_setup_only_records_sysv_autostart_when_missing() -> None:
    script = (ROOT / "setup.sh").read_text(encoding="utf-8")
    assert "compgen -G '/etc/rc?.d/S??docker'" in script


def test_clean_is_sourceable_from_posix_shells() -> None:
    script = (ROOT / "clean.sh").read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh\n")
    assert "BASH_SOURCE" not in script
    assert "[[" not in script
    assert "DOCKER_CMD=(" not in script


def test_clean_removes_local_resources_and_test_environment() -> None:
    script = (ROOT / "clean.sh").read_text(encoding="utf-8")
    assert 'clean_docker rm -fv "$CLEAN_CONTAINER_NAME"' in script
    assert 'rm -rf "$VENV_DIR"' in script
    assert 'rm -f "$ENV_FILE"' in script
    for variable in (
        "NEWS_AI_ENVIRONMENT",
        "NEWS_AI_CONFIG_DIR",
        "NEWS_AI_DATABASE_URL",
        "NEWS_AI_REDIS_URL",
        "NEWS_AI_READINESS_TIMEOUT_SECONDS",
        "NEWS_AI_SOCIAL_MODE",
        "NEWS_AI_PUBLISHING_ENABLED",
        "NEWS_AI_PUBLISHING_PAUSED",
        "NEWS_AI_RUN_LIVE_GROQ",
        "GROQ_API_KEY",
    ):
        assert f"unset {variable}" in script
