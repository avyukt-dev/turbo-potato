"""Production publication execution and fail-closed social composition."""

from .composition import build_instagram_adapter, build_production_publisher_stack
from .worker import PublisherWorker

__all__ = ["build_instagram_adapter", "build_production_publisher_stack", "PublisherWorker"]
