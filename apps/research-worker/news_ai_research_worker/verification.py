"""Durable fact-check and story-verification workers."""

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
from news_ai_events.consumer_contracts import (
    FACT_CHECK_CONSUMER_GROUP,
    STORY_VERIFICATION_CONSUMER_GROUP,
)
from news_ai_events.idempotency import mark_processed, was_processed
from news_ai_events.streams import stream_for_event
from news_ai_evidence import FactCheckEngine
from sqlalchemy.orm import Session

from .worker import ResearchWorkerBatchResult


class FactCheckWorker:
    """Turn evidence.collected into durable claim verdicts and fact_check.completed."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        engine: FactCheckEngine,
        *,
        retry_policy: WorkerRetryPolicy | None = None,
    ) -> None:
        expected = stream_for_event(EventType.EVIDENCE_COLLECTED)
        if consumer.stream != expected:
            raise ValueError(f"fact checker must read {expected!r}")
        if consumer.group != FACT_CHECK_CONSUMER_GROUP:
            raise ValueError("fact checker consumer group is invalid")
        self.consumer = consumer
        self.session_factory = session_factory
        self.engine = engine
        self.reliability = ReliableMessageProcessor(
            consumer,
            session_factory,
            consumer_group=FACT_CHECK_CONSUMER_GROUP,
            handled_event_types=frozenset({EventType.EVIDENCE_COLLECTED}),
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
        duplicate, stale = self._process_event(event)
        if stale:
            return ProcessingOutcome.STALE
        return ProcessingOutcome.DUPLICATE if duplicate else ProcessingOutcome.PROCESSED

    def _process_event(self, event: EventEnvelope) -> tuple[bool, bool]:
        with self.session_factory() as session, session.begin():
            if was_processed(
                session,
                event_id=event.event_id,
                consumer_group=FACT_CHECK_CONSUMER_GROUP,
            ):
                return True, False
            result = self.engine.verify_evidence_collection(session, event)
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=FACT_CHECK_CONSUMER_GROUP,
                result=result.as_handler_result(),
            )
        return result.created_count == 0, bool(result.stale_claim_ids and not result.fact_check_ids)


class StoryVerificationWorker:
    """Mark the verification stage complete only after durable fact-check persistence."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        engine: FactCheckEngine,
        *,
        retry_policy: WorkerRetryPolicy | None = None,
    ) -> None:
        expected = stream_for_event(EventType.FACT_CHECK_COMPLETED)
        if consumer.stream != expected:
            raise ValueError(f"story verifier must read {expected!r}")
        if consumer.group != STORY_VERIFICATION_CONSUMER_GROUP:
            raise ValueError("story verifier consumer group is invalid")
        self.consumer = consumer
        self.session_factory = session_factory
        self.engine = engine
        self.reliability = ReliableMessageProcessor(
            consumer,
            session_factory,
            consumer_group=STORY_VERIFICATION_CONSUMER_GROUP,
            handled_event_types=frozenset({EventType.FACT_CHECK_COMPLETED}),
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
        duplicate, stale = self._process_event(event)
        if stale:
            return ProcessingOutcome.STALE
        return ProcessingOutcome.DUPLICATE if duplicate else ProcessingOutcome.PROCESSED

    def _process_event(self, event: EventEnvelope) -> tuple[bool, bool]:
        with self.session_factory() as session, session.begin():
            if was_processed(
                session,
                event_id=event.event_id,
                consumer_group=STORY_VERIFICATION_CONSUMER_GROUP,
            ):
                return True, False
            result = self.engine.mark_story_verified(session, event)
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=STORY_VERIFICATION_CONSUMER_GROUP,
                result=result.as_handler_result(),
            )
        return result.ready and not result.created, result.stale
