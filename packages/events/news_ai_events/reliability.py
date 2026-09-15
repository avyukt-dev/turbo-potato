"""Shared bounded failure handling for Redis consumer-group workers."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from news_ai_common.config import AppSettings, ConfigDomain, ConfigLoader
from news_ai_database import (
    EventAttemptStatus,
    EventDeadLetter,
    EventFailureClass,
    EventProcessingAttempt,
    Job,
    ProcessedEvent,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from .consumer import RedisStreamConsumer, StreamMessage
from .envelope import EventEnvelope
from .types import EventType

_SECRET_RE = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret)"
    r"\s*[\"']?\s*[:=]\s*[\"']?[^\s,;\"'}]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_TOKEN_PATTERN_RE = re.compile(r"\b(?:(?:sk|pk|rk)[_-]|(?:ghp|github_pat)_)[A-Za-z0-9_-]{12,}\b")
_DIAGNOSTIC_LIMIT = 16_384
_SENSITIVE_KEYS = {
    "authorization",
    "apikey",
    "accesstoken",
    "refreshtoken",
    "token",
    "password",
    "secret",
    "clientsecret",
    "credential",
    "credentials",
}


class ProcessingOutcome(StrEnum):
    PROCESSED = "PROCESSED"
    DUPLICATE = "DUPLICATE"
    STALE = "STALE"


class FailureDisposition(StrEnum):
    RETRY = "RETRY"
    DEAD_LETTER = "DEAD_LETTER"
    STALE = "STALE"


class PermanentEventError(ValueError):
    """Deterministic contract/domain failure that must not be retried."""


class TransientEventError(RuntimeError):
    """Retryable infrastructure/provider failure independent of provider packages."""


class DeferredWorkError(TransientEventError):
    """Temporary operational deferral, not a failed attempt or exhausted retry."""


class StaleWorkError(RuntimeError):
    """Valid but superseded work that is durably classified and acknowledged."""


@dataclass(frozen=True, slots=True)
class WorkerRetryPolicy:
    delays_seconds: tuple[int, ...] = (30, 120, 600, 1800, 7200)
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if not self.delays_seconds or any(value <= 0 for value in self.delays_seconds):
            raise ValueError("worker retry delays must be positive")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("worker retry jitter_ratio must be between 0 and 1")

    @property
    def retry_budget(self) -> int:
        return len(self.delays_seconds)

    @property
    def failure_limit(self) -> int:
        """Failures allowed before exhaustion: one per delay, then one terminal failure."""

        return self.retry_budget + 1

    def next_retry_at(
        self,
        delivery_key: str,
        attempt_number: int,
        *,
        now: datetime,
    ) -> datetime:
        base = self.delays_seconds[min(attempt_number - 1, len(self.delays_seconds) - 1)]
        sample = int(hashlib.sha256(delivery_key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        factor = 1 + ((sample * 2) - 1) * self.jitter_ratio
        return now + timedelta(seconds=base * factor)


class WorkerRetryConfig(BaseModel):
    """Typed runtime configuration for bounded stream-consumer retries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    delays_seconds: tuple[int, ...] = Field(min_length=1)
    jitter_ratio: float = Field(ge=0, le=1)

    def policy(self) -> WorkerRetryPolicy:
        return WorkerRetryPolicy(
            delays_seconds=self.delays_seconds,
            jitter_ratio=self.jitter_ratio,
        )


def load_worker_retry_policy() -> WorkerRetryPolicy:
    settings = AppSettings()
    config = ConfigLoader(settings.config_dir).load_domain_file(
        ConfigDomain.RUNTIME,
        "worker-retry.yaml",
        WorkerRetryConfig,
    )
    return config.policy()


@dataclass(frozen=True, slots=True)
class WorkerBatchResult:
    received: int = 0
    processed: int = 0
    duplicates: int = 0
    ignored: int = 0
    stale: int = 0
    retrying: int = 0
    dead_lettered: int = 0
    failed: int = 0
    failed_message_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _FailureDecision:
    disposition: FailureDisposition
    attempt_number: int


Handler = Callable[[EventEnvelope], ProcessingOutcome | Awaitable[ProcessingOutcome]]


