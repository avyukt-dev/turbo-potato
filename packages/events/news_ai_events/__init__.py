"""Typed event contracts, outbox helpers, and Redis Streams transport primitives."""

from .consumer import RedisStreamConsumer, StreamMessage
from .dispatcher import DispatchStats, OutboxDispatcher, RetryPolicy
from .envelope import EventEnvelope
from .idempotency import mark_processed, was_processed
from .payloads import (
    ClaimsExtractedV1,
    ContentRequestedV1,
    EvidenceCollectedV1,
    EvidenceRequestedV1,
    FactCheckCompletedV1,
    StoryVerifiedV1,
    parse_event_payload,
    payload_model_for,
)
from .reliability import (
    PermanentEventError,
    ProcessingOutcome,
    ReliableMessageProcessor,
    StaleWorkError,
    TransientEventError,
    WorkerBatchResult,
    WorkerRetryConfig,
    WorkerRetryPolicy,
    load_worker_retry_policy,
)
from .streams import RedisStreamPublisher, stream_for_event
from .types import EventType

__all__ = [
    "DispatchStats",
    "EventEnvelope",
    "EventType",
    "ClaimsExtractedV1",
    "ContentRequestedV1",
    "EvidenceCollectedV1",
    "EvidenceRequestedV1",
    "FactCheckCompletedV1",
    "OutboxDispatcher",
    "PermanentEventError",
    "ProcessingOutcome",
    "RedisStreamConsumer",
    "RedisStreamPublisher",
    "ReliableMessageProcessor",
    "StaleWorkError",
    "TransientEventError",
    "RetryPolicy",
    "StreamMessage",
    "StoryVerifiedV1",
    "WorkerBatchResult",
    "WorkerRetryConfig",
    "WorkerRetryPolicy",
    "load_worker_retry_policy",
    "mark_processed",
    "parse_event_payload",
    "payload_model_for",
    "stream_for_event",
    "was_processed",
]
