"""HTTP RSS/Atom collector with conditional requests and bounded response bodies."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from pydantic import ValidationError

from .feed_parser import FeedParseError, parse_feed
from .models import CollectedArticle, FeedDefinition, FeedFetchResult


class FeedTooLargeError(ValueError):
    pass


class RSSCollector:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        user_agent: str = "news-ai-social-manager/0.1 (+feed-collector)",
    ) -> None:
        self._client = client
        self._user_agent = user_agent

    @asynccontextmanager
    async def _client_context(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        accept = (
            "application/rss+xml, application/atom+xml, application/xml, "
            "text/xml;q=0.9, */*;q=0.1"
        )
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": self._user_agent, "Accept": accept},
        ) as client:
            yield client

    async def collect(self, feed: FeedDefinition) -> FeedFetchResult:
        headers: dict[str, str] = {}
        if feed.etag:
            headers["If-None-Match"] = feed.etag
        if feed.last_modified:
            headers["If-Modified-Since"] = feed.last_modified

        timeout = httpx.Timeout(feed.timeout_seconds)
        async with self._client_context() as client, client.stream(
            "GET",
            str(feed.url),
            headers=headers,
            timeout=timeout,
        ) as response:
            if response.status_code == httpx.codes.NOT_MODIFIED:
                return FeedFetchResult(
                    source_feed_id=feed.source_feed_id,
                    etag=response.headers.get("etag") or feed.etag,
                    last_modified=response.headers.get("last-modified") or feed.last_modified,
                    not_modified=True,
                )
            response.raise_for_status()
            content = await self._read_bounded(response, max_bytes=feed.max_response_bytes)
            final_url = str(response.url)
            etag = response.headers.get("etag")
            last_modified = response.headers.get("last-modified")

        parsed_entries, warnings = parse_feed(content, base_url=final_url)
        articles: list[CollectedArticle] = []
        for entry in parsed_entries:
            try:
                articles.append(
                    CollectedArticle(
                        source_id=feed.source_id,
                        source_feed_id=feed.source_feed_id,
                        **entry,
                    )
                )
            except ValidationError as exc:
                warnings.append(f"skipped invalid feed entry: {type(exc).__name__}")

        return FeedFetchResult(
            source_feed_id=feed.source_feed_id,
            articles=articles,
            etag=etag,
            last_modified=last_modified,
            warnings=warnings,
        )

    @staticmethod
    async def _read_bounded(response: httpx.Response, *, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise FeedTooLargeError(f"feed response exceeds {max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)


__all__ = ["FeedParseError", "FeedTooLargeError", "RSSCollector"]
