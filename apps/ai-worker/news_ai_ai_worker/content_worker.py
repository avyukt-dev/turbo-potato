"""Reliable Stage-21 consumer for canonical content.requested events."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from news_ai_content import ContentGenerationService
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
from news_ai_events.idempotency import mark_processed, was_processed
from news_ai_events.streams import stream_for_event
from sqlalchemy.orm import Session

CONTENT_CONSUMER_GROUP = "content-worker"


class ContentGenerationWorker:
    """Generate content outside a DB transaction, then revalidate and commit before ACK."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        service: ContentGenerationService,
        *,
        retry_policy: WorkerRetryPolicy | None = None,
    ) -> None:
        expected = stream_for_event(EventType.CONTENT_REQUESTED)
        if consumer.stream != expected or consumer.group != CONTENT_CONSUMER_GROUP:
            raise ValueError("content worker must use the canonical content stream/group")
        self.consumer = consumer
        self.session_factory = session_factory
        self.service = service
        self.reliability = ReliableMessageProcessor(
            consumer,
            session_factory,
            consumer_group=CONTENT_CONSUMER_GROUP,
            handled_event_types=frozenset({EventType.CONTENT_REQUESTED}),
            retry_policy=retry_policy,
        )

    async def ensure_ready(self) -> None:
        await self.consumer.ensure_group()

    async def run_once(self) -> WorkerBatchResult:
        return await self._process_messages(await self.consumer.read())

    async def recover_once(
        self, *, min_idle_ms: int, start_id: str = "0-0"
    ) -> tuple[str, WorkerBatchResult]:
        next_start, messages = await self.consumer.claim_stale(
            min_idle_ms=min_idle_ms, start_id=start_id
        )
        return next_start, await self._process_messages(messages)

    async def _process_messages(self, messages: Sequence[StreamMessage]) -> WorkerBatchResult:
        return await self.reliability.process(messages, self._handle_event)

    async def _handle_event(self, event: EventEnvelope) -> ProcessingOutcome:
        with self.session_factory() as session:
            if was_processed(
                session, event_id=event.event_id, consumer_group=CONTENT_CONSUMER_GROUP
            ):
                return ProcessingOutcome.DUPLICATE
            context = self.service.load_context(session, event)
            existing = self.service.existing_result(session, context)

        if existing is not None:
            with self.session_factory() as session, session.begin():
                if was_processed(
                    session, event_id=event.event_id, consumer_group=CONTENT_CONSUMER_GROUP
                ):
                    return ProcessingOutcome.DUPLICATE
                mark_processed(
                    session,
                    event_id=event.event_id,
                    consumer_group=CONTENT_CONSUMER_GROUP,
                    result=existing.as_handler_result(),
                )
            return ProcessingOutcome.DUPLICATE

        execution = await self.service.generate(context, event)
        with self.session_factory() as session, session.begin():
            if was_processed(
                session, event_id=event.event_id, consumer_group=CONTENT_CONSUMER_GROUP
            ):
                return ProcessingOutcome.DUPLICATE
            result = self.service.persist(
                session, context=context, event=event, execution=execution
            )
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=CONTENT_CONSUMER_GROUP,
                result=result.as_handler_result(),
            )
        return ProcessingOutcome.PROCESSED if result.created else ProcessingOutcome.DUPLICATE
