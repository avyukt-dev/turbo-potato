"""Negative acceptance probe: valid caller media cannot cross current Quality Gate.

A passing reproduction is NOT a passing full-MVP acceptance result. The audit
report records this production composition gap; this test does not bypass it.
"""

import asyncio
import os

import pytest
from integration.test_production_research_pipeline import _run_pipeline
from news_ai_database import ContentVariant, EventDeadLetter, MediaAsset, Publication
from news_ai_domain import ReviewState
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


def attach_synthetic_media(factory):
    with factory() as session, session.begin():
        variant = session.scalar(select(ContentVariant))
        assets = [
            MediaAsset(
                asset_type="IMAGE",
                storage_provider="acceptance-synthetic",
                storage_key=f"slide-{index}.jpg",
                public_url=f"https://media.example.org/acceptance-{index}.jpg",
                mime_type="image/jpeg",
                file_hash=str(index) * 64,
                visual_check_status="VALIDATED",
                source_metadata={"media_format": "JPEG"},
            )
            for index in (1, 2)
        ]
        session.add_all(assets)
        session.flush()
        variant.media_asset_ids = [str(asset.id) for asset in assets]


def probe_valid_caller_media_blocks_full_mvp_at_quality_boundary(monkeypatch):
    database_url, redis_url = os.getenv("NEWS_AI_DATABASE_URL"), os.getenv("NEWS_AI_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("local PostgreSQL and Redis acceptance services required")
    assert os.getenv("NEWS_AI_ENVIRONMENT") == "test"
    assert make_url(database_url).host in {"127.0.0.1", "localhost", "postgres"}
    assert make_url(redis_url).host in {"127.0.0.1", "localhost", "redis"}
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "false")
    # This asserts today's failure, not desirable product semantics.
    with pytest.raises(AssertionError):
        asyncio.run(
            _run_pipeline(
                database_url,
                redis_url,
                False,
                True,
                ReviewState.APPROVED,
                before_quality=attach_synthetic_media,
            )
        )
    engine = create_engine(database_url)
    try:
        with sessionmaker(engine)() as session:
            letters = list(session.scalars(select(EventDeadLetter)))
            assert len(letters) == 1
            assert (
                letters[0].error_message == "Stage-22 input contains fabricated media identifiers"
            )
            assert session.scalar(select(func.count()).select_from(Publication)) == 0
            variant = session.scalar(select(ContentVariant))
            assert len(variant.media_asset_ids) == 2
            assert variant.review_state is ReviewState.NOT_READY
    finally:
        engine.dispose()


if __name__ == "__main__":
    with pytest.MonkeyPatch.context() as patch:
        probe_valid_caller_media_blocks_full_mvp_at_quality_boundary(patch)
    print("Full MVP acceptance: FAIL; valid durable media rejected before quality assessment")
