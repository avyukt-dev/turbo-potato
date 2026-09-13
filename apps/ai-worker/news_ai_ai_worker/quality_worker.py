"""Reliable Stage-22 consumer for canonical content.generated events."""

from __future__ import annotations

from collections.abc import Callable, Sequence

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
from news_ai_events.consumer_contracts import QUALITY_CONSUMER_GROUP
from news_ai_events.idempotency import mark_processed, was_processed
from news_ai_events.streams import stream_for_event
from news_ai_quality import QualityAssessmentService
from sqlalchemy.orm import Session


class QualityWorker:
    """Assess outside a DB transaction, then revalidate and commit before ACK."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        service: QualityAssessmentService,
        *,
        retry_policy: WorkerRetryPolicy | None = None,
    ) -> None:
        expected = stream_for_event(EventType.CONTENT_GENERATED)
        if consumer.stream != expected or consumer.group != QUALITY_CONSUMER_GROUP:
            raise ValueError("quality worker must use the canonical content stream/group")
        self.consumer = consumer
        self.session_factory = session_factory
        self.service = service
        self.reliability = ReliableMessageProcessor(
            consumer,
            session_factory,
            consumer_group=QUALITY_CONSUMER_GROUP,
            handled_event_types=frozenset({EventType.CONTENT_GENERATED}),
            retry_policy=retry_policy,
        )

    async def ensure_ready(self) -> None:
        await self.consumer.ensure_group()

    async def run_once(self) -> WorkerBatchResult:
        return await self._process(await self.consumer.read())

    async def recover_once(
        self, *, min_idle_ms: int, start_id: str = "0-0"
    ) -> tuple[str, WorkerBatchResult]:
        cursor, messages = await self.consumer.claim_stale(
            min_idle_ms=min_idle_ms, start_id=start_id
        )
        return cursor, await self._process(messages)

    async def _process(self, messages: Sequence[StreamMessage]) -> WorkerBatchResult:
        return await self.reliability.process(messages, self._handle_event)

    async def _handle_event(self, event: EventEnvelope) -> ProcessingOutcome:
        with self.session_factory() as session:
            if was_processed(
                session, event_id=event.event_id, consumer_group=QUALITY_CONSUMER_GROUP
            ):
                return ProcessingOutcome.DUPLICATE
            context = self.service.load_context(session, event)
            existing = self.service.existing_result(session, context)
        if existing is not None:
            with self.session_factory() as session, session.begin():
                if was_processed(
                    session, event_id=event.event_id, consumer_group=QUALITY_CONSUMER_GROUP
                ):
                    return ProcessingOutcome.DUPLICATE
                mark_processed(
                    session,
                    event_id=event.event_id,
                    consumer_group=QUALITY_CONSUMER_GROUP,
                    result=existing.as_handler_result(),
                )
            return ProcessingOutcome.DUPLICATE
        executions = await self.service.assess(context, event)
        with self.session_factory() as session, session.begin():
            if was_processed(
                session, event_id=event.event_id, consumer_group=QUALITY_CONSUMER_GROUP
            ):
                return ProcessingOutcome.DUPLICATE
            result = self.service.persist(
                session, context=context, event=event, executions=executions
            )
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=QUALITY_CONSUMER_GROUP,
                result=result.as_handler_result(),
            )
        return ProcessingOutcome.PROCESSED if result.created else ProcessingOutcome.DUPLICATE
