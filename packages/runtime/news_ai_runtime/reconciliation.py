"""Production composition for explicit PostgreSQL-to-Redis event reconciliation."""

from dataclasses import dataclass

from news_ai_common.config import AppSettings
from news_ai_database import create_database_engine, create_session_factory
from news_ai_events import EventReconciliationService, RedisStreamPublisher

from .publishing import DatabasePublishingControl


@dataclass(slots=True)
class EventReconciliationStack:
    service: EventReconciliationService
    redis_client: object
    engine: object

    async def close(self) -> None:
        await self.redis_client.aclose()
        self.engine.dispose()


def build_reconciliation_stack(settings: AppSettings) -> EventReconciliationStack:
    if not settings.database_url or not settings.redis_url:
        raise ValueError("event reconciliation dependencies are unavailable")
    from redis.asyncio import Redis

    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.readiness_timeout_seconds,
        socket_timeout=settings.readiness_timeout_seconds,
    )
    service = EventReconciliationService(
        factory,
        RedisStreamPublisher(redis),
        redis,
        DatabasePublishingControl(
            factory,
            timeout_seconds=settings.readiness_timeout_seconds,
        ),
    )
    return EventReconciliationStack(service=service, redis_client=redis, engine=engine)
