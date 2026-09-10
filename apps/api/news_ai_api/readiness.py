"""Readiness probes for infrastructure dependencies.

The API reports dependency health but does not expose connection strings or provider exceptions.
"""

import asyncio
from collections.abc import Awaitable, Callable

from news_ai_common.config import AppSettings
from news_ai_database.session import create_database_engine
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

ReadinessProbe = Callable[[AppSettings], Awaitable[dict[str, bool]]]


async def _check_database(database_url: str, *, timeout_seconds: float) -> bool:
    def ping() -> bool:
        engine = create_database_engine(database_url)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        finally:
            engine.dispose()

    try:
        return await asyncio.wait_for(asyncio.to_thread(ping), timeout=timeout_seconds)
    except (SQLAlchemyError, OSError, TimeoutError):
        return False


async def _check_redis(redis_url: str, *, timeout_seconds: float) -> bool:
    from redis.asyncio import Redis
    from redis.exceptions import RedisError

    client = Redis.from_url(
        redis_url,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
    )
    try:
        return bool(await asyncio.wait_for(client.ping(), timeout=timeout_seconds))
    except (RedisError, OSError, TimeoutError):
        return False
    finally:
        await client.aclose()


async def run_dependency_checks(settings: AppSettings) -> dict[str, bool]:
    database_task = (
        _check_database(settings.database_url, timeout_seconds=settings.readiness_timeout_seconds)
        if settings.database_url
        else _false()
    )
    redis_task = (
        _check_redis(settings.redis_url, timeout_seconds=settings.readiness_timeout_seconds)
        if settings.redis_url
        else _false()
    )
    database_ready, redis_ready = await asyncio.gather(database_task, redis_task)
    return {"postgres": database_ready, "redis": redis_ready}


async def _false() -> bool:
    return False