class ReliableMessageProcessor:
    """Classify failures durably and ACK only success, stale, or terminal messages."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        *,
        consumer_group: str,
        handled_event_types: frozenset[EventType],
        retry_policy: WorkerRetryPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.consumer = consumer
        self.session_factory = session_factory
        self.consumer_group = consumer_group
        self.handled_event_types = handled_event_types
        self.retry_policy = retry_policy or load_worker_retry_policy()
        self.clock = clock or (lambda: datetime.now(UTC))

    async def process(
        self,
        messages: Sequence[StreamMessage],
        handler: Handler,
    ) -> WorkerBatchResult:
        processed = duplicates = ignored = stale = retrying = dead_lettered = failed = 0
        failed_ids: list[str] = []
        for message in messages:
            if (
                message.event is not None
                and message.event.event_type not in self.handled_event_types
            ):
                await self.consumer.ack(message)
                ignored += 1
                continue
            preflight = self._preflight(message, now=self.clock())
            if preflight == "terminal":
                await self.consumer.ack(message)
                dead_lettered += 1
                continue
            if preflight == "stale":
                await self.consumer.ack(message)
                stale += 1
                continue
            if preflight == "wait":
                retrying += 1
                continue
            try:
                if message.event is None:
                    raise PermanentEventError(
                        message.decode_error or "Redis stream event envelope is invalid"
                    )
                outcome = handler(message.event)
                if inspect.isawaitable(outcome):
                    outcome = await outcome
            except DeferredWorkError:
                # Operational deferrals leave work pending without consuming the
                # failure budget or marking domain work permanently completed.
                retrying += 1
                continue
            except Exception as exc:
                decision = self._record_failure(message, exc, now=self.clock())
                if decision.disposition is FailureDisposition.STALE:
                    await self.consumer.ack(message)
                    stale += 1
                elif decision.disposition is FailureDisposition.DEAD_LETTER:
                    failed += 1
                    failed_ids.append(message.message_id)
                    await self.consumer.ack(message)
                    dead_lettered += 1
                else:
                    failed += 1
                    failed_ids.append(message.message_id)
                    retrying += 1
                continue

            if outcome is ProcessingOutcome.STALE:
                self._record_failure(
                    message,
                    StaleWorkError("operation is superseded by current durable state"),
                    now=self.clock(),
                )
                await self.consumer.ack(message)
                stale += 1
                continue
            await self.consumer.ack(message)
            if outcome is ProcessingOutcome.DUPLICATE:
                duplicates += 1
            else:
                processed += 1
        return WorkerBatchResult(
            received=len(messages),
            processed=processed,
            duplicates=duplicates,
            ignored=ignored,
            stale=stale,
            retrying=retrying,
            dead_lettered=dead_lettered,
            failed=failed,
            failed_message_ids=tuple(failed_ids),
        )

    async def recover(
        self,
        handler: Handler,
        *,
        min_idle_ms: int,
        start_id: str = "0-0",
    ) -> tuple[str, WorkerBatchResult]:
        """Recover crashed work plus durably scheduled retries without stealing peers' work."""
        next_start, stale_messages = await self.consumer.claim_stale(
            min_idle_ms=min_idle_ms,
            start_id=start_id,
        )
        stale_result = await self.process(stale_messages, handler)
        seen = {message.message_id for message in stale_messages}
        due_ids = self._due_retry_message_ids(now=self.clock(), exclude_message_ids=seen)
        retry_messages = await self.consumer.claim_owned_pending(due_ids) if due_ids else []
        retry_result = await self.process(retry_messages, handler)
        return next_start, _merge_batch_results(stale_result, retry_result)

    def _due_retry_message_ids(
        self,
        *,
        now: datetime,
        exclude_message_ids: set[str] | None = None,
    ) -> tuple[str, ...]:
        latest = (
            select(
                EventProcessingAttempt.delivery_key.label("delivery_key"),
                func.max(EventProcessingAttempt.attempt_number).label("attempt_number"),
            )
            .where(
                EventProcessingAttempt.consumer_group == self.consumer_group,
                EventProcessingAttempt.source_stream == self.consumer.stream,
            )
            .group_by(EventProcessingAttempt.delivery_key)
            .subquery()
        )
        statement = (
            select(EventProcessingAttempt.message_id)
            .join(
                latest,
                (EventProcessingAttempt.delivery_key == latest.c.delivery_key)
                & (EventProcessingAttempt.attempt_number == latest.c.attempt_number),
            )
            .where(
                EventProcessingAttempt.consumer_group == self.consumer_group,
                EventProcessingAttempt.source_stream == self.consumer.stream,
                EventProcessingAttempt.status == EventAttemptStatus.RETRY_PENDING,
                EventProcessingAttempt.next_retry_at.is_not(None),
                EventProcessingAttempt.next_retry_at <= _as_utc(now),
                EventProcessingAttempt.event_id.is_not(None),
                ~exists().where(
                    ProcessedEvent.event_id == EventProcessingAttempt.event_id,
                    ProcessedEvent.consumer_group == self.consumer_group,
                ),
            )
            .order_by(
                EventProcessingAttempt.next_retry_at.asc(),
                EventProcessingAttempt.created_at.asc(),
                EventProcessingAttempt.id.asc(),
            )
            .limit(max(1, int(getattr(self.consumer, "count", 10))))
        )
        if exclude_message_ids:
            statement = statement.where(
                EventProcessingAttempt.message_id.notin_(tuple(exclude_message_ids))
            )
        with self.session_factory() as session:
            return tuple(session.scalars(statement))

    def _preflight(self, message: StreamMessage, *, now: datetime) -> str:
        delivery_key = _delivery_key(message, self.consumer_group)
        with self.session_factory() as session:
            if (
                session.scalar(
                    select(EventDeadLetter.id).where(EventDeadLetter.delivery_key == delivery_key)
                )
                is not None
            ):
                return "terminal"
            latest_attempt = session.execute(
                select(
                    EventProcessingAttempt.status,
                    EventProcessingAttempt.next_retry_at,
                )
                .where(EventProcessingAttempt.delivery_key == delivery_key)
                .order_by(EventProcessingAttempt.attempt_number.desc())
                .limit(1)
            ).one_or_none()
        if latest_attempt is not None and latest_attempt.status == EventAttemptStatus.STALE:
            return "stale"
        if (
            latest_attempt is not None
            and latest_attempt.next_retry_at is not None
            and _as_utc(latest_attempt.next_retry_at) > _as_utc(now)
        ):
            return "wait"
        return "process"

    def _record_failure(
        self,
        message: StreamMessage,
        exc: Exception,
        *,
        now: datetime,
    ) -> _FailureDecision:
        delivery_key = _delivery_key(message, self.consumer_group)
        event_id, event_type, event_payload = _event_identity(message)
        failure_class = _classify(exc, message)
        error_code = type(exc).__name__[:128]
        error_message = _safe_message(exc)
        with self.session_factory() as session, session.begin():
            existing_dead_letter = session.scalar(
                select(EventDeadLetter).where(EventDeadLetter.delivery_key == delivery_key)
            )
            if existing_dead_letter is not None:
                return _FailureDecision(
                    FailureDisposition.DEAD_LETTER,
                    existing_dead_letter.attempt_count,
                )
            attempt_number = (
                session.scalar(
                    select(func.max(EventProcessingAttempt.attempt_number)).where(
                        EventProcessingAttempt.delivery_key == delivery_key
                    )
                )
                or 0
            ) + 1
            stale = failure_class is EventFailureClass.STALE
            terminal = failure_class is EventFailureClass.PERMANENT
            if not terminal and not stale and attempt_number > self.retry_policy.retry_budget:
                failure_class = EventFailureClass.EXHAUSTED
                terminal = True
            next_retry_at = None
            if not terminal and not stale:
                next_retry_at = self.retry_policy.next_retry_at(
                    delivery_key,
                    attempt_number,
                    now=now,
                )
            attempt = EventProcessingAttempt(
                delivery_key=delivery_key,
                event_id=event_id,
                event_type=event_type,
                consumer_group=self.consumer_group,
                source_stream=message.stream,
                message_id=message.message_id,
                attempt_number=attempt_number,
                failure_class=failure_class,
                status=(
                    EventAttemptStatus.STALE
                    if stale
                    else (
                        EventAttemptStatus.DEAD_LETTERED
                        if terminal
                        else EventAttemptStatus.RETRY_PENDING
                    )
                ),
                error_code=error_code,
                error_message=error_message,
                next_retry_at=next_retry_at,
            )
            job_id = None
            session.add(attempt)
            if (
                message.event is not None
                and message.event.event_type is EventType.EVIDENCE_REQUESTED
                and message.event.aggregate_type == "research_run"
            ):
                job = session.get(Job, message.event.aggregate_id)
                if job is not None and job.job_type == "RESEARCH":
                    job_id = job.id
                    if not stale:
                        job.status = "FAILED" if terminal else "PENDING"
                        job.error_code = error_code[:64]
                        job.error_message = error_message
            if terminal:
                session.add(
                    EventDeadLetter(
                        delivery_key=delivery_key,
                        event_id=event_id,
                        event_type=event_type,
                        aggregate_type=(
                            message.event.aggregate_type if message.event is not None else None
                        ),
                        aggregate_id=(
                            message.event.aggregate_id if message.event is not None else None
                        ),
                        job_id=job_id,
                        consumer_group=self.consumer_group,
                        source_stream=message.stream,
                        message_id=message.message_id,
                        attempt_count=attempt_number,
                        failure_class=failure_class,
                        error_code=error_code,
                        error_message=error_message,
                        raw_event=_safe_raw_event(message),
                        raw_event_hash=_raw_event_hash(message),
                        event_payload=_safe_payload(event_payload),
                        failed_at=now,
                    )
                )
        return _FailureDecision(
            (
                FailureDisposition.STALE
                if stale
                else (FailureDisposition.DEAD_LETTER if terminal else FailureDisposition.RETRY)
            ),
            attempt_number,
        )


