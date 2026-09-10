"""Deterministic first-pass story clustering.

This layer uses conservative lexical similarity plus source-publication time
proximity. It does not claim semantic or entity-aware matching; those signals
can be added later without changing the story/event persistence boundary.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from news_ai_database import Article, ArticleVersion, Story, StorySource
from news_ai_events import EventEnvelope, EventType
from news_ai_events.outbox import build_outbox_record
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .worker import ArticleNormalizedWorkItem

_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_ENGLISH_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "was",
        "were",
        "with",
    }
)


@dataclass(frozen=True, slots=True)
class StoryClusteringConfig:
    time_window_hours: int = 48
    min_lexical_similarity: float = 0.62
    max_candidates: int = 200

    def __post_init__(self) -> None:
        if self.time_window_hours <= 0:
            raise ValueError("time_window_hours must be positive")
        if not 0.0 <= self.min_lexical_similarity <= 1.0:
            raise ValueError("min_lexical_similarity must be between 0 and 1")
        if self.max_candidates <= 0:
            raise ValueError("max_candidates must be positive")


@dataclass(frozen=True, slots=True)
class StoryClusteringResult:
    story_id: UUID
    created_story: bool
    created_relation: bool
    similarity_score: float
    cluster_method: str
    event_id: UUID

    def as_handler_result(self) -> dict[str, Any]:
        return {
            "story_id": str(self.story_id),
            "created_story": self.created_story,
            "created_relation": self.created_relation,
            "similarity_score": self.similarity_score,
            "cluster_method": self.cluster_method,
            "event_id": str(self.event_id),
        }


class StoryClusteringService:
    """Associate one persisted article version with one conservative story cluster."""

    def __init__(
        self,
        config: StoryClusteringConfig | None = None,
        *,
        producer: str = "processor",
        producer_version: str = "0.1.0",
    ) -> None:
        self.config = config or StoryClusteringConfig()
        self.producer = producer
        self.producer_version = producer_version

    def __call__(
        self,
        session: Session,
        triggering_event: EventEnvelope,
        work_item: ArticleNormalizedWorkItem,
    ) -> dict[str, Any]:
        return self.cluster(session, triggering_event, work_item).as_handler_result()

    def cluster(
        self,
        session: Session,
        triggering_event: EventEnvelope,
        work_item: ArticleNormalizedWorkItem,
    ) -> StoryClusteringResult:
        article, version = self._load_and_validate_state(session, work_item)

        existing_story = self._existing_story_for_article(session, article.id)
        if existing_story is not None:
            existing_story.last_updated_at = _utc(work_item.retrieved_at)
            event = self._clustered_event(
                session,
                triggering_event,
                existing_story,
                work_item,
                similarity_score=1.0,
                cluster_method="existing-article",
            )
            return StoryClusteringResult(
                story_id=existing_story.id,
                created_story=False,
                created_relation=False,
                similarity_score=1.0,
                cluster_method="existing-article",
                event_id=event.event_id,
            )

        best_story, best_score = self._find_best_story(session, article, work_item)
        if best_story is None:
            story = self._create_story(session, article, version, work_item)
            session.add(
                StorySource(
                    story_id=story.id,
                    article_id=article.id,
                    relationship_type="ORIGIN",
                )
            )
            session.flush()
            event = self._created_event(session, triggering_event, story, article.id)
            return StoryClusteringResult(
                story_id=story.id,
                created_story=True,
                created_relation=True,
                similarity_score=1.0,
                cluster_method="new-story",
                event_id=event.event_id,
            )

        best_story.last_updated_at = _utc(work_item.retrieved_at)
        session.add(
            StorySource(
                story_id=best_story.id,
                article_id=article.id,
                relationship_type="RELATED",
            )
        )
        session.flush()
        event = self._clustered_event(
            session,
            triggering_event,
            best_story,
            work_item,
            similarity_score=best_score,
            cluster_method="lexical+time",
        )
        return StoryClusteringResult(
            story_id=best_story.id,
            created_story=False,
            created_relation=True,
            similarity_score=best_score,
            cluster_method="lexical+time",
            event_id=event.event_id,
        )

    @staticmethod
    def _load_and_validate_state(
        session: Session,
        work_item: ArticleNormalizedWorkItem,
    ) -> tuple[Article, ArticleVersion]:
        article = session.get(Article, work_item.article_id)
        version = session.get(ArticleVersion, work_item.article_version_id)
        if article is None:
            raise ValueError(f"article {work_item.article_id} does not exist")
        if version is None:
            raise ValueError(f"article version {work_item.article_version_id} does not exist")
        if version.article_id != article.id:
            raise ValueError("article version does not belong to event article")
        if version.version_number != work_item.version_number:
            raise ValueError("article version number does not match event payload")
        if version.content_hash != work_item.content_hash:
            raise ValueError("article content hash does not match event payload")
        if article.source_id != work_item.source_id:
            raise ValueError("article source does not match event payload")
        if article.canonical_url != work_item.canonical_url:
            raise ValueError("article canonical URL does not match event payload")
        return article, version

    @staticmethod
    def _existing_story_for_article(session: Session, article_id: UUID) -> Story | None:
        story_ids = list(
            session.scalars(
                select(StorySource.story_id)
                .where(StorySource.article_id == article_id)
                .order_by(StorySource.created_at.asc())
            )
        )
        if not story_ids:
            return None
        if len(story_ids) > 1:
            raise RuntimeError(
                "article belongs to multiple stories; multi-story reprocessing is not supported yet"
            )
        return session.get(Story, story_ids[0])

    def _find_best_story(
        self,
        session: Session,
        article: Article,
        work_item: ArticleNormalizedWorkItem,
    ) -> tuple[Story | None, float]:
        incoming_headline = _normalize_headline(article.title)
        incoming_tokens = _headline_tokens(article.title, article.language)
        if not incoming_headline or len(incoming_tokens) < 2:
            return None, 0.0

        anchor = _utc(work_item.published_at or work_item.retrieved_at)
        window = timedelta(hours=self.config.time_window_hours)
        lower = anchor - window
        upper = anchor + window

        candidate_story_ids = (
            select(StorySource.story_id)
            .join(Article, Article.id == StorySource.article_id)
            .where(
                or_(
                    and_(
                        Article.published_at.is_not(None),
                        Article.published_at >= lower,
                        Article.published_at <= upper,
                    ),
                    and_(
                        Article.published_at.is_(None),
                        StorySource.created_at >= lower,
                        StorySource.created_at <= upper,
                    ),
                )
            )
            .group_by(StorySource.story_id)
        )
        candidates = list(
            session.scalars(
                select(Story)
                .where(Story.id.in_(candidate_story_ids))
                .order_by(Story.last_updated_at.desc())
                .limit(self.config.max_candidates)
                .with_for_update()
            )
        )

        best_story: Story | None = None
        best_score = 0.0
        for story in candidates:
            if not _languages_compatible(article.language, story.language):
                continue
            lexical = _lexical_similarity(
                incoming_headline,
                incoming_tokens,
                story.canonical_headline,
                story.language,
            )
            if lexical < self.config.min_lexical_similarity:
                continue
            candidate_anchor = self._story_anchor(session, story)
            hours_apart = abs((anchor - candidate_anchor).total_seconds()) / 3600
            time_score = max(0.0, 1.0 - (hours_apart / self.config.time_window_hours))
            combined = round((lexical * 0.9) + (time_score * 0.1), 5)
            wins_tie = (
                combined == best_score
                and best_story is not None
                and str(story.id) < str(best_story.id)
            )
            if combined > best_score or wins_tie:
                best_story = story
                best_score = combined

        return best_story, best_score

    @staticmethod
    def _story_anchor(session: Session, story: Story) -> datetime:
        published_at = session.scalar(
            select(Article.published_at)
            .join(StorySource, StorySource.article_id == Article.id)
            .where(
                StorySource.story_id == story.id,
                Article.published_at.is_not(None),
            )
            .order_by(Article.published_at.asc())
            .limit(1)
        )
        return _utc(published_at or story.first_seen_at or story.created_at)

    @staticmethod
    def _create_story(
        session: Session,
        article: Article,
        version: ArticleVersion,
        work_item: ArticleNormalizedWorkItem,
    ) -> Story:
        summary = version.version_metadata.get("summary")
        headline = article.title or article.canonical_url
        cluster_anchor = work_item.published_at or work_item.retrieved_at
        story = Story(
            canonical_headline=headline,
            summary=summary if isinstance(summary, str) else None,
            status="DISCOVERED",
            language=article.language,
            first_seen_at=_utc(work_item.retrieved_at),
            last_updated_at=_utc(work_item.retrieved_at),
            cluster_key=_cluster_key(headline, article.language, cluster_anchor),
            story_metadata={"initial_article_id": str(article.id)},
        )
        session.add(story)
        session.flush()
        return story

    def _created_event(
        self,
        session: Session,
        triggering_event: EventEnvelope,
        story: Story,
        article_id: UUID,
    ) -> EventEnvelope:
        event = EventEnvelope(
            event_type=EventType.STORY_CREATED,
            producer=self.producer,
            producer_version=self.producer_version,
            aggregate_type="story",
            aggregate_id=story.id,
            correlation_id=triggering_event.correlation_id,
            causation_id=triggering_event.event_id,
            idempotency_key=f"story.created:{story.id}",
            payload={
                "story_id": str(story.id),
                "article_id": str(article_id),
                "cluster_key": story.cluster_key,
            },
        )
        session.add(build_outbox_record(event))
        session.flush()
        return event

    def _clustered_event(
        self,
        session: Session,
        triggering_event: EventEnvelope,
        story: Story,
        work_item: ArticleNormalizedWorkItem,
        *,
        similarity_score: float,
        cluster_method: str,
    ) -> EventEnvelope:
        article_ids = sorted(
            str(article_id)
            for article_id in session.scalars(
                select(StorySource.article_id).where(StorySource.story_id == story.id)
            )
        )
        event = EventEnvelope(
            event_type=EventType.STORY_CLUSTERED,
            producer=self.producer,
            producer_version=self.producer_version,
            aggregate_type="story",
            aggregate_id=story.id,
            correlation_id=triggering_event.correlation_id,
            causation_id=triggering_event.event_id,
            idempotency_key=(
                f"story.clustered:{story.id}:{work_item.article_id}:{work_item.article_version_id}"
            ),
            payload={
                "story_id": str(story.id),
                "article_ids": article_ids,
                "similarity_score": similarity_score,
                "cluster_method": cluster_method,
            },
        )
        session.add(build_outbox_record(event))
        session.flush()
        return event


def _normalize_headline(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(_TOKEN_RE.findall(normalized))


def _headline_tokens(value: str | None, language: str | None) -> frozenset[str]:
    tokens = set(_TOKEN_RE.findall(_normalize_headline(value)))
    if _language_base(language) == "en":
        tokens.difference_update(_ENGLISH_STOPWORDS)
    return frozenset(token for token in tokens if len(token) > 1)


def _lexical_similarity(
    normalized_a: str,
    tokens_a: frozenset[str],
    headline_b: str | None,
    language_b: str | None,
) -> float:
    normalized_b = _normalize_headline(headline_b)
    tokens_b = _headline_tokens(headline_b, language_b)
    if not normalized_b or not tokens_b:
        return 0.0
    if normalized_a == normalized_b:
        return 1.0

    intersection = len(tokens_a & tokens_b)
    if intersection == 0:
        return 0.0
    containment = intersection / min(len(tokens_a), len(tokens_b))
    union = len(tokens_a | tokens_b)
    jaccard = intersection / union if union else 0.0
    sequence = SequenceMatcher(None, normalized_a, normalized_b, autojunk=False).ratio()
    return round((containment * 0.5) + (jaccard * 0.3) + (sequence * 0.2), 5)


def _cluster_key(headline: str, language: str | None, anchor: datetime) -> str:
    normalized = _normalize_headline(headline) or headline.casefold().strip()
    day = _utc(anchor).date().isoformat()
    material = f"{day}|{_language_base(language) or 'und'}|{normalized}".encode()
    return hashlib.sha256(material).hexdigest()[:32]


def _languages_compatible(left: str | None, right: str | None) -> bool:
    left_base = _language_base(left)
    right_base = _language_base(right)
    return not left_base or not right_base or left_base == right_base


def _language_base(language: str | None) -> str | None:
    if not language:
        return None
    return language.casefold().replace("_", "-").split("-", maxsplit=1)[0]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
