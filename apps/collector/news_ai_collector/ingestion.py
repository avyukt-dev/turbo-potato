"""Integration boundary from collected feed items into article normalization/persistence."""

from __future__ import annotations

from datetime import datetime

from news_ai_processor import (
    ArticleNormalizationInput,
    ArticleNormalizer,
    ArticlePersistenceResult,
    ArticlePersistenceService,
)
from sqlalchemy.orm import Session

from .models import CollectedArticle


class NormalizedArticleHandler:
    """Normalize and persist one collected article inside the caller-owned transaction."""

    def __init__(
        self,
        normalizer: ArticleNormalizer | None = None,
        *,
        producer: str = "processor",
        producer_version: str = "0.1.0",
    ) -> None:
        self.normalizer = normalizer or ArticleNormalizer()
        self.producer = producer
        self.producer_version = producer_version

    def __call__(
        self,
        session: Session,
        article: CollectedArticle,
        *,
        retrieved_at: datetime,
    ) -> ArticlePersistenceResult:
        normalized = self.normalizer.normalize(
            ArticleNormalizationInput(
                source_id=article.source_id,
                source_feed_id=article.source_feed_id,
                url=article.url,
                title=article.title,
                author=article.author,
                published_at=article.published_at,
                summary=article.summary,
                external_id=article.external_id,
                retrieved_at=retrieved_at,
            )
        )
        return ArticlePersistenceService(
            session,
            producer=self.producer,
            producer_version=self.producer_version,
        ).persist(normalized)
