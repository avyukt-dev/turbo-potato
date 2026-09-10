"""Article processing, normalization, persistence, clustering, and worker primitives."""

from .clustering import StoryClusteringConfig, StoryClusteringResult, StoryClusteringService
from .models import ArticleNormalizationInput, NormalizedArticle
from .normalizer import ArticleNormalizer, canonicalize_url
from .persistence import ArticlePersistenceResult, ArticlePersistenceService
from .worker import (
    PROCESSOR_CONSUMER_GROUP,
    ArticleNormalizedHandler,
    ArticleNormalizedWorkItem,
    ProcessorBatchResult,
    ProcessorEventWorker,
)

__all__ = [
    "PROCESSOR_CONSUMER_GROUP",
    "ArticleNormalizationInput",
    "ArticleNormalizedHandler",
    "ArticleNormalizedWorkItem",
    "ArticleNormalizer",
    "ArticlePersistenceResult",
    "ArticlePersistenceService",
    "NormalizedArticle",
    "ProcessorBatchResult",
    "ProcessorEventWorker",
    "StoryClusteringConfig",
    "StoryClusteringResult",
    "StoryClusteringService",
    "canonicalize_url",
]
