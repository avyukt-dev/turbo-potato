"""Durable fact-check and story-verification workers."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from news_ai_events import EventEnvelope, EventType, RedisStreamConsumer, StreamMessage
from news_ai_events.idempotency import mark_processed, was_processed
from news_ai_events.streams import stream_for_event
from news_ai_evidence import FactCheckEngine
from sqlalchemy.orm import Session

from .worker import ResearchWorkerBatchResult

FACT_CHECK_CONSUMER_GROUP = "fact-checker"
STORY_VERIFICATION_CONSUMER_GROUP = "story-verifier"


class FactCheckWorker:
    """Turn evidence.collected into durable claim verdicts and fact_check.completed."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        engine: FactCheckEngine,
    ) -> None:
        expected = stream_for_event(EventType.EVIDENCE_COLLECTED)
        if consumer.stream != expected:
            raise ValueError(f"fact checker must read {expected!r}")
        if consumer.group != FACT_CHECK_CONSUMER_GROUP:
            raise ValueError("fact checker consumer group is invalid")
        self.consumer = consumer
        self.session_factory = session_factory
        self.engine = engine

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
        processed = duplicates = ignored = 0
        failed_ids: list[str] = []
        for message in messages:
            if message.event.event_type != EventType.EVIDENCE_COLLECTED:
                await self.consumer.ack(message)
                ignored += 1
                continue
            try:
                duplicate = self._process_event(message.event)
            except Exception:
                failed_ids.append(message.message_id)
                continue
            await self.consumer.ack(message)
            if duplicate:
                duplicates += 1
            else:
                processed += 1
        return ResearchWorkerBatchResult(
            received=len(messages),
            processed=processed,
            duplicates=duplicates,
            ignored=ignored,
            failed=len(failed_ids),
            failed_message_ids=tuple(failed_ids),
        )

    def _process_event(self, event: EventEnvelope) -> bool:
        with self.session_factory() as session, session.begin():
            if was_processed(
                session,
                event_id=event.event_id,
                consumer_group=FACT_CHECK_CONSUMER_GROUP,
            ):
                return True
            result = self.engine.verify_evidence_collection(session, event)
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=FACT_CHECK_CONSUMER_GROUP,
                result=result.as_handler_result(),
            )
        return result.created_count == 0


class StoryVerificationWorker:
    """Mark the verification stage complete only after durable fact-check persistence."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        engine: FactCheckEngine,
    ) -> None:
        expected = stream_for_event(EventType.FACT_CHECK_COMPLETED)
        if consumer.stream != expected:
            raise ValueError(f"story verifier must read {expected!r}")
        if consumer.group != STORY_VERIFICATION_CONSUMER_GROUP:
            raise ValueError("story verifier consumer group is invalid")
        self.consumer = consumer
        self.session_factory = session_factory
        self.engine = engine

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
        processed = duplicates = ignored = 0
        failed_ids: list[str] = []
        for message in messages:
            if message.event.event_type != EventType.FACT_CHECK_COMPLETED:
                await self.consumer.ack(message)
                ignored += 1
                continue
            try:
                duplicate = self._process_event(message.event)
            except Exception:
                failed_ids.append(message.message_id)
                continue
            await self.consumer.ack(message)
            if duplicate:
                duplicates += 1
            else:
                processed += 1
        return ResearchWorkerBatchResult(
            received=len(messages),
            processed=processed,
            duplicates=duplicates,
            ignored=ignored,
            failed=len(failed_ids),
            failed_message_ids=tuple(failed_ids),
        )

    def _process_event(self, event: EventEnvelope) -> bool:
        with self.session_factory() as session, session.begin():
            if was_processed(
                session,
                event_id=event.event_id,
                consumer_group=STORY_VERIFICATION_CONSUMER_GROUP,
            ):
                return True
            result = self.engine.mark_story_verified(session, event)
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=STORY_VERIFICATION_CONSUMER_GROUP,
                result=result.as_handler_result(),
            )
        return result.ready and not result.created
