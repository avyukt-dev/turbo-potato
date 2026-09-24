"""News collection package."""

from news_ai_common import (
    CollectedMediaCandidate,
    FeedMediaOrigin,
    FeedMediaType,
    MediaReuseStatus,
)

from .ingestion import ArticleDiscoveryResult, DiscoveredArticleHandler
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
    "CollectedMediaCandidate",
    "CollectionBatchResult",
    "CollectionConfig",
    "CollectionCycleResult",
    "CollectionDefaults",
    "CollectorScheduler",
    "CollectorService",
    "FeedConfig",
    "FeedDefinition",
    "FeedFetchResult",
    "FeedMediaOrigin",
    "FeedMediaType",
    "MediaReuseStatus",
    "FeedRegistryConfig",
    "ArticleDiscoveryResult",
    "DiscoveredArticleHandler",
    "RSSCollector",
    "RegistrySyncResult",
    "SourceConfig",
    "SourceConfigSnapshot",
    "SourceRegistryConfig",
    "SourceRegistryLoader",
    "SourceRegistryService",
]
