"""Processor worker for durable handling of ``article.normalized`` events.

Redis owns delivery only. Each message is validated, handed to the next processor stage inside a
PostgreSQL transaction, marked durably as processed, and ACKed only after that transaction commits.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from news_ai_database import Article, ArticleDiscovery, ArticleVersion, EventOutbox
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
from news_ai_events.consumer_contracts import (
    NORMALIZER_CONSUMER_GROUP,
    PROCESSOR_CONSUMER_GROUP,
)
from news_ai_events.idempotency import mark_processed, was_processed
from news_ai_events.payloads import ArticleDiscoveredV1, ArticleNormalizedV1
from news_ai_events.streams import stream_for_event
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ArticleNormalizationInput
from .normalizer import ArticleNormalizer
from .persistence import ArticlePersistenceService


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


ProcessorBatchResult = WorkerBatchResult


class ProcessorEventWorker:
    """Consume normalized-article events with durable idempotency and ACK-after-commit semantics."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        handler: ArticleNormalizedHandler,
        *,
        consumer_group: str = PROCESSOR_CONSUMER_GROUP,
        retry_policy: WorkerRetryPolicy | None = None,
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
        self.reliability = ReliableMessageProcessor(
            consumer,
            session_factory,
            consumer_group=consumer_group,
            handled_event_types=frozenset({EventType.ARTICLE_NORMALIZED}),
            retry_policy=retry_policy,
        )

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
        return await self.reliability.process(messages, self._handle_event)

    def _handle_event(self, event: EventEnvelope) -> ProcessingOutcome:
        return (
            ProcessingOutcome.DUPLICATE
            if self._process_event(event)
            else ProcessingOutcome.PROCESSED
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


class NormalizerEventWorker:
    """Consume durable discovery inputs and emit article.normalized after persistence."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        session_factory: Callable[[], Session],
        normalizer: ArticleNormalizer | None = None,
        *,
        retry_policy: WorkerRetryPolicy | None = None,
    ) -> None:
        expected_stream = stream_for_event(EventType.ARTICLE_DISCOVERED)
        if consumer.stream != expected_stream:
            raise ValueError(
                f"normalizer consumer must read {expected_stream!r}, got {consumer.stream!r}"
            )
        if consumer.group != NORMALIZER_CONSUMER_GROUP:
            raise ValueError("normalizer consumer group is invalid")
        self.consumer = consumer
        self.session_factory = session_factory
        self.normalizer = normalizer or ArticleNormalizer()
        self.reliability = ReliableMessageProcessor(
            consumer,
            session_factory,
            consumer_group=NORMALIZER_CONSUMER_GROUP,
            handled_event_types=frozenset({EventType.ARTICLE_DISCOVERED}),
            retry_policy=retry_policy,
        )

    async def ensure_ready(self) -> None:
        await self.consumer.ensure_group()

    async def run_once(self) -> ProcessorBatchResult:
        return await self._process_messages(await self.consumer.read())

    async def recover_once(
        self,
        *,
        min_idle_ms: int,
        start_id: str = "0-0",
    ) -> tuple[str, ProcessorBatchResult]:
        next_start, messages = await self.consumer.claim_stale(
            min_idle_ms=min_idle_ms,
            start_id=start_id,
        )
        return next_start, await self._process_messages(messages)

    async def _process_messages(self, messages: Sequence[StreamMessage]) -> ProcessorBatchResult:
        return await self.reliability.process(messages, self._handle_event)

    def _handle_event(self, event: EventEnvelope) -> ProcessingOutcome:
        return (
            ProcessingOutcome.DUPLICATE
            if self._process_event(event)
            else ProcessingOutcome.PROCESSED
        )

    def _process_event(self, event: EventEnvelope) -> bool:
        payload = ArticleDiscoveredV1.model_validate(event.payload)
        if event.aggregate_type != "article" or payload.article_id != event.aggregate_id:
            raise ValueError("article.discovered aggregate does not match payload")
        with self.session_factory() as session, session.begin():
            if was_processed(
                session,
                event_id=event.event_id,
                consumer_group=NORMALIZER_CONSUMER_GROUP,
            ):
                return True
            discovery = session.scalar(
                select(ArticleDiscovery)
                .where(ArticleDiscovery.event_id == event.event_id)
                .with_for_update()
            )
            article = session.get(Article, payload.article_id)
            if discovery is None and article is not None:
                legacy = session.scalar(
                    select(EventOutbox)
                    .where(
                        EventOutbox.event_type == EventType.ARTICLE_NORMALIZED.value,
                        EventOutbox.aggregate_id == article.id,
                        EventOutbox.causation_id == event.event_id,
                    )
                    .limit(1)
                )
                if legacy is not None:
                    normalized_payload = ArticleNormalizedV1.model_validate(legacy.payload)
                    legacy_version = session.get(
                        ArticleVersion, normalized_payload.article_version_id
                    )
                    if (
                        normalized_payload.article_id == article.id
                        and legacy_version is not None
                        and legacy_version.article_id == article.id
                        and legacy_version.content_hash == normalized_payload.content_hash
                    ):
                        mark_processed(
                            session,
                            event_id=event.event_id,
                            consumer_group=NORMALIZER_CONSUMER_GROUP,
                            result={
                                "article_id": str(article.id),
                                "article_version_id": str(normalized_payload.article_version_id),
                                "legacy_completed": True,
                                "event_id": str(legacy.event_id),
                            },
                        )
                        return True
            if discovery is None or article is None or discovery.article_id != article.id:
                raise ValueError("article.discovered references missing durable discovery input")
            if (
                article.source_id != payload.source_id
                or article.canonical_url != payload.canonical_url
            ):
                raise ValueError(
                    "article.discovered payload does not match durable article identity"
                )
            normalized = self.normalizer.normalize(
                ArticleNormalizationInput.model_validate(discovery.raw_payload)
            )
            if normalized.canonical_url != article.canonical_url:
                raise ValueError("durable discovery URL normalizes to another Article identity")
            result = ArticlePersistenceService(session).persist(
                normalized,
                correlation_id=event.correlation_id,
                causation_id=event.event_id,
                discovery_id=discovery.id,
            )
            mark_processed(
                session,
                event_id=event.event_id,
                consumer_group=NORMALIZER_CONSUMER_GROUP,
                result={
                    "article_id": str(result.article_id),
                    "article_version_id": str(result.version_id),
                    "created_version": result.created_version,
                    "event_id": str(result.event_id) if result.event_id is not None else None,
                },
            )
        return not result.created_version


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
