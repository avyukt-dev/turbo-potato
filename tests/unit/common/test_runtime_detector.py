import subprocess

import pytest
from news_ai_common.runtime import RuntimeDetector, ServiceManager, ServiceResult, ServiceState
from news_ai_common.runtime.base import validate_service_name
from news_ai_runtime.controller import _ordered_native_managers


class FakeManager(ServiceManager):
    def __init__(self, name: str, available: bool) -> None:
        super().__init__()
        self.name = name
        self.available = available

    def probe(self) -> bool:
        return self.available

    def _result(self, service: str) -> ServiceResult:
        validate_service_name(service)
        return ServiceResult(ServiceState.ACTIVE, 0)

    status = start = stop = _result


class TimeoutManager(FakeManager):
    def probe(self) -> bool:
        raise subprocess.TimeoutExpired(["probe"], 5)


def test_detector_selects_first_verified_capability() -> None:
    detector = RuntimeDetector(
        candidates=[FakeManager("first", False), FakeManager("second", True)],
    )

    assert detector.detect_service_manager().name == "second"


def test_detector_rejects_unusable_override() -> None:
    detector = RuntimeDetector(candidates=[FakeManager("openrc", False)], override="openrc")

    with pytest.raises(RuntimeError, match="not usable"):
        detector.detect_service_manager()


def test_profile_preference_does_not_suppress_omitted_native_manager() -> None:
    managers = [
        FakeManager("openrc", False),
        FakeManager("systemd", True),
        FakeManager("sysv", False),
    ]

    ordered = _ordered_native_managers(managers, ("openrc",))

    assert [manager.name for manager in ordered] == ["openrc", "systemd", "sysv"]
    assert RuntimeDetector(candidates=ordered).detect_service_manager().name == "systemd"


def test_profile_preferred_native_order_is_honored_with_complete_fallback() -> None:
    managers = [
        FakeManager("openrc", True),
        FakeManager("systemd", True),
        FakeManager("sysv", False),
    ]

    ordered = _ordered_native_managers(managers, ("sysv", "openrc"))

    assert [manager.name for manager in ordered] == ["sysv", "openrc", "systemd"]
    assert RuntimeDetector(candidates=ordered).detect_service_manager().name == "openrc"


def test_profile_manual_preference_cannot_preempt_native_detection() -> None:
    managers = [
        FakeManager("openrc", False),
        FakeManager("systemd", True),
        FakeManager("sysv", False),
    ]

    ordered = _ordered_native_managers(managers, ("manual",))

    assert [manager.name for manager in ordered] == ["openrc", "systemd", "sysv"]
    assert RuntimeDetector(candidates=ordered).detect_service_manager().name == "systemd"


def test_explicit_manual_override_preempts_available_native_manager() -> None:
    detector = RuntimeDetector(candidates=[FakeManager("systemd", True)], override="manual")

    assert detector.detect_service_manager().name == "manual"


def test_detector_falls_back_to_manual() -> None:
    detector = RuntimeDetector(candidates=[FakeManager("none", False)])

    assert detector.detect_service_manager().name == "manual"


def test_probe_timeout_does_not_break_runtime_detection() -> None:
    detector = RuntimeDetector(
        candidates=[TimeoutManager("slow", True), FakeManager("usable", True)],
    )

    assert detector.detect_service_manager().name == "usable"


@pytest.mark.parametrize("name", ["-bad", "bad name", "bad/thing", ""])
def test_service_name_validation_rejects_unsafe_values(name: str) -> None:
    with pytest.raises(ValueError):
        validate_service_name(name)
