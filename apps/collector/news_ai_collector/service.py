"""Collection orchestration that isolates source failures."""

import asyncio

from .base import FeedCollector
from .models import CollectionBatchResult, CollectionFailure, FeedDefinition, FeedFetchResult


class CollectorService:
    def __init__(self, collector: FeedCollector, *, concurrency: int = 4) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self.collector = collector
        self.concurrency = concurrency

    async def collect(self, feeds: list[FeedDefinition]) -> CollectionBatchResult:
        semaphore = asyncio.Semaphore(self.concurrency)

        async def one(feed: FeedDefinition) -> FeedFetchResult | CollectionFailure:
            async with semaphore:
                try:
                    return await self.collector.collect(feed)
                except Exception as exc:
                    return CollectionFailure(
                        source_feed_id=feed.source_feed_id,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )

        completed = await asyncio.gather(*(one(feed) for feed in feeds))
        return CollectionBatchResult(
            results=[item for item in completed if isinstance(item, FeedFetchResult)],
            failures=[item for item in completed if isinstance(item, CollectionFailure)],
        )