def _merge_batch_results(*results: WorkerBatchResult) -> WorkerBatchResult:
    return WorkerBatchResult(
        received=sum(result.received for result in results),
        processed=sum(result.processed for result in results),
        duplicates=sum(result.duplicates for result in results),
        ignored=sum(result.ignored for result in results),
        stale=sum(result.stale for result in results),
        retrying=sum(result.retrying for result in results),
        dead_lettered=sum(result.dead_lettered for result in results),
        failed=sum(result.failed for result in results),
        failed_message_ids=tuple(
            message_id for result in results for message_id in result.failed_message_ids
        ),
    )


def _delivery_key(message: StreamMessage, consumer_group: str) -> str:
    if message.event is not None:
        material = f"{message.event.event_id}:{consumer_group}"
    else:
        event_id, _, _ = _event_identity(message)
        material = (
            f"{event_id}:{consumer_group}"
            if event_id is not None
            else f"{message.stream}:{consumer_group}:{message.message_id}:{message.raw_event}"
        )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _event_identity(
    message: StreamMessage,
) -> tuple[UUID | None, str | None, dict[str, Any] | None]:
    if message.event is not None:
        return (
            message.event.event_id,
            message.event.event_type.value,
            message.event.model_dump(mode="json"),
        )
    try:
        payload = json.loads(message.raw_event or "")
    except (TypeError, ValueError):
        return None, None, None
    if not isinstance(payload, dict):
        return None, None, None
    try:
        event_id = UUID(str(payload.get("event_id")))
    except (TypeError, ValueError):
        event_id = None
    event_type = payload.get("event_type")
    return event_id, event_type[:128] if isinstance(event_type, str) else None, payload


