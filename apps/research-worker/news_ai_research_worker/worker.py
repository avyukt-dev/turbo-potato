"""Durable research planning and evidence-collection workers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from news_ai_events import EventEnvelope, EventType, RedisStreamConsumer, StreamMessage
from news_ai_events.idempotency import mark_processed, was_processed
from news_ai_events.streams import stream_for_event
from news_ai_evidence import EvidenceEngine
from sqlalchemy.orm import Session

RESEARCH_PLANNING_CONSUMER_GROUP = "research-planner"
EVIDENCE_COLLECTION_CONSUMER_GROUP = "evidence-collector"


@dataclass(frozen=True, slots=True)
class ResearchWorkerBatchResult:
    received: int = 0
    processed: int = 0
    duplicates: int = 0
    ignored: int = 0
    failed: int = 0
    failed_message_ids: tuple[str, ...] = ()


class ResearchPlanningWorker:
    """Turn claims.extracted into durable research jobs and evidence.requested events."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        engine: EvidenceEngine,
    ) -> None:
        expected = stream_for_event(EventType.CLAIMS_EXTRACTED)
        if consumer.stream != expected:
            raise ValueError(f"research planner must read {expected!r}")
        if consumer.group != RESEARCH_PLANNING_CONSUMER_GROUP:
            raise ValueError("research planner consumer group is invalid")
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
            if message.event.event_type != EventType.CLAIMS_EXTRACTED:
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
                consumer_group=RESEARCH_PLANNING_CONSUMER_GROUP,
            ):
                return True
            result = self.engine.request_research(session, event)
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=RESEARCH_PLANNING_CONSUMER_GROUP,
                result=result.as_handler_result(),
            )
        return not result.created


class EvidenceCollectionWorker:
    """Execute external searches outside DB transactions and persist results atomically."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        engine: EvidenceEngine,
    ) -> None:
        expected = stream_for_event(EventType.EVIDENCE_REQUESTED)
        if consumer.stream != expected:
            raise ValueError(f"evidence collector must read {expected!r}")
        if consumer.group != EVIDENCE_COLLECTION_CONSUMER_GROUP:
            raise ValueError("evidence collector consumer group is invalid")
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
            if message.event.event_type != EventType.EVIDENCE_REQUESTED:
                await self.consumer.ack(message)
                ignored += 1
                continue
            try:
                outcome = await self._process_event(message.event)
            except Exception:
                failed_ids.append(message.message_id)
                continue
            await self.consumer.ack(message)
            if outcome == "duplicate":
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

    async def _process_event(self, event: EventEnvelope) -> str:
        with self.session_factory() as session:
            if was_processed(
                session,
                event_id=event.event_id,
                consumer_group=EVIDENCE_COLLECTION_CONSUMER_GROUP,
            ):
                return "duplicate"
            existing = self.engine.existing_collection_result(session, event)
            if existing is not None:
                task = None
            else:
                task = self.engine.load_collection_task(session, event)

        if existing is not None:
            with self.session_factory() as session, session.begin():
                if was_processed(
                    session,
                    event_id=event.event_id,
                    consumer_group=EVIDENCE_COLLECTION_CONSUMER_GROUP,
                ):
                    return "duplicate"
                mark_processed(
                    session,
                    event_id=event.event_id,
                    consumer_group=EVIDENCE_COLLECTION_CONSUMER_GROUP,
                    result=existing,
                )
            return "processed"

        assert task is not None
        collection = await self.engine.collect(task)
        with self.session_factory() as session, session.begin():
            if was_processed(
                session,
                event_id=event.event_id,
                consumer_group=EVIDENCE_COLLECTION_CONSUMER_GROUP,
            ):
                return "duplicate"
            existing = self.engine.existing_collection_result(session, event)
            if existing is not None:
                mark_processed(
                    session,
                    event_id=event.event_id,
                    consumer_group=EVIDENCE_COLLECTION_CONSUMER_GROUP,
                    result=existing,
                )
                return "processed"
            result = self.engine.persist_collection(session, event, task, collection)
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=EVIDENCE_COLLECTION_CONSUMER_GROUP,
                result=result.as_handler_result(),
            )
        return "processed"
