import pytest

from news_ai_common.runtime import RuntimeDetector, ServiceManager, ServiceResult, ServiceState
from news_ai_common.runtime.base import validate_service_name


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


def test_detector_selects_first_verified_capability() -> None:
    detector = RuntimeDetector(
        candidates=[FakeManager("first", False), FakeManager("second", True)],
    )

    assert detector.detect_service_manager().name == "second"


def test_detector_rejects_unusable_override() -> None:
    detector = RuntimeDetector(candidates=[FakeManager("openrc", False)], override="openrc")

    with pytest.raises(RuntimeError, match="not usable"):
        detector.detect_service_manager()


def test_detector_falls_back_to_manual() -> None:
    detector = RuntimeDetector(candidates=[FakeManager("none", False)])

    assert detector.detect_service_manager().name == "manual"


@pytest.mark.parametrize("name", ["-bad", "bad name", "bad/thing", ""])
def test_service_name_validation_rejects_unsafe_values(name: str) -> None:
    with pytest.raises(ValueError):
        validate_service_name(name)
