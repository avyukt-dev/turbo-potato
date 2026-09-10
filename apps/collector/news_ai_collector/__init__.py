"""News collection package."""

from .models import CollectedArticle, CollectionBatchResult, FeedDefinition, FeedFetchResult
from .rss import RSSCollector
from .service import CollectorService

__all__ = [
    "CollectedArticle",
    "CollectionBatchResult",
    "CollectorService",
    "FeedDefinition",
    "FeedFetchResult",
    "RSSCollector",
]
