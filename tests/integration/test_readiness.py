import asyncio
import os

import pytest
from news_ai_api.readiness import run_dependency_checks
from news_ai_common.config import AppSettings


@pytest.mark.skipif(
    not os.getenv("NEWS_AI_DATABASE_URL") or not os.getenv("NEWS_AI_REDIS_URL"),
    reason="integration dependencies are not configured",
)
def test_postgres_and_redis_are_reachable() -> None:
    settings = AppSettings()

    result = asyncio.run(run_dependency_checks(settings))

    assert result == {"postgres": True, "redis": True}
