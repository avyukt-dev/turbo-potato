"""Collector protocol independent of RSS or any future source transport."""

from typing import Protocol

from .models import FeedDefinition, FeedFetchResult


class FeedCollector(Protocol):
    async def collect(self, feed: FeedDefinition) -> FeedFetchResult: ...
