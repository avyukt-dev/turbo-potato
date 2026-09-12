"""Fail-closed MOCK/LIVE Instagram adapter composition."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from uuid import uuid4

from news_ai_common.config import AppSettings, ConfigDomain, ConfigLoader
from news_ai_database import create_database_engine, create_session_factory
from news_ai_editorial import EditorialConfigLoader
from news_ai_events import RedisStreamConsumer
from news_ai_publishing import PublicationExecutionService, PublicationService, SchedulerConfig
from news_ai_publishing.contracts import system_clock
from news_ai_review import ApprovalEligibilityService
from news_ai_social import (
    HttpxInstagramGraphTransport,
    InstagramAdapter,
    InstagramExecutionAdapter,
    InstagramGraphTransport,
    MockInstagramAdapter,
    SocialMode,
    SocialSettings,
    load_instagram_config,
)

from .worker import PublisherWorker


@dataclass(frozen=True)
class ProductionPublisherStack:
    service: PublicationExecutionService
    worker: PublisherWorker


def build_production_publisher_stack(
    settings: AppSettings,
    *,
    social_settings: SocialSettings | None = None,
    session_factory=None,
    redis_client=None,
    transport=None,
    clock=system_clock,
    sleeper=None,
    jitter=None,
):
    if not settings.database_url and session_factory is None:
        raise ValueError("publisher database configuration unavailable")
    if not settings.redis_url and redis_client is None:
        raise ValueError("publisher Redis configuration unavailable")
    loader = ConfigLoader(settings.config_dir)
    config = loader.load_domain_file(ConfigDomain.PLATFORMS, "publishing.yaml", SchedulerConfig)
    policy = EditorialConfigLoader(loader).load_publishing_policy()
    factory = session_factory or create_session_factory(
        create_database_engine(settings.database_url)
    )
    if redis_client is None:
        from redis.asyncio import Redis

        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    social_settings = social_settings or SocialSettings(environment=settings.environment)
    adapter = build_instagram_adapter(
        social_settings, config_root=str(settings.config_dir), transport=transport
    )

    def paused():
        value = os.getenv("NEWS_AI_PUBLISHING_PAUSED")
        if value is None:
            return config.publishing_paused
        if value.casefold() not in {"true", "false"}:
            raise ValueError("invalid publishing pause configuration")
        return value.casefold() == "true"

    service = PublicationService(
        factory,
        ApprovalEligibilityService(factory, policy),
        clock=clock,
        platform_config=load_instagram_config(settings.config_dir),
    )
    options = {}
    if sleeper is not None:
        options["sleeper"] = sleeper
    if jitter is not None:
        options["jitter"] = jitter
    execution = PublicationExecutionService(
        service,
        adapter,
        config.publisher,
        paused=paused,
        live_account_id=(
            social_settings.instagram_account_id
            if social_settings.social_mode == SocialMode.LIVE
            else None
        ),
        **options,
    )
    consumer = RedisStreamConsumer(
        redis_client,
        stream="news:publishing",
        group="publisher",
        consumer=f"{config.publisher.consumer_name_prefix}-{socket.gethostname()}-{uuid4().hex[:8]}",
        count=config.publisher.batch_size,
        block_ms=config.publisher.block_ms,
    )
    return ProductionPublisherStack(execution, PublisherWorker(consumer, execution))


def build_instagram_adapter(
    settings: SocialSettings,
    *,
    config_root: str = "config",
    transport: InstagramGraphTransport | None = None,
) -> InstagramExecutionAdapter:
    config = load_instagram_config(config_root)
    if settings.social_mode is SocialMode.MOCK:
        return MockInstagramAdapter(config)

    environment = settings.environment.strip().lower()
    if environment not in config.live_environments:
        raise RuntimeError("LIVE social mode is not allowed in this environment")
    if not settings.publishing_enabled:
        raise RuntimeError("LIVE social mode requires publishing to be explicitly enabled")
    if not config.enabled:
        raise RuntimeError("Instagram platform configuration is disabled")
    if settings.instagram_account_id is None:
        raise RuntimeError("LIVE Instagram mode requires an account ID")
    if settings.instagram_access_token is None:
        raise RuntimeError("LIVE Instagram mode requires an access token")
    graph_transport = transport or HttpxInstagramGraphTransport(
        graph_base_url=str(config.graph_base_url),
        api_version=config.api_version,
        access_token=settings.instagram_access_token,
        timeout_seconds=config.request_timeout_seconds,
    )
    return InstagramAdapter(
        config,
        account_id=settings.instagram_account_id,
        transport=graph_transport,
    )
