import asyncio
from uuid import uuid4

from news_ai_collector.models import FeedDefinition, FeedFetchResult
from news_ai_collector.service import CollectorService


class MixedCollector:
    async def collect(self, feed: FeedDefinition) -> FeedFetchResult:
        if feed.name == "broken":
            raise RuntimeError("feed unavailable")
        return FeedFetchResult(source_feed_id=feed.source_feed_id)


def _feed(name: str) -> FeedDefinition:
    return FeedDefinition(
        source_id=uuid4(),
        source_feed_id=uuid4(),
        name=name,
        url=f"https://example.com/{name}.xml",
    )


def test_one_broken_feed_does_not_stop_other_sources() -> None:
    service = CollectorService(MixedCollector(), concurrency=2)

    result = asyncio.run(service.collect([_feed("healthy"), _feed("broken")]))

    assert len(result.results) == 1
    assert len(result.failures) == 1
    assert result.failures[0].error_type == "RuntimeError"
    assert result.failures[0].message == "feed unavailable"
