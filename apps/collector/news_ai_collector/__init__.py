"""News collection package."""

from .ingestion import NormalizedArticleHandler
from .models import CollectedArticle, CollectionBatchResult, FeedDefinition, FeedFetchResult
from .registry import (
    CollectionConfig,
    CollectionDefaults,
    FeedConfig,
    FeedRegistryConfig,
    RegistrySyncResult,
    SourceConfig,
    SourceConfigSnapshot,
    SourceRegistryConfig,
    SourceRegistryLoader,
    SourceRegistryService,
)
from .rss import RSSCollector
from .scheduling import CollectionCycleResult, CollectorScheduler
from .service import CollectorService

__all__ = [
    "CollectedArticle",
    "CollectionBatchResult",
    "CollectionConfig",
    "CollectionCycleResult",
    "CollectionDefaults",
    "CollectorScheduler",
    "CollectorService",
    "FeedConfig",
    "FeedDefinition",
    "FeedFetchResult",
    "FeedRegistryConfig",
    "NormalizedArticleHandler",
    "RSSCollector",
    "RegistrySyncResult",
    "SourceConfig",
    "SourceConfigSnapshot",
    "SourceRegistryConfig",
    "SourceRegistryLoader",
    "SourceRegistryService",
]
