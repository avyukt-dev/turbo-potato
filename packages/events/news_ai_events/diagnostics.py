"""Closed, reliability-neutral outbox diagnostics; never render exception messages."""

from enum import StrEnum

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy.exc import SQLAlchemyError


class OutboxFailureContext(StrEnum):
    TRANSPORT = "OUTBOX_TRANSPORT_FAILED"
    STATE_UPDATE = "OUTBOX_STATE_UPDATE_FAILED"


class OutboxFailureCategory(StrEnum):
    TIMEOUT = "TIMEOUT"
    CONNECTION = "CONNECTION"
    DATABASE = "DATABASE"
    INTERNAL = "INTERNAL"


def safe_outbox_diagnostic(context: OutboxFailureContext, error: Exception) -> str:
    """Classify types only, without accessing text, repr, causes, SQL, or parameters."""
    if isinstance(error, SQLAlchemyError):
        category = OutboxFailureCategory.DATABASE
    elif isinstance(error, (TimeoutError, RedisTimeoutError)):
        category = OutboxFailureCategory.TIMEOUT
    elif isinstance(error, (ConnectionError, RedisConnectionError)):
        category = OutboxFailureCategory.CONNECTION
    else:
        category = OutboxFailureCategory.INTERNAL
    return f"{context.value}:{category.value}"
