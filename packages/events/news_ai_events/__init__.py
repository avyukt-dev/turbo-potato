"""Typed event contracts, outbox helpers, and Redis Streams transport primitives."""

from .consumer import RedisStreamConsumer, StreamMessage
from .dispatcher import DispatchStats, OutboxDispatcher, RetryPolicy
from .envelope import EventEnvelope
from .idempotency import mark_processed, was_processed
from .streams import RedisStreamPublisher, stream_for_event
from .types import EventType

__all__ = [
    "DispatchStats",
    "EventEnvelope",
    "EventType",
    "OutboxDispatcher",
    "RedisStreamConsumer",
    "RedisStreamPublisher",
    "RetryPolicy",
    "StreamMessage",
    "mark_processed",
    "stream_for_event",
    "was_processed",
]
