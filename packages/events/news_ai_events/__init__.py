"""Typed event contracts, outbox helpers, and Redis Streams transport primitives."""

from .consumer import RedisStreamConsumer, StreamMessage
from .dispatcher import DispatchStats, OutboxDispatcher, RetryPolicy
from .envelope import EventEnvelope
from .idempotency import mark_processed, was_processed
from .payloads import (
    ClaimsExtractedV1,
    ContentGeneratedV1,
    ContentQualityCheckedV1,
    ContentRequestedV1,
    EvidenceCollectedV1,
    EvidenceRequestedV1,
    FactCheckCompletedV1,
    StoryVerifiedV1,
    parse_event_payload,
    payload_model_for,
)
from .reconciliation import (
    DEFAULT_RECONCILIATION_LIMIT,
    MAX_RECONCILIATION_LIMIT,
    RECONCILIATION_POLICIES,
    RECONCILIATION_POLICY_BY_EVENT,
    EventReconciliationPolicy,
    EventReconciliationService,
    ReconciliationDisposition,
    ReconciliationMode,
    ReconciliationReport,
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
    "EventReconciliationService",
    "EventReconciliationPolicy",
    "RECONCILIATION_POLICIES",
    "RECONCILIATION_POLICY_BY_EVENT",
    "EventType",
    "ClaimsExtractedV1",
    "ContentRequestedV1",
    "ContentGeneratedV1",
    "ContentQualityCheckedV1",
    "EvidenceCollectedV1",
    "EvidenceRequestedV1",
    "FactCheckCompletedV1",
    "OutboxDispatcher",
    "PermanentEventError",
    "ProcessingOutcome",
    "RedisStreamConsumer",
    "RedisStreamPublisher",
    "ReliableMessageProcessor",
    "ReconciliationDisposition",
    "ReconciliationMode",
    "ReconciliationReport",
    "StaleWorkError",
    "TransientEventError",
    "RetryPolicy",
    "StreamMessage",
    "StoryVerifiedV1",
    "WorkerBatchResult",
    "WorkerRetryConfig",
    "WorkerRetryPolicy",
    "load_worker_retry_policy",
    "DEFAULT_RECONCILIATION_LIMIT",
    "MAX_RECONCILIATION_LIMIT",
    "mark_processed",
    "parse_event_payload",
    "payload_model_for",
    "stream_for_event",
    "was_processed",
]
