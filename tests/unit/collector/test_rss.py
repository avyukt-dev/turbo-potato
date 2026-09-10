import asyncio
from uuid import uuid4

import httpx
import pytest

from news_ai_collector.models import FeedDefinition
from news_ai_collector.rss import FeedTooLargeError, RSSCollector

RSS = b"""<rss version="2.0"><channel><item><title>Headline</title><link>https://example.com/story</link></item></channel></rss>"""


def _feed(**overrides: object) -> FeedDefinition:
    data: dict[str, object] = {
        "source_id": uuid4(),
        "source_feed_id": uuid4(),
        "name": "Example",
        "url": "https://example.com/feed.xml",
    }
    data.update(overrides)
    return FeedDefinition(**data)


def test_collects_feed_and_propagates_cache_headers() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"old"'
        assert request.headers["if-modified-since"] == "Wed, 09 Sep 2026 00:00:00 GMT"
        return httpx.Response(
            200,
            headers={"ETag": '"new"', "Last-Modified": "Thu, 10 Sep 2026 00:00:00 GMT"},
            content=RSS,
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    collector = RSSCollector(client)
    feed = _feed(etag='"old"', last_modified="Wed, 09 Sep 2026 00:00:00 GMT")

    try:
        result = asyncio.run(collector.collect(feed))
    finally:
        asyncio.run(client.aclose())

    assert len(result.articles) == 1
    assert result.etag == '"new"'
    assert result.last_modified == "Thu, 10 Sep 2026 00:00:00 GMT"
    assert result.not_modified is False


def test_304_returns_without_parsing() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304, headers={"ETag": '"same"'}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = RSSCollector(client)

    try:
        result = asyncio.run(collector.collect(_feed(etag='"same"')))
    finally:
        asyncio.run(client.aclose())

    assert result.not_modified is True
    assert result.articles == []
    assert result.etag == '"same"'


def test_response_size_limit_is_enforced() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 2048, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector = RSSCollector(client)

    try:
        with pytest.raises(FeedTooLargeError):
            asyncio.run(collector.collect(_feed(max_response_bytes=1024)))
    finally:
        asyncio.run(client.aclose())
