"""One upstream host owner; existing production stacks retain domain ownership."""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from math import ceil
from typing import Any
from uuid import uuid4

from news_ai_ai import build_ai_router
from news_ai_ai_worker import (
    ProductionContentStack,
    ProductionQualityStack,
    build_production_content_stack,
    build_production_quality_stack,
)
from news_ai_collector import (
    CollectorScheduler,
    DiscoveredArticleHandler,
    RSSCollector,
    SourceRegistryLoader,
)
from news_ai_common.config import AppSettings, ConfigLoader
from news_ai_database import create_database_engine, create_session_factory
from news_ai_events import OutboxDispatcher, RedisStreamConsumer, RedisStreamPublisher
from news_ai_events.consumer_contracts import NORMALIZER_CONSUMER_GROUP, PROCESSOR_CONSUMER_GROUP
from news_ai_processor import NormalizerEventWorker, ProcessorEventWorker, StoryClusteringService
from news_ai_processor.acquisition import ArticleContentAcquirer, HttpArticleContentAcquirer
from news_ai_research_worker import ProductionResearchStack, build_production_research_stack
from redis.asyncio import Redis
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from .configuration import PipelineConfig


@dataclass
class ProductionPipelineStack:
    settings: AppSettings
    config: PipelineConfig
    engine: Engine
    session_factory: Callable[[], Session]
    redis_client: Redis
    collector: CollectorScheduler
    dispatcher: OutboxDispatcher
    research: ProductionResearchStack
    content: ProductionContentStack
    quality: ProductionQualityStack
    workers: dict[str, Any]
    owned_ai_providers: tuple = ()
    owned_content_acquirer: HttpArticleContentAcquirer | None = None
    _closed: bool = field(default=False, init=False)

    async def ensure_ready(self):
        def probe_database():
            with self.session_factory() as session:
                if session.bind.dialect.name == "postgresql":
                    session.execute(
                        text("SELECT set_config('statement_timeout', :timeout, true)"),
                        {"timeout": f"{int(self.settings.readiness_timeout_seconds * 1000)}ms"},
                    )
                session.execute(text("SELECT 1"))

        await asyncio.to_thread(probe_database)
        if not await self.redis_client.ping():
            raise RuntimeError("pipeline dependency unavailable")
        for worker in self.workers.values():
            await worker.ensure_ready()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        await asyncio.wait_for(
            _close_resources(
                self.owned_ai_providers
                + ((self.owned_content_acquirer,) if self.owned_content_acquirer else ()),
                self.redis_client,
                self.engine,
            ),
            timeout=self.config.shutdown_timeout_seconds,
        )


async def _close_resources(providers, client, engine):
    # Attempt every close even if one dependency fails; never render its raw error.
    closes = [provider.close() for provider in providers if hasattr(provider, "close")]
    if client is not None:
        closes.append(client.aclose())
    try:
        results = await asyncio.gather(*closes, return_exceptions=True)
    finally:
        engine.dispose()
    if any(isinstance(result, BaseException) for result in results):
        raise RuntimeError("pipeline resource cleanup unavailable") from None


async def build_production_pipeline_stack(
    settings: AppSettings,
    *,
    ai_providers=None,
    feed_collector=None,
    config: PipelineConfig | None = None,
    article_content_acquirer: ArticleContentAcquirer | None = None,
) -> ProductionPipelineStack:
    """Injected providers/collector use official boundaries and remain caller-owned."""
    loader = ConfigLoader(settings.config_dir)
    config = config or PipelineConfig.load(loader)
    source_loader = SourceRegistryLoader(loader)
    snapshot = source_loader.load()  # Fail startup, not the first feed tick.
    if not settings.database_url or not settings.redis_url:
        raise ValueError("pipeline infrastructure configuration unavailable")
    database_url = make_url(settings.database_url)
    if database_url.get_backend_name() == "postgresql":
        # Bound the actual driver connection, not just cancellation of its to_thread await.
        database_url = database_url.update_query_dict(
            {"connect_timeout": str(max(2, ceil(settings.readiness_timeout_seconds)))}
        )
    engine = create_database_engine(database_url)
    client = None
    owned = ()
    owned_acquirer = None
    try:
        factory = create_session_factory(engine)
        client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.readiness_timeout_seconds,
            socket_timeout=max(10, config.consumer_block_ms / 1000 + 5),
        )
        router = build_ai_router(loader, providers=ai_providers)
        if ai_providers is None:
            owned = tuple(
                router.registry.get(item.provider_id) for item in router.registry.capabilities()
            )
        name = f"news-pipeline-{uuid4().hex}"
        arguments = dict(
            session_factory=factory, redis_client=client, consumer_name=name, ai_router=router
        )
        research = build_production_research_stack(settings, **arguments)
        content = build_production_content_stack(settings, **arguments)
        quality = build_production_quality_stack(settings, **arguments)
        if article_content_acquirer is None:
            owned_acquirer = HttpArticleContentAcquirer(
                snapshot.collection.defaults,
                user_agent=snapshot.collection.defaults.user_agent,
                max_concurrency=snapshot.collection.defaults.max_concurrency,
            )
            article_content_acquirer = owned_acquirer
        normalizer = NormalizerEventWorker(
            RedisStreamConsumer(
                client, stream="news:articles", group=NORMALIZER_CONSUMER_GROUP, consumer=name
            ),
            factory,
            content_acquirer=article_content_acquirer,
        )
        processor = ProcessorEventWorker(
            RedisStreamConsumer(
                client, stream="news:articles", group=PROCESSOR_CONSUMER_GROUP, consumer=name
            ),
            factory,
            StoryClusteringService(),
        )
        workers = {
            "normalizer": normalizer,
            "processor": processor,
            "claims": research.claim_worker,
            "research_planning": research.research_planning_worker,
            "evidence": research.evidence_collection_worker,
            "fact_check": research.fact_check_worker,
            "story_verification": research.story_verification_worker,
            "fact_sheet": research.fact_sheet_worker,
            "content": content.worker,
            "quality": quality.worker,
        }
        for worker in workers.values():
            worker.consumer.block_ms = config.consumer_block_ms
        return ProductionPipelineStack(
            settings=settings,
            config=config,
            engine=engine,
            session_factory=factory,
            redis_client=client,
            collector=CollectorScheduler(
                session_factory=factory,
                snapshot_loader=source_loader,
                collector=(
                    feed_collector
                    if feed_collector is not None
                    else RSSCollector(user_agent=snapshot.collection.defaults.user_agent)
                ),
                article_handler=DiscoveredArticleHandler(),
            ),
            dispatcher=OutboxDispatcher(factory, RedisStreamPublisher(client)),
            research=research,
            content=content,
            quality=quality,
            workers=workers,
            owned_ai_providers=owned,
            owned_content_acquirer=owned_acquirer,
        )
    except BaseException:
        # Preserve startup failure, with every owned resource close attempted.
        with suppress(Exception):
            await asyncio.wait_for(
                _close_resources(
                    owned + ((owned_acquirer,) if owned_acquirer else ()), client, engine
                ),
                timeout=config.shutdown_timeout_seconds,
            )
        raise
