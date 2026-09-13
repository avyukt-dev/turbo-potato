"""Claim worker for story intelligence events."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID

from news_ai_ai import ClaimExtractionService
from news_ai_events import (
    EventEnvelope,
    EventType,
    ProcessingOutcome,
    RedisStreamConsumer,
    ReliableMessageProcessor,
    StreamMessage,
    WorkerBatchResult,
    WorkerRetryPolicy,
)
from news_ai_events.consumer_contracts import CLAIM_CONSUMER_GROUP
from news_ai_events.idempotency import mark_processed, was_processed
from news_ai_events.streams import stream_for_event
from sqlalchemy.orm import Session

_HANDLED_EVENT_TYPES = frozenset({EventType.STORY_CREATED, EventType.STORY_CLUSTERED})


ClaimWorkerBatchResult = WorkerBatchResult


class ClaimExtractionWorker:
    """Consume story events and persist claim extraction with ACK-after-commit semantics."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        service: ClaimExtractionService,
        *,
        consumer_group: str = CLAIM_CONSUMER_GROUP,
        retry_policy: WorkerRetryPolicy | None = None,
    ) -> None:
        expected_stream = stream_for_event(EventType.STORY_CREATED)
        if consumer.stream != expected_stream:
            raise ValueError(
                f"claim consumer must read {expected_stream!r}, got {consumer.stream!r}"
            )
        if consumer.group != consumer_group:
            raise ValueError(
                f"claim consumer group must be {consumer_group!r}, got {consumer.group!r}"
            )
        self.consumer = consumer
        self.session_factory = session_factory
        self.service = service
        self.consumer_group = consumer_group
        self.reliability = ReliableMessageProcessor(
            consumer,
            session_factory,
            consumer_group=consumer_group,
            handled_event_types=_HANDLED_EVENT_TYPES,
            retry_policy=retry_policy,
        )

    async def ensure_ready(self) -> None:
        await self.consumer.ensure_group()

    async def run_once(self) -> ClaimWorkerBatchResult:
        return await self._process_messages(await self.consumer.read())

    async def recover_once(
        self,
        *,
        min_idle_ms: int,
        start_id: str = "0-0",
    ) -> tuple[str, ClaimWorkerBatchResult]:
        next_start, messages = await self.consumer.claim_stale(
            min_idle_ms=min_idle_ms,
            start_id=start_id,
        )
        return next_start, await self._process_messages(messages)

    async def _process_messages(self, messages: Sequence[StreamMessage]) -> ClaimWorkerBatchResult:
        return await self.reliability.process(messages, self._handle_event)

    async def _handle_event(self, event: EventEnvelope) -> ProcessingOutcome:
        return ProcessingOutcome((await self._process_event(event)).upper())

    async def _process_event(self, event: EventEnvelope) -> str:
        story_id = _story_id(event)

        with self.session_factory() as session:
            if was_processed(
                session,
                event_id=event.event_id,
                consumer_group=self.consumer_group,
            ):
                return "duplicate"
            context = self.service.load_context(session, story_id)
            existing = self.service.existing_result(
                session,
                story_id=story_id,
                context_hash=context.context_hash,
            )

        if existing is not None:
            with self.session_factory() as session, session.begin():
                if was_processed(
                    session,
                    event_id=event.event_id,
                    consumer_group=self.consumer_group,
                ):
                    return "duplicate"
                mark_processed(
                    session,
                    event_id=event.event_id,
                    consumer_group=self.consumer_group,
                    result=existing,
                )
            return "processed"

        execution = await self.service.generate(context, event)

        with self.session_factory() as session, session.begin():
            if was_processed(
                session,
                event_id=event.event_id,
                consumer_group=self.consumer_group,
            ):
                return "duplicate"
            result = self.service.persist(
                session,
                context=context,
                triggering_event=event,
                execution=execution,
            )
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=self.consumer_group,
                result=result.as_handler_result(),
            )
        return "processed"


def _story_id(event: EventEnvelope) -> UUID:
    if event.event_type not in _HANDLED_EVENT_TYPES:
        raise ValueError(f"claim worker does not handle event type {event.event_type.value}")
    if event.aggregate_type != "story":
        raise ValueError("claim worker story event must use aggregate_type='story'")
    payload_story_id = event.payload.get("story_id")
    if payload_story_id is None:
        raise ValueError("claim worker story event is missing payload story_id")
    story_id = UUID(str(payload_story_id))
    if story_id != event.aggregate_id:
        raise ValueError("story event payload story_id must match aggregate_id")
    return story_id
