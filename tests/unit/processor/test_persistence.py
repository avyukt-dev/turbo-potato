from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from news_ai_database import Article, ArticleVersion, Base, EventOutbox, Source
from news_ai_events import EventEnvelope, EventType
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
            retrieved_at=datetime(2026, 9, 10, 3, 5, tzinfo=UTC),
        )
    )


def _discovered_factory(correlation_id: UUID):
    def build(article: Article, normalized: NormalizedArticle) -> EventEnvelope:
        return EventEnvelope(
            event_type=EventType.ARTICLE_DISCOVERED,
            producer="collector",
            producer_version="0.1.0",
            aggregate_type="article",
            aggregate_id=article.id,
            correlation_id=correlation_id,
            idempotency_key=f"article.discovered:{article.id}",
            payload={
                "article_id": str(article.id),
                "source_id": str(normalized.source_id),
                "canonical_url": normalized.canonical_url,
                "title": normalized.title,
            },
        )

    return build


def _count(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


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


def test_predecessor_event_is_persisted_before_first_normalized_event(session: Session) -> None:
    source = _source(session)
    correlation_id = uuid4()

    result = ArticlePersistenceService(session).persist(
        _normalized(source.id),
        correlation_id=correlation_id,
        predecessor_event_factory=_discovered_factory(correlation_id),
    )

    events = list(session.scalars(select(EventOutbox).order_by(EventOutbox.created_at, EventOutbox.id)))
    assert result.created_article is True
    assert len(events) == 2
    discovered, normalized = events
    assert discovered.event_type == EventType.ARTICLE_DISCOVERED
    assert normalized.event_type == EventType.ARTICLE_NORMALIZED
    assert discovered.producer == "collector"
    assert normalized.producer == "processor"
    assert discovered.correlation_id == correlation_id
    assert normalized.correlation_id == correlation_id
    assert normalized.causation_id == discovered.event_id


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


def test_predecessor_is_not_duplicated_on_identical_replay(session: Session) -> None:
    source = _source(session)
    correlation_id = uuid4()
    service = ArticlePersistenceService(session)
    normalized = _normalized(source.id)
    factory = _discovered_factory(correlation_id)

    first = service.persist(
        normalized,
        correlation_id=correlation_id,
        predecessor_event_factory=factory,
    )
    second = service.persist(
        normalized,
        correlation_id=uuid4(),
        predecessor_event_factory=_discovered_factory(uuid4()),
    )

    assert first.created_article is True
    assert second.created_article is False
    assert second.created_version is False
    assert _count(session, ArticleVersion) == 1
    events = list(session.scalars(select(EventOutbox).order_by(EventOutbox.created_at, EventOutbox.id)))
    assert [event.event_type for event in events] == [
        EventType.ARTICLE_DISCOVERED,
        EventType.ARTICLE_NORMALIZED,
    ]


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


def test_changed_content_does_not_repeat_discovery_event(session: Session) -> None:
    source = _source(session)
    service = ArticlePersistenceService(session)
    first_correlation = uuid4()

    service.persist(
        _normalized(source.id, body="Version one"),
        correlation_id=first_correlation,
        predecessor_event_factory=_discovered_factory(first_correlation),
    )
    second_correlation = uuid4()
    second = service.persist(
        _normalized(source.id, body="Version two"),
        correlation_id=second_correlation,
        predecessor_event_factory=_discovered_factory(second_correlation),
    )

    assert second.created_article is False
    assert second.created_version is True
    events = list(session.scalars(select(EventOutbox).order_by(EventOutbox.created_at, EventOutbox.id)))
    assert [event.event_type for event in events] == [
        EventType.ARTICLE_DISCOVERED,
        EventType.ARTICLE_NORMALIZED,
        EventType.ARTICLE_NORMALIZED,
    ]
    assert events[-1].correlation_id == second_correlation
    assert events[-1].causation_id is None


def test_invalid_predecessor_rolls_back_when_caller_rolls_back_transaction(session: Session) -> None:
    source = _source(session)
    source_id = source.id
    session.commit()

    def invalid_predecessor(_article: Article, _normalized: NormalizedArticle) -> EventEnvelope:
        return EventEnvelope(
            event_type=EventType.ARTICLE_DISCOVERED,
            producer="collector",
            producer_version="0.1.0",
            aggregate_type="story",
            aggregate_id=uuid4(),
            idempotency_key="invalid-predecessor",
        )

    with pytest.raises(ValueError, match="predecessor event"), session.begin():
        ArticlePersistenceService(session).persist(
            _normalized(source_id),
            predecessor_event_factory=invalid_predecessor,
        )

    assert _count(session, Article) == 0
    assert _count(session, ArticleVersion) == 0
    assert _count(session, EventOutbox) == 0


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
