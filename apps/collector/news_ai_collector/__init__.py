"""News collection package."""

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
from .service import CollectorService

__all__ = [
    "CollectedArticle",
    "CollectionBatchResult",
    "CollectionConfig",
    "CollectionDefaults",
    "CollectorService",
    "FeedConfig",
    "FeedDefinition",
    "FeedFetchResult",
    "FeedRegistryConfig",
    "RSSCollector",
    "RegistrySyncResult",
    "SourceConfig",
    "SourceConfigSnapshot",
    "SourceRegistryConfig",
    "SourceRegistryLoader",
    "SourceRegistryService",
]
