"""Config-driven feed polling and ingestion orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from news_ai_database import SourceFeed
from sqlalchemy import select
from sqlalchemy.orm import Session

from .base import FeedCollector
from .models import CollectedArticle, CollectionFailure, FeedDefinition, FeedFetchResult
from .registry import SourceConfigSnapshot, SourceRegistryService
from .service import CollectorService


class SourceSnapshotLoader(Protocol):
    def load(self) -> SourceConfigSnapshot: ...


class ArticleHandler(Protocol):
    def __call__(
        self,
        session: Session,
        article: CollectedArticle,
        *,
        retrieved_at: datetime,
    ) -> object: ...


SessionFactory = Callable[[], Session]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class CollectionCycleResult:
    active_feed_count: int
    due_feed_count: int
    claimed_feed_count: int
    skipped_inflight_count: int
    fetched_feed_count: int
    not_modified_count: int
    fetched_article_count: int
    processed_article_count: int
    failures: list[CollectionFailure] = field(default_factory=list)


class CollectorScheduler:
    """Poll due feeds without overlapping work for the same feed in this process."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        snapshot_loader: SourceSnapshotLoader,
        collector: FeedCollector,
        article_handler: ArticleHandler,
        clock: Clock | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.snapshot_loader = snapshot_loader
        self.collector = collector
        self.article_handler = article_handler
        self.clock = clock or (lambda: datetime.now(UTC))
        self._inflight: set[UUID] = set()
        self._inflight_lock = asyncio.Lock()

    async def run_cycle(self) -> CollectionCycleResult:
        snapshot = self.snapshot_loader.load()
        now = self._now()
        feeds = self._sync_and_load_feeds(snapshot)
        due = [feed for feed in feeds if self._is_due(feed, now)]
        claimed, skipped = await self._claim_feeds(due)
        if not claimed:
            return CollectionCycleResult(
                active_feed_count=len(feeds),
                due_feed_count=len(due),
                claimed_feed_count=0,
                skipped_inflight_count=skipped,
                fetched_feed_count=0,
                not_modified_count=0,
                fetched_article_count=0,
                processed_article_count=0,
            )

        service = CollectorService(
            self.collector,
            concurrency=snapshot.collection.defaults.max_concurrency,
        )
        try:
            batch = await service.collect(claimed)
            failures = list(batch.failures)
            processed = 0
            persisted_results = 0
            not_modified = 0
            for result in batch.results:
                try:
                    processed += self._persist_fetch_result(result)
                    persisted_results += 1
                    not_modified += int(result.not_modified)
                except Exception as exc:
                    failures.append(
                        CollectionFailure(
                            source_feed_id=result.source_feed_id,
                            error_type=type(exc).__name__,
                            message=str(exc),
                        )
                    )
            return CollectionCycleResult(
                active_feed_count=len(feeds),
                due_feed_count=len(due),
                claimed_feed_count=len(claimed),
                skipped_inflight_count=skipped,
                fetched_feed_count=persisted_results,
                not_modified_count=not_modified,
                fetched_article_count=batch.article_count,
                processed_article_count=processed,
                failures=failures,
            )
        finally:
            await self._release_feeds(claimed)

    async def serve(
        self,
        stop_event: asyncio.Event,
        *,
        wake_interval_seconds: float = 30.0,
    ) -> None:
        """Run collection cycles until stopped while feed intervals control actual polling."""

        if wake_interval_seconds <= 0:
            raise ValueError("wake_interval_seconds must be > 0")
        while not stop_event.is_set():
            await self.run_cycle()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=wake_interval_seconds)
            except TimeoutError:
                continue

    def _sync_and_load_feeds(self, snapshot: SourceConfigSnapshot) -> list[FeedDefinition]:
        with self.session_factory() as session, session.begin():
            registry = SourceRegistryService(session)
            registry.sync(snapshot)
            definitions = registry.active_feed_definitions(snapshot)
            if not definitions:
                return []
            feed_ids = [definition.source_feed_id for definition in definitions]
            states = {
                feed.id: feed
                for feed in session.scalars(select(SourceFeed).where(SourceFeed.id.in_(feed_ids)))
            }
            scheduled: list[FeedDefinition] = []
            for definition in definitions:
                state = states.get(definition.source_feed_id)
                if state is None:
                    raise RuntimeError(
                        f"source feed {definition.source_feed_id} disappeared during registry sync"
                    )
                scheduled.append(
                    definition.model_copy(
                        update={
                            "poll_interval_seconds": state.poll_interval_seconds or 900,
                            "last_polled_at": self._coerce_utc(state.last_polled_at),
                        }
                    )
                )
            return scheduled

    def _persist_fetch_result(self, result: FeedFetchResult) -> int:
        completed_at = self._now()
        with self.session_factory() as session, session.begin():
            registry = SourceRegistryService(session)
            feed = session.get(SourceFeed, result.source_feed_id)
            if feed is None or not feed.is_active:
                raise RuntimeError(f"source feed {result.source_feed_id} is no longer active")

            processed = 0
            if not result.not_modified:
                for article in result.articles:
                    if article.source_feed_id != result.source_feed_id:
                        raise ValueError("collected article belongs to a different source feed")
                    self.article_handler(session, article, retrieved_at=completed_at)
                    processed += 1

            registry.update_poll_state(
                result.source_feed_id,
                polled_at=completed_at,
                etag=result.etag,
                last_modified=result.last_modified,
            )
            return processed

    async def _claim_feeds(
        self,
        feeds: list[FeedDefinition],
    ) -> tuple[list[FeedDefinition], int]:
        claimed: list[FeedDefinition] = []
        skipped = 0
        async with self._inflight_lock:
            for feed in feeds:
                if feed.source_feed_id in self._inflight:
                    skipped += 1
                    continue
                self._inflight.add(feed.source_feed_id)
                claimed.append(feed)
        return claimed, skipped

    async def _release_feeds(self, feeds: list[FeedDefinition]) -> None:
        async with self._inflight_lock:
            for feed in feeds:
                self._inflight.discard(feed.source_feed_id)

    @staticmethod
    def _is_due(feed: FeedDefinition, now: datetime) -> bool:
        if feed.last_polled_at is None:
            return True
        return now >= feed.last_polled_at + timedelta(seconds=feed.poll_interval_seconds)

    @staticmethod
    def _coerce_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("collector clock must return a timezone-aware datetime")
        return value.astimezone(UTC)
