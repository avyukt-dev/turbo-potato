"""Processor worker for durable handling of ``article.normalized`` events.

Redis owns delivery only. Each message is validated, handed to the next processor stage inside a
PostgreSQL transaction, marked durably as processed, and ACKed only after that transaction commits.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from news_ai_database import Article, ArticleVersion
from news_ai_events import (
    EventEnvelope,
    EventType,
    RedisStreamConsumer,
    StreamMessage,
)
from news_ai_events.idempotency import mark_processed, was_processed
from news_ai_events.payloads import ArticleNormalizedV1
from news_ai_events.streams import stream_for_event
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

PROCESSOR_CONSUMER_GROUP = "processor"


class ArticleNormalizedWorkItem(BaseModel):
    """Validated handoff contract from article persistence to story processing."""

    model_config = ConfigDict(extra="forbid")

    article_id: UUID
    article_version_id: UUID
    version_number: int = Field(ge=1)
    source_id: UUID
    source_feed_id: UUID | None = None
    canonical_url: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: datetime | None = None
    retrieved_at: datetime

    @field_validator("published_at", "retrieved_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("event timestamps must be timezone-aware")
        return value


ArticleNormalizedHandler = Callable[
    [Session, EventEnvelope, ArticleNormalizedWorkItem],
    dict[str, Any] | None,
]


@dataclass(frozen=True, slots=True)
class ProcessorBatchResult:
    received: int = 0
    processed: int = 0
    duplicates: int = 0
    failed: int = 0
    failed_message_ids: tuple[str, ...] = ()


class ProcessorEventWorker:
    """Consume normalized-article events with durable idempotency and ACK-after-commit semantics."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        handler: ArticleNormalizedHandler,
        *,
        consumer_group: str = PROCESSOR_CONSUMER_GROUP,
    ) -> None:
        expected_stream = stream_for_event(EventType.ARTICLE_NORMALIZED)
        if consumer.stream != expected_stream:
            raise ValueError(
                f"processor consumer must read {expected_stream!r}, got {consumer.stream!r}"
            )
        if consumer.group != consumer_group:
            raise ValueError(
                f"processor consumer group must be {consumer_group!r}, got {consumer.group!r}"
            )
        self.consumer = consumer
        self.session_factory = session_factory
        self.handler = handler
        self.consumer_group = consumer_group

    async def ensure_ready(self) -> None:
        await self.consumer.ensure_group()

    async def run_once(self) -> ProcessorBatchResult:
        """Process one batch of new messages."""

        return await self._process_messages(await self.consumer.read())

    async def recover_once(
        self,
        *,
        min_idle_ms: int,
        start_id: str = "0-0",
    ) -> tuple[str, ProcessorBatchResult]:
        """Claim and process one batch of stale pending messages."""

        next_start, messages = await self.consumer.claim_stale(
            min_idle_ms=min_idle_ms,
            start_id=start_id,
        )
        return next_start, await self._process_messages(messages)

    async def _process_messages(self, messages: Sequence[StreamMessage]) -> ProcessorBatchResult:
        processed = 0
        duplicates = 0
        failed_ids: list[str] = []

        for message in messages:
            try:
                duplicate = self._process_event(message.event)
            except Exception:
                # Keep the message pending. Recovery/retry policy owns the next attempt.
                failed_ids.append(message.message_id)
                continue

            await self.consumer.ack(message)
            if duplicate:
                duplicates += 1
            else:
                processed += 1

        return ProcessorBatchResult(
            received=len(messages),
            processed=processed,
            duplicates=duplicates,
            failed=len(failed_ids),
            failed_message_ids=tuple(failed_ids),
        )

    def _process_event(self, event: EventEnvelope) -> bool:
        if event.event_type != EventType.ARTICLE_NORMALIZED:
            raise ValueError(f"processor worker does not handle event type {event.event_type!s}")

        payload = ArticleNormalizedV1.model_validate(event.payload)
        if payload.article_id != event.aggregate_id:
            raise ValueError("article.normalized payload article_id must match aggregate_id")

        with self.session_factory() as session, session.begin():
            if was_processed(
                session,
                event_id=event.event_id,
                consumer_group=self.consumer_group,
            ):
                return True

            work_item = _load_work_item(session, payload)
            result = self.handler(session, event, work_item)
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=self.consumer_group,
                result=result,
            )

        return False


def _load_work_item(session: Session, payload: ArticleNormalizedV1) -> ArticleNormalizedWorkItem:
    article = session.get(Article, payload.article_id)
    version = session.get(ArticleVersion, payload.article_version_id)
    if article is None or version is None or version.article_id != payload.article_id:
        raise ValueError(
            "article.normalized references missing or mismatched durable article state"
        )
    if version.content_hash != payload.content_hash:
        raise ValueError("article.normalized content_hash does not match durable article version")
    if article.title != payload.title or article.language != payload.language:
        raise ValueError("article.normalized metadata does not match durable article state")
    metadata = dict(version.version_metadata or {})
    source_feed_id = metadata.get("source_feed_id")
    return ArticleNormalizedWorkItem(
        article_id=article.id,
        article_version_id=version.id,
        version_number=version.version_number,
        source_id=article.source_id,
        source_feed_id=UUID(str(source_feed_id)) if source_feed_id is not None else None,
        canonical_url=article.canonical_url,
        content_hash=version.content_hash,
        published_at=_as_utc(article.published_at),
        retrieved_at=_as_utc(version.retrieved_at),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
