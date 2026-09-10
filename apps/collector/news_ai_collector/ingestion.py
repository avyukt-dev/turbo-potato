"""Integration boundary from collected feed items into article normalization/persistence."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from news_ai_database import Article
from news_ai_events import EventEnvelope, EventType
from news_ai_processor import (
    ArticleNormalizationInput,
    ArticleNormalizer,
    ArticlePersistenceResult,
    ArticlePersistenceService,
    NormalizedArticle,
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
        collector_producer: str = "collector",
        collector_producer_version: str = "0.1.0",
    ) -> None:
        self.normalizer = normalizer or ArticleNormalizer()
        self.producer = producer
        self.producer_version = producer_version
        self.collector_producer = collector_producer
        self.collector_producer_version = collector_producer_version

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
        correlation_id = uuid4()

        def discovered_event(
            persisted_article: Article,
            normalized_article: NormalizedArticle,
        ) -> EventEnvelope:
            return EventEnvelope(
                event_type=EventType.ARTICLE_DISCOVERED,
                occurred_at=retrieved_at,
                producer=self.collector_producer,
                producer_version=self.collector_producer_version,
                aggregate_type="article",
                aggregate_id=persisted_article.id,
                correlation_id=correlation_id,
                idempotency_key=f"article.discovered:{persisted_article.id}",
                payload={
                    "article_id": str(persisted_article.id),
                    "source_id": str(normalized_article.source_id),
                    "source_feed_id": (
                        str(normalized_article.source_feed_id)
                        if normalized_article.source_feed_id is not None
                        else None
                    ),
                    "canonical_url": normalized_article.canonical_url,
                    "title": normalized_article.title,
                    "published_at": (
                        normalized_article.published_at.isoformat()
                        if normalized_article.published_at is not None
                        else None
                    ),
                },
            )

        return ArticlePersistenceService(
            session,
            producer=self.producer,
            producer_version=self.producer_version,
        ).persist(
            normalized,
            correlation_id=correlation_id,
            predecessor_event_factory=discovered_event,
        )
