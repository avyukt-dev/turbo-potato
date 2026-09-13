"""Positive Slice-A probe, superseding the historical PA-MVP-01 reproduction.

The historical audit correctly demonstrated the fabricated-media rejection.
Permanent regression coverage now proves supported attachment, exact quality,
explicit human review and MOCK execution in the real PostgreSQL/Redis pipeline.
This remains destructive only to explicitly configured local test services.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.test_production_research_pipeline import _run_pipeline
from news_ai_domain import ReviewState
from sqlalchemy.engine import make_url


def probe_valid_caller_media_reaches_mock_publication(monkeypatch):
    database_url, redis_url = os.getenv("NEWS_AI_DATABASE_URL"), os.getenv("NEWS_AI_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("local PostgreSQL and Redis acceptance services required")
    assert os.getenv("NEWS_AI_ENVIRONMENT") == "test"
    assert make_url(database_url).host in {"127.0.0.1", "localhost", "postgres"}
    assert make_url(redis_url).host in {"127.0.0.1", "localhost", "redis"}
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")
    asyncio.run(
        _run_pipeline(database_url, redis_url, False, True, ReviewState.APPROVED, publish_mock=True)
    )


if __name__ == "__main__":
    with pytest.MonkeyPatch.context() as patch:
        probe_valid_caller_media_reaches_mock_publication(patch)
    print("PA-MVP-01: CLOSED/PASS; caller media reached explicit review and MOCK publication")
