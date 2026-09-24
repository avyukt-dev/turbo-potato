from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from news_ai_common import CollectedMediaCandidate, FeedMediaOrigin, FeedMediaType
from news_ai_database import Article, ArticleVersion, Base, EventOutbox, Source
from news_ai_events import EventType
from news_ai_processor import (
    ArticleNormalizationInput,
    ArticleNormalizer,
    ArticlePersistenceService,
    NormalizedArticle,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session
    engine.dispose()


def _source(session: Session) -> Source:
    source = Source(name="Example News", domain="example.com", source_type="NEWS")
    session.add(source)
    session.flush()
    return source


def _normalized(
    source_id: UUID,
    *,
    body: str = "Original body",
    title: str = "Example headline",
    author: str = "Jane Doe",
    summary: str = "Example summary",
    media_candidates: tuple[CollectedMediaCandidate, ...] = (),
) -> NormalizedArticle:
    return ArticleNormalizer().normalize(
        ArticleNormalizationInput(
            source_id=source_id,
            source_feed_id=uuid4(),
            url="https://example.com/news/item?utm_source=test&a=1",
            title=title,
            author=author,
            published_at=datetime(2026, 9, 10, 3, 0, tzinfo=UTC),
            language="en",
            summary=summary,
            body=body,
            external_id="item-123",
            media_candidates=media_candidates,
            retrieved_at=datetime(2026, 9, 10, 3, 5, tzinfo=UTC),
        )
    )


def _count(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


@pytest.mark.parametrize("body", [None, "", "  \n "])
def test_new_normalized_state_requires_body_even_without_acquisition_worker(session, body):
    source = _source(session)
    normalized = _normalized(source.id, body=body)
    with pytest.raises(ValueError, match="nonempty article body"):
        ArticlePersistenceService(session).persist(normalized)
    assert _count(session, ArticleVersion) == 0
    assert _count(session, EventOutbox) == 0


def test_persist_creates_article_version_and_outbox_atomically(session: Session) -> None:
    source = _source(session)
    normalized = _normalized(source.id)
    correlation_id = uuid4()
    causation_id = uuid4()

    result = ArticlePersistenceService(session).persist(
        normalized,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )

    assert result.created_article is True
    assert result.created_version is True
    assert result.version_number == 1
    assert result.event_id is not None
    assert _count(session, Article) == 1
    assert _count(session, ArticleVersion) == 1
    assert _count(session, EventOutbox) == 1

    article = session.get(Article, result.article_id)
    version = session.get(ArticleVersion, result.version_id)
    event = session.scalar(select(EventOutbox))
    assert article is not None
    assert version is not None
    assert event is not None
    assert article.canonical_url == "https://example.com/news/item?a=1"
    assert article.title == "Example headline"
    assert version.content_hash == normalized.content_hash
    assert version.version_metadata["summary"] == "Example summary"
    assert version.version_metadata["external_id"] == "item-123"
    assert event.event_type == EventType.ARTICLE_NORMALIZED.value
    assert event.aggregate_id == article.id
    assert event.correlation_id == correlation_id
    assert event.causation_id == causation_id
    assert event.payload["article_version_id"] == str(version.id)
    assert event.payload["content_hash"] == normalized.content_hash


def test_identical_replay_reuses_version_and_does_not_duplicate_event(session: Session) -> None:
    source = _source(session)
    normalized = _normalized(source.id)
    service = ArticlePersistenceService(session)

    first = service.persist(normalized)
    second = service.persist(normalized)

    assert second.article_id == first.article_id
    assert second.version_id == first.version_id
    assert second.version_number == 1
    assert second.created_article is False
    assert second.created_version is False
    assert second.event_id is None
    assert _count(session, Article) == 1
    assert _count(session, ArticleVersion) == 1
    assert _count(session, EventOutbox) == 1


def test_changed_content_creates_next_version_and_event(session: Session) -> None:
    source = _source(session)
    service = ArticlePersistenceService(session)

    first = service.persist(_normalized(source.id, body="Version one"))
    second = service.persist(_normalized(source.id, body="Version two"))

    assert second.article_id == first.article_id
    assert second.version_id != first.version_id
    assert second.version_number == 2
    assert second.created_article is False
    assert second.created_version is True
    assert second.event_id is not None
    assert _count(session, Article) == 1
    assert _count(session, ArticleVersion) == 2
    assert _count(session, EventOutbox) == 2

    version_numbers = list(
        session.scalars(
            select(ArticleVersion.version_number)
            .where(ArticleVersion.article_id == first.article_id)
            .order_by(ArticleVersion.version_number)
        )
    )
    assert version_numbers == [1, 2]


def test_metadata_only_change_updates_article_without_new_version(session: Session) -> None:
    source = _source(session)
    service = ArticlePersistenceService(session)

    first = service.persist(_normalized(source.id, author="Jane Doe"))
    second = service.persist(_normalized(source.id, author="John Doe"))

    article = session.get(Article, first.article_id)
    assert article is not None
    assert article.author == "John Doe"
    assert second.version_id == first.version_id
    assert second.created_version is False
    assert _count(session, ArticleVersion) == 1
    assert _count(session, EventOutbox) == 1


def test_caller_rollback_removes_article_version_and_outbox(session: Session) -> None:
    source = _source(session)
    source_id = source.id
    session.commit()

    with pytest.raises(RuntimeError, match="rollback"), session.begin():
        ArticlePersistenceService(session).persist(_normalized(source_id))
        raise RuntimeError("rollback")

    assert _count(session, Article) == 0
    assert _count(session, ArticleVersion) == 0
    assert _count(session, EventOutbox) == 0


def _media_candidate(url: str) -> CollectedMediaCandidate:
    return CollectedMediaCandidate(
        url=url,
        media_type=FeedMediaType.IMAGE,
        origin=FeedMediaOrigin.MEDIA_CONTENT,
        mime_type="image/jpeg",
        width=1600,
        height=900,
        credit="District administration",
        license_url="https://example.com/license",
    )


def test_media_candidates_are_snapshotted_on_exact_article_version(session: Session) -> None:
    source = _source(session)
    candidate = _media_candidate("https://example.com/media/incident.jpg")

    result = ArticlePersistenceService(session).persist(
        _normalized(source.id, media_candidates=(candidate,))
    )

    version = session.get(ArticleVersion, result.version_id)
    assert version is not None
    assert version.version_metadata["media_candidates"] == [candidate.model_dump(mode="json")]
    assert version.version_metadata["media_candidates"][0]["reuse_status"] == "UNASSESSED"


def test_changed_media_candidate_creates_new_immutable_article_version(session: Session) -> None:
    source = _source(session)
    service = ArticlePersistenceService(session)

    first = service.persist(
        _normalized(
            source.id,
            media_candidates=(_media_candidate("https://example.com/media/first.jpg"),),
        )
    )
    second = service.persist(
        _normalized(
            source.id,
            media_candidates=(_media_candidate("https://example.com/media/second.jpg"),),
        )
    )

    assert second.version_id != first.version_id
    assert second.version_number == 2
    assert _count(session, ArticleVersion) == 2
