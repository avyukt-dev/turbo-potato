"""Durable research planning and evidence-collection workers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID

from news_ai_database import Job, JobAttempt
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
from news_ai_evidence import EvidenceEngine
from sqlalchemy import select
from sqlalchemy.orm import Session

RESEARCH_PLANNING_CONSUMER_GROUP = "research-planner"
EVIDENCE_COLLECTION_CONSUMER_GROUP = "evidence-collector"


ResearchWorkerBatchResult = WorkerBatchResult


class ResearchPlanningWorker:
    """Turn claims.extracted into durable research jobs and evidence.requested events."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        engine: EvidenceEngine,
        *,
        retry_policy: WorkerRetryPolicy | None = None,
    ) -> None:
        expected = stream_for_event(EventType.CLAIMS_EXTRACTED)
        if consumer.stream != expected:
            raise ValueError(f"research planner must read {expected!r}")
        if consumer.group != RESEARCH_PLANNING_CONSUMER_GROUP:
            raise ValueError("research planner consumer group is invalid")
        self.consumer = consumer
        self.session_factory = session_factory
        self.engine = engine
        self.reliability = ReliableMessageProcessor(
            consumer,
            session_factory,
            consumer_group=RESEARCH_PLANNING_CONSUMER_GROUP,
            handled_event_types=frozenset({EventType.CLAIMS_EXTRACTED}),
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
        *,
        retry_policy: WorkerRetryPolicy | None = None,
    ) -> None:
        expected = stream_for_event(EventType.EVIDENCE_REQUESTED)
        if consumer.stream != expected:
            raise ValueError(f"evidence collector must read {expected!r}")
        if consumer.group != EVIDENCE_COLLECTION_CONSUMER_GROUP:
            raise ValueError("evidence collector consumer group is invalid")
        self.consumer = consumer
        self.session_factory = session_factory
        self.engine = engine
        self.reliability = ReliableMessageProcessor(
            consumer,
            session_factory,
            consumer_group=EVIDENCE_COLLECTION_CONSUMER_GROUP,
            handled_event_types=frozenset({EventType.EVIDENCE_REQUESTED}),
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

    async def _handle_event(self, event: EventEnvelope) -> ProcessingOutcome:
        outcome = await self._process_event(event)
        return ProcessingOutcome(outcome.upper())

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
        attempt_id = self._begin_attempt(task.plan.research_run_id)
        try:
            collection = await self.engine.collect(task)
        except Exception as exc:
            self._fail_attempt(attempt_id, exc)
            raise
        with self.session_factory() as session, session.begin():
            if was_processed(
                session,
                event_id=event.event_id,
                consumer_group=EVIDENCE_COLLECTION_CONSUMER_GROUP,
            ):
                self._complete_attempt(session, attempt_id)
                return "duplicate"
            existing = self.engine.existing_collection_result(session, event)
            if existing is not None:
                self._complete_attempt(session, attempt_id)
                mark_processed(
                    session,
                    event_id=event.event_id,
                    consumer_group=EVIDENCE_COLLECTION_CONSUMER_GROUP,
                    result=existing,
                )
                return (
                    "stale"
                    if not existing.get("current_claim_ids") and existing.get("stale_claim_ids")
                    else "processed"
                )
            result = self.engine.persist_collection(session, event, task, collection)
            self._complete_attempt(session, attempt_id)
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=EVIDENCE_COLLECTION_CONSUMER_GROUP,
                result=result.as_handler_result(),
            )
        return "stale" if result.event_id is None and result.stale_claim_ids else "processed"

    def _begin_attempt(self, research_run_id: UUID) -> UUID:
        with self.session_factory() as session, session.begin():
            job = session.scalar(select(Job).where(Job.id == research_run_id).with_for_update())
            if job is None or job.job_type != "RESEARCH":
                raise ValueError("research run disappeared before execution")
            job.attempts += 1
            job.status = "RUNNING"
            attempt = JobAttempt(
                job_id=job.id,
                attempt_number=job.attempts,
                status="RUNNING",
                started_at=datetime.now(UTC),
            )
            session.add(attempt)
            session.flush()
            return attempt.id

    def _fail_attempt(self, attempt_id: UUID, exc: Exception) -> None:
        with self.session_factory() as session, session.begin():
            attempt = session.get(JobAttempt, attempt_id)
            if attempt is None:
                raise RuntimeError("research JobAttempt disappeared after failure") from exc
            job = session.get(Job, attempt.job_id)
            attempt.status = "FAILED"
            attempt.completed_at = datetime.now(UTC)
            attempt.error_code = type(exc).__name__[:64]
            attempt.error_message = f"{type(exc).__name__} during research collection"
            if job is not None:
                job.status = "PENDING"
                job.error_code = attempt.error_code
                job.error_message = attempt.error_message

    @staticmethod
    def _complete_attempt(session: Session, attempt_id: UUID) -> None:
        attempt = session.get(JobAttempt, attempt_id)
        if attempt is None:
            raise RuntimeError("research JobAttempt disappeared before completion")
        attempt.status = "COMPLETED"
        attempt.completed_at = datetime.now(UTC)
