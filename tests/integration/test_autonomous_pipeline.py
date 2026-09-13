"""Autonomous production owner acceptance: no manual dispatcher or worker ticks."""

import asyncio
import os
import shutil
from datetime import UTC, datetime

import pytest
import yaml
from article_fixtures import offline_acquirer
from integration.test_production_research_pipeline import DeterministicResearchAI
from media_fixtures import attach_caller_media
from news_ai_collector import CollectedArticle, FeedFetchResult
from news_ai_common.config import AppSettings
from news_ai_database import (
    ArticleVersion,
    Base,
    ContentDraft,
    ContentQualityCheck,
    ContentVariant,
    EventOutbox,
    ProcessedEvent,
    Publication,
    ReviewDecisionRecord,
    Story,
)
from news_ai_domain import ReviewState
from news_ai_pipeline import PipelineRunner, build_production_pipeline_stack
from sqlalchemy import func, select
from unit.pipeline.test_runner import fast_config


class Feed:
    calls = 0

    async def collect(self, definition):
        self.calls += 1
        return FeedFetchResult(
            source_feed_id=definition.source_feed_id,
            articles=[
                CollectedArticle(
                    source_id=definition.source_id,
                    source_feed_id=definition.source_feed_id,
                    url="https://records.example/gauge",
                    title="River gauge measured two metres",
                    language="en",
                    external_id="gauge-1",
                    summary="Officials announced an update.",
                    body=None,
                    published_at=datetime(2026, 9, 13, tzinfo=UTC),
                )
            ],
        )


async def eventually(predicate):
    async with asyncio.timeout(20):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("restart", [False, True])
def test_real_postgres_redis_autonomous_owner_media_deferral_and_restart(
    tmp_path, restart, monkeypatch
):
    database_url, redis_url = os.getenv("NEWS_AI_DATABASE_URL"), os.getenv("NEWS_AI_REDIS_URL")
    if not database_url or not redis_url or os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("explicit disposable PostgreSQL 16 / Redis 7 test services required")
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "true")
    config_root = tmp_path / "config"
    shutil.copytree("config", config_root)
    (config_root / "sources" / "registry.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "key": "records",
                        "name": "Gauge records",
                        "source_type": "NEWS",
                        "domain": "records.example",
                        "language": "en",
                    }
                ],
            }
        )
    )
    (config_root / "sources" / "feeds.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "feeds": [
                    {
                        "key": "records-rss",
                        "source_key": "records",
                        "name": "Records RSS",
                        "url": "https://records.example/feed",
                        "poll_interval_seconds": 60,
                    }
                ],
            }
        )
    )
    settings = AppSettings(
        config_dir=config_root,
        database_url=database_url,
        redis_url=redis_url,
    )

    async def scenario():
        ai, feed = DeterministicResearchAI(), Feed()
        stack = await build_production_pipeline_stack(
            settings,
            ai_providers=(ai,),
            feed_collector=feed,
            config=fast_config(),
            article_content_acquirer=offline_acquirer(
                "The river gauge measured two metres. Official flood records show this reading. "
                "The bridge will reopen at 06:30 on 14 September after the structural inspection."
            ),
        )
        factory = stack.session_factory
        with stack.engine.begin() as connection:
            tables = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
            connection.exec_driver_sql(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")
        streams = {worker.consumer.stream for worker in stack.workers.values()}
        await stack.redis_client.delete(*streams)
        runner = PipelineRunner(stack)
        task = asyncio.create_task(runner.run())

        def generated_and_deferred():
            with factory() as session:
                variant = session.scalar(select(ContentVariant))
                if variant is None:
                    return False
                generated = session.scalar(
                    select(EventOutbox).where(EventOutbox.event_type == "content.generated")
                )
                return generated is not None and generated.status == "PUBLISHED"

        try:
            await asyncio.wait_for(runner.ready_event.wait(), 5)
            await eventually(generated_and_deferred)
            with factory() as session:
                variant = session.scalar(select(ContentVariant))
                variant_id = variant.id
                assert variant.review_state is ReviewState.NOT_READY
                assert variant.media_asset_ids == []
                assert not session.scalar(select(ContentQualityCheck))
                generated = session.scalar(
                    select(EventOutbox).where(EventOutbox.event_type == "content.generated")
                )
                assert session.get(ProcessedEvent, (generated.event_id, "quality-worker")) is None
            async with asyncio.timeout(5):
                while not (await stack.redis_client.xpending("news:content", "quality-worker"))[
                    "pending"
                ]:
                    await asyncio.sleep(0.01)
            if restart:
                runner.stop_event.set()
                await asyncio.wait_for(task, 5)
                stack = await build_production_pipeline_stack(
                    settings,
                    ai_providers=(ai,),
                    feed_collector=feed,
                    config=fast_config(),
                    article_content_acquirer=offline_acquirer(),
                )
                factory = stack.session_factory
                # Only the external caller attaches media; all pipeline work stays autonomous.
                attach_caller_media(factory, variant_id)
                runner = PipelineRunner(stack)
                task = asyncio.create_task(runner.run())
                await asyncio.wait_for(runner.ready_event.wait(), 5)
            else:
                attach_caller_media(factory, variant_id)

            def ready():
                with factory() as session:
                    return (
                        session.get(ContentVariant, variant_id).review_state
                        is ReviewState.READY_FOR_REVIEW
                    )

            await eventually(ready)
            with factory() as session:
                for model in (
                    ArticleVersion,
                    Story,
                    ContentDraft,
                    ContentVariant,
                    ContentQualityCheck,
                ):
                    assert session.scalar(select(func.count()).select_from(model)) == 1
                assert not session.scalar(select(Publication))
                assert not session.scalar(select(ReviewDecisionRecord))
                events = list(session.scalars(select(EventOutbox)))
                expected = {
                    "article.discovered",
                    "article.normalized",
                    "story.created",
                    "claims.extracted",
                    "evidence.requested",
                    "evidence.collected",
                    "fact_check.completed",
                    "story.verified",
                    "content.requested",
                    "content.generated",
                    "content.quality_checked",
                }
                assert expected <= {event.event_type for event in events}
                assert session.get(ProcessedEvent, (generated.event_id, "quality-worker"))
                variant = session.get(ContentVariant, variant_id)
                assert (
                    variant.media_asset_ids and variant.review_state is ReviewState.READY_FOR_REVIEW
                )
            assert feed.calls == 1
            from news_ai_ai import AITaskType

            request = next(
                item for item in ai.requests if item.task_type is AITaskType.CLAIM_EXTRACTION
            )
            with factory() as session:
                reviewed = session.scalar(select(ArticleVersion))
                assert request.input["articles"][0]["content"] == reviewed.body
                assert "06:30 on 14 September" in reviewed.body
                assert reviewed.version_metadata["content_acquisition"]["origin"] == "ARTICLE_PAGE"
        finally:
            runner.stop_event.set()
            await asyncio.wait_for(task, 5)
            assert stack._closed and all(component.done() for component in runner.tasks)

    asyncio.run(scenario())
