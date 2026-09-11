"""Durable collection boundary that emits article.discovered before normalization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from news_ai_database import Article, ArticleDiscovery
from news_ai_events import EventEnvelope, EventType
from news_ai_events.outbox import build_outbox_record
from news_ai_processor import canonicalize_url
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CollectedArticle


@dataclass(frozen=True, slots=True)
class ArticleDiscoveryResult:
    article_id: UUID
    discovery_id: UUID
    event_id: UUID
    created_article: bool
    created_discovery: bool


class DiscoveredArticleHandler:
    """Persist raw discovery input and its outbox intent in the caller transaction."""

    def __init__(
        self,
        *,
        producer: str = "collector",
        producer_version: str = "0.1.0",
    ) -> None:
        self.producer = producer
        self.producer_version = producer_version

    def __call__(
        self,
        session: Session,
        article: CollectedArticle,
        *,
        retrieved_at: datetime,
    ) -> ArticleDiscoveryResult:
        canonical_url = canonicalize_url(str(article.url))
        persisted = session.scalar(
            select(Article)
            .where(
                Article.source_id == article.source_id,
                Article.canonical_url == canonical_url,
            )
            .with_for_update()
        )
        created_article = persisted is None
        if persisted is None:
            persisted = Article(source_id=article.source_id, canonical_url=canonical_url)
            session.add(persisted)
            session.flush()

        raw_payload = {
            "source_id": str(article.source_id),
            "source_feed_id": str(article.source_feed_id),
            "url": str(article.url),
            "title": article.title,
            "author": article.author,
            "published_at": (
                article.published_at.isoformat() if article.published_at is not None else None
            ),
            "language": article.language,
            "summary": article.summary,
            "body": article.body,
            "external_id": article.external_id,
            "retrieved_at": retrieved_at.isoformat(),
        }
        semantic_payload = {
            key: value for key, value in raw_payload.items() if key != "retrieved_at"
        }
        raw_hash = hashlib.sha256(
            json.dumps(semantic_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        existing = session.scalar(
            select(ArticleDiscovery).where(
                ArticleDiscovery.article_id == persisted.id,
                ArticleDiscovery.raw_hash == raw_hash,
            )
        )
        if existing is not None:
            return ArticleDiscoveryResult(
                article_id=persisted.id,
                discovery_id=existing.id,
                event_id=existing.event_id,
                created_article=created_article,
                created_discovery=False,
            )

        correlation_id = uuid4()
        event = EventEnvelope(
            event_type=EventType.ARTICLE_DISCOVERED,
            occurred_at=retrieved_at,
            producer=self.producer,
            producer_version=self.producer_version,
            aggregate_type="article",
            aggregate_id=persisted.id,
            correlation_id=correlation_id,
            idempotency_key=f"article.discovered:{persisted.id}:{raw_hash}",
            payload={
                "article_id": str(persisted.id),
                "source_id": str(article.source_id),
                "source_feed_id": str(article.source_feed_id),
                "canonical_url": canonical_url,
                "title": article.title,
                "published_at": raw_payload["published_at"],
            },
        )
        discovery = ArticleDiscovery(
            event_id=event.event_id,
            article_id=persisted.id,
            raw_hash=raw_hash,
            raw_payload=raw_payload,
            retrieved_at=retrieved_at,
        )
        session.add_all([discovery, build_outbox_record(event)])
        session.flush()
        return ArticleDiscoveryResult(
            article_id=persisted.id,
            discovery_id=discovery.id,
            event_id=event.event_id,
            created_article=created_article,
            created_discovery=True,
        )
