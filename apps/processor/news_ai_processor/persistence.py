"""Transactional persistence for normalized articles.

The service never commits. Article state, article versions, and the event-outbox record are added to
the caller's SQLAlchemy transaction so they succeed or roll back together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from news_ai_database import Article, ArticleVersion
from news_ai_events import EventEnvelope, EventType
from news_ai_events.outbox import build_outbox_record
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import NormalizedArticle


@dataclass(frozen=True, slots=True)
class ArticlePersistenceResult:
    article_id: UUID
    version_id: UUID
    version_number: int
    created_article: bool
    created_version: bool
    event_id: UUID | None


class ArticlePersistenceService:
    """Persist normalized articles idempotently using the transactional outbox pattern."""

    def __init__(
        self,
        session: Session,
        *,
        producer: str = "processor",
        producer_version: str = "0.1.0",
    ) -> None:
        self.session = session
        self.producer = producer
        self.producer_version = producer_version

    def persist(
        self,
        normalized: NormalizedArticle,
        *,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
    ) -> ArticlePersistenceResult:
        article, created_article = self._get_or_create_article(normalized)
        self._apply_latest_metadata(article, normalized)

        existing_version = self.session.scalar(
            select(ArticleVersion)
            .where(
                ArticleVersion.article_id == article.id,
                ArticleVersion.content_hash == normalized.content_hash,
            )
            .order_by(ArticleVersion.version_number.asc())
            .limit(1)
        )
        if existing_version is not None:
            return ArticlePersistenceResult(
                article_id=article.id,
                version_id=existing_version.id,
                version_number=existing_version.version_number,
                created_article=created_article,
                created_version=False,
                event_id=None,
            )

        current_version = self.session.scalar(
            select(func.max(ArticleVersion.version_number)).where(
                ArticleVersion.article_id == article.id
            )
        )
        version_number = (current_version or 0) + 1
        version = ArticleVersion(
            article_id=article.id,
            version_number=version_number,
            content_hash=normalized.content_hash,
            body=normalized.body,
            retrieved_at=normalized.retrieved_at,
            version_metadata=self._version_metadata(normalized),
        )
        self.session.add(version)
        self.session.flush()

        event = EventEnvelope(
            event_type=EventType.ARTICLE_NORMALIZED,
            producer=self.producer,
            producer_version=self.producer_version,
            aggregate_type="article",
            aggregate_id=article.id,
            correlation_id=correlation_id if correlation_id is not None else uuid4(),
            causation_id=causation_id,
            idempotency_key=(
                f"article.normalized:{article.id}:{version.version_number}:{version.content_hash}"
            ),
            payload=self._event_payload(article, version, normalized),
        )
        self.session.add(build_outbox_record(event))
        self.session.flush()

        return ArticlePersistenceResult(
            article_id=article.id,
            version_id=version.id,
            version_number=version.version_number,
            created_article=created_article,
            created_version=True,
            event_id=event.event_id,
        )

    def _get_or_create_article(self, normalized: NormalizedArticle) -> tuple[Article, bool]:
        lookup = (
            select(Article)
            .where(
                Article.source_id == normalized.source_id,
                Article.canonical_url == normalized.canonical_url,
            )
            .with_for_update()
        )
        article = self.session.scalar(lookup)
        if article is not None:
            return article, False

        article = Article(
            source_id=normalized.source_id,
            canonical_url=normalized.canonical_url,
        )
        try:
            with self.session.begin_nested():
                self.session.add(article)
                self.session.flush()
        except IntegrityError:
            article = self.session.scalar(lookup)
            if article is None:
                raise
            return article, False
        return article, True

    @staticmethod
    def _apply_latest_metadata(article: Article, normalized: NormalizedArticle) -> None:
        article.title = normalized.title
        article.author = normalized.author
        article.language = normalized.language
        article.published_at = normalized.published_at

    @staticmethod
    def _version_metadata(normalized: NormalizedArticle) -> dict[str, Any]:
        return {
            "canonical_url": normalized.canonical_url,
            "title": normalized.title,
            "author": normalized.author,
            "language": normalized.language,
            "published_at": (
                normalized.published_at.isoformat() if normalized.published_at is not None else None
            ),
            "summary": normalized.summary,
            "source_feed_id": (
                str(normalized.source_feed_id) if normalized.source_feed_id is not None else None
            ),
            "external_id": normalized.external_id,
        }

    @staticmethod
    def _event_payload(
        article: Article,
        version: ArticleVersion,
        normalized: NormalizedArticle,
    ) -> dict[str, Any]:
        return {
            "article_id": str(article.id),
            "article_version_id": str(version.id),
            "version_number": version.version_number,
            "source_id": str(normalized.source_id),
            "source_feed_id": (
                str(normalized.source_feed_id) if normalized.source_feed_id is not None else None
            ),
            "canonical_url": normalized.canonical_url,
            "content_hash": normalized.content_hash,
            "published_at": (
                normalized.published_at.isoformat() if normalized.published_at is not None else None
            ),
            "retrieved_at": normalized.retrieved_at.isoformat(),
        }
