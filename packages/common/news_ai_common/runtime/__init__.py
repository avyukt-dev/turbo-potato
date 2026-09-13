"""Platform-independent runtime capability and service-management boundary."""

from .base import ServiceManager, ServiceResult, ServiceState, UnsupportedOperation
from .detector import RuntimeDetector, RuntimeInfo

__all__ = [
    "RuntimeDetector",
    "RuntimeInfo",
    "ServiceManager",
    "ServiceResult",
    "ServiceState",
    "UnsupportedOperation",
]
