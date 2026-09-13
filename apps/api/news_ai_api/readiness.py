"""Compatibility boundary for the canonical shared dependency probes."""

from news_ai_runtime.dependencies import ReadinessProbe, _check_ai_router, run_dependency_checks

__all__ = ["ReadinessProbe", "_check_ai_router", "run_dependency_checks"]
