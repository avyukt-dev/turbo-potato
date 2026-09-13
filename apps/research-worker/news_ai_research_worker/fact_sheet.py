"""Durable worker that snapshots verified stories into immutable Fact Sheets."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from news_ai_events import (
    EventEnvelope,
    EventType,
    ProcessingOutcome,
    RedisStreamConsumer,
    ReliableMessageProcessor,
    StreamMessage,
    WorkerRetryPolicy,
)
from news_ai_events.consumer_contracts import FACT_SHEET_CONSUMER_GROUP
from news_ai_events.idempotency import mark_processed, was_processed
from news_ai_events.streams import stream_for_event
from news_ai_evidence import FactSheetGenerator
from sqlalchemy.orm import Session

from .worker import ResearchWorkerBatchResult


class FactSheetWorker:
    """Build a Fact Sheet after verification and ACK only after durable commit."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        generator: FactSheetGenerator,
        *,
        retry_policy: WorkerRetryPolicy | None = None,
    ) -> None:
        expected = stream_for_event(EventType.STORY_VERIFIED)
        if consumer.stream != expected:
            raise ValueError(f"Fact Sheet worker must read {expected!r}")
        if consumer.group != FACT_SHEET_CONSUMER_GROUP:
            raise ValueError("Fact Sheet worker consumer group is invalid")
        self.consumer = consumer
        self.session_factory = session_factory
        self.generator = generator
        self.reliability = ReliableMessageProcessor(
            consumer,
            session_factory,
            consumer_group=FACT_SHEET_CONSUMER_GROUP,
            handled_event_types=frozenset({EventType.STORY_VERIFIED}),
            retry_policy=retry_policy,
        )

    async def ensure_ready(self) -> None:
        await self.consumer.ensure_group()

    async def run_once(self) -> ResearchWorkerBatchResult:
        return await self._process_messages(await self.consumer.read())

    async def recover_once(
        self,
        *,
        min_idle_ms: int,
        start_id: str = "0-0",
    ) -> tuple[str, ResearchWorkerBatchResult]:
        next_start, messages = await self.consumer.claim_stale(
            min_idle_ms=min_idle_ms,
            start_id=start_id,
        )
        return next_start, await self._process_messages(messages)

    async def _process_messages(
        self,
        messages: Sequence[StreamMessage],
    ) -> ResearchWorkerBatchResult:
        return await self.reliability.process(messages, self._handle_event)

    def _handle_event(self, event: EventEnvelope) -> ProcessingOutcome:
        return (
            ProcessingOutcome.DUPLICATE
            if self._process_event(event)
            else ProcessingOutcome.PROCESSED
        )

    def _process_event(self, event: EventEnvelope) -> bool:
        with self.session_factory() as session, session.begin():
            if was_processed(
                session,
                event_id=event.event_id,
                consumer_group=FACT_SHEET_CONSUMER_GROUP,
            ):
                return True
            result = self.generator.generate(session, event)
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=FACT_SHEET_CONSUMER_GROUP,
                result=result.as_handler_result(),
            )
        return not result.created
