"""Claim worker for story intelligence events."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from news_ai_ai import ClaimExtractionService
from news_ai_events import EventEnvelope, EventType, RedisStreamConsumer, StreamMessage
from news_ai_events.idempotency import mark_processed, was_processed
from news_ai_events.streams import stream_for_event
from sqlalchemy.orm import Session

CLAIM_CONSUMER_GROUP = "claim-worker"
_HANDLED_EVENT_TYPES = frozenset({EventType.STORY_CREATED, EventType.STORY_CLUSTERED})


@dataclass(frozen=True, slots=True)
class ClaimWorkerBatchResult:
    received: int = 0
    processed: int = 0
    duplicates: int = 0
    ignored: int = 0
    failed: int = 0
    failed_message_ids: tuple[str, ...] = ()


class ClaimExtractionWorker:
    """Consume story events and persist claim extraction with ACK-after-commit semantics."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        service: ClaimExtractionService,
        *,
        consumer_group: str = CLAIM_CONSUMER_GROUP,
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
        processed = 0
        duplicates = 0
        ignored = 0
        failed_ids: list[str] = []

        for message in messages:
            if message.event.event_type not in _HANDLED_EVENT_TYPES:
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

        return ClaimWorkerBatchResult(
            received=len(messages),
            processed=processed,
            duplicates=duplicates,
            ignored=ignored,
            failed=len(failed_ids),
            failed_message_ids=tuple(failed_ids),
        )

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