def _classify(exc: Exception, message: StreamMessage) -> EventFailureClass:
    if isinstance(exc, StaleWorkError):
        return EventFailureClass.STALE
    if message.event is None or isinstance(exc, (PermanentEventError, ValidationError, ValueError)):
        return EventFailureClass.PERMANENT
    return EventFailureClass.TRANSIENT


def _safe_message(exc: Exception) -> str:
    value = _redact_string(str(exc))
    return value[:1000] or type(exc).__name__


def _redact_string(value: str) -> str:
    value = _SECRET_RE.sub(r"\1=[REDACTED]", value)
    value = _BEARER_RE.sub("Bearer [REDACTED]", value)
    return _TOKEN_PATTERN_RE.sub("[REDACTED]", value)


def _is_sensitive_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
    return normalized in _SENSITIVE_KEYS or normalized.endswith(
        ("authorization", "apikey", "token", "password", "secret", "credential", "credentials")
    )


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _is_sensitive_key(key) else _redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _safe_raw_event(message: StreamMessage) -> str | None:
    raw = message.raw_event
    if raw is None and message.event is not None:
        raw = message.event.model_dump_json()
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        redacted = _redact_string(raw)
    else:
        redacted = json.dumps(_redact_value(parsed), separators=(",", ":"), sort_keys=True)
    return redacted[:_DIAGNOSTIC_LIMIT]


def _raw_event_hash(message: StreamMessage) -> str:
    raw = message.raw_event
    if raw is None and message.event is not None:
        raw = message.event.model_dump_json()
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def _safe_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    redacted = _redact_value(payload)
    serialized = json.dumps(redacted, separators=(",", ":"), sort_keys=True)
    if len(serialized) <= _DIAGNOSTIC_LIMIT:
        return redacted
    return {
        "diagnostic_truncated": True,
        "redacted_payload_hash": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
