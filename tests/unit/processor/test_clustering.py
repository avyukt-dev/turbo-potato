from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from news_ai_database import (
    Article,
    ArticleVersion,
    Base,
    EventOutbox,
    Source,
    Story,
    StorySource,
)
from news_ai_events import EventEnvelope, EventType
from news_ai_processor import (
    ArticleNormalizedWorkItem,
    StoryClusteringConfig,
    StoryClusteringService,
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


def _source(session: Session, name: str) -> Source:
    source = Source(name=name, domain=f"{name.casefold()}.example", source_type="NEWS")
    session.add(source)
    session.flush()
    return source


def _article(
    session: Session,
    source: Source,
    *,
    title: str | None,
    published_at: datetime,
    language: str = "en",
    body: str = "Body",
    version_number: int = 1,
    article: Article | None = None,
) -> tuple[Article, ArticleVersion, ArticleNormalizedWorkItem, EventEnvelope]:
    if article is None:
        article = Article(
            source_id=source.id,
            canonical_url=f"https://{source.domain}/item/{uuid4()}",
            title=title,
            language=language,
            published_at=published_at,
        )
        session.add(article)
        session.flush()
    content_hash = f"{version_number:064x}"
    retrieved_at = published_at + timedelta(minutes=5)
    version = ArticleVersion(
        article_id=article.id,
        version_number=version_number,
        content_hash=content_hash,
        body=body,
        retrieved_at=retrieved_at,
        version_metadata={"summary": f"Summary {version_number}"},
    )
    session.add(version)
    session.flush()
    work_item = ArticleNormalizedWorkItem(
        article_id=article.id,
        article_version_id=version.id,
        version_number=version_number,
        source_id=source.id,
        canonical_url=article.canonical_url,
        content_hash=content_hash,
        published_at=published_at,
        retrieved_at=retrieved_at,
    )
    event = EventEnvelope(
        event_type=EventType.ARTICLE_NORMALIZED,
        producer="processor",
        producer_version="0.1.0",
        aggregate_type="article",
        aggregate_id=article.id,
        correlation_id=uuid4(),
        idempotency_key=f"article.normalized:{article.id}:{version_number}",
        payload=work_item.model_dump(mode="json"),
    )
    return article, version, work_item, event


def _count(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _outbox_event(session: Session, event_type: EventType) -> EventOutbox:
    event = session.scalar(
        select(EventOutbox)
        .where(EventOutbox.event_type == event_type.value)
        .order_by(EventOutbox.created_at.desc())
        .limit(1)
    )
    assert event is not None
    return event


def test_first_article_creates_story_relation_and_story_created_event(session: Session) -> None:
    source = _source(session, "Alpha")
    article, _, item, trigger = _article(
        session,
        source,
        title="India and France sign defence agreement in Paris",
        published_at=datetime(2026, 9, 10, 3, 0, tzinfo=UTC),
    )

    result = StoryClusteringService().cluster(session, trigger, item)

    assert result.created_story is True
    assert result.created_relation is True
    assert result.cluster_method == "new-story"
    assert _count(session, Story) == 1
    assert _count(session, StorySource) == 1

    story = session.get(Story, result.story_id)
    relation = session.get(StorySource, (result.story_id, article.id))
    assert story is not None
    assert relation is not None
    assert relation.relationship_type == "ORIGIN"
    assert story.canonical_headline == article.title
    assert story.cluster_key is not None
    assert len(story.cluster_key) == 32

    outbox = _outbox_event(session, EventType.STORY_CREATED)
    assert outbox.event_id == result.event_id
    assert outbox.aggregate_id == story.id
    assert outbox.correlation_id == trigger.correlation_id
    assert outbox.causation_id == trigger.event_id
    assert outbox.payload == {
        "story_id": str(story.id),
        "article_id": str(article.id),
        "cluster_key": story.cluster_key,
    }


def test_similar_recent_article_clusters_into_existing_story(session: Session) -> None:
    alpha = _source(session, "Alpha")
    beta = _source(session, "Beta")
    anchor = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
    first_article, _, first_item, first_trigger = _article(
        session,
        alpha,
        title="India and France sign defence agreement in Paris",
        published_at=anchor,
    )
    service = StoryClusteringService()
    first = service.cluster(session, first_trigger, first_item)

    second_article, _, second_item, second_trigger = _article(
        session,
        beta,
        title="France and India sign new defence agreement in Paris",
        published_at=anchor + timedelta(hours=2),
    )
    second = service.cluster(session, second_trigger, second_item)

    assert second.story_id == first.story_id
    assert second.created_story is False
    assert second.created_relation is True
    assert second.cluster_method == "lexical+time"
    assert second.similarity_score >= 0.62
    assert _count(session, Story) == 1
    assert _count(session, StorySource) == 2

    relation = session.get(StorySource, (first.story_id, second_article.id))
    assert relation is not None
    assert relation.relationship_type == "RELATED"

    outbox = _outbox_event(session, EventType.STORY_CLUSTERED)
    assert outbox.correlation_id == second_trigger.correlation_id
    assert outbox.causation_id == second_trigger.event_id
    assert outbox.payload["cluster_method"] == "lexical+time"
    assert set(outbox.payload["article_ids"]) == {
        str(first_article.id),
        str(second_article.id),
    }


def test_unrelated_recent_article_creates_separate_story(session: Session) -> None:
    alpha = _source(session, "Alpha")
    beta = _source(session, "Beta")
    anchor = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
    service = StoryClusteringService()

    _, _, first_item, first_trigger = _article(
        session,
        alpha,
        title="India and France sign defence agreement in Paris",
        published_at=anchor,
    )
    first = service.cluster(session, first_trigger, first_item)
    _, _, second_item, second_trigger = _article(
        session,
        beta,
        title="Scientists discover new coral species in Pacific Ocean",
        published_at=anchor + timedelta(hours=1),
    )
    second = service.cluster(session, second_trigger, second_item)

    assert second.story_id != first.story_id
    assert second.created_story is True
    assert _count(session, Story) == 2


def test_similar_article_outside_time_window_creates_separate_story(session: Session) -> None:
    alpha = _source(session, "Alpha")
    beta = _source(session, "Beta")
    anchor = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
    service = StoryClusteringService(StoryClusteringConfig(time_window_hours=48))

    _, _, first_item, first_trigger = _article(
        session,
        alpha,
        title="India and France sign defence agreement in Paris",
        published_at=anchor,
    )
    first = service.cluster(session, first_trigger, first_item)
    _, _, second_item, second_trigger = _article(
        session,
        beta,
        title="India and France sign defence agreement in Paris",
        published_at=anchor + timedelta(days=5),
    )
    second = service.cluster(session, second_trigger, second_item)

    assert second.story_id != first.story_id
    assert second.created_story is True
    assert _count(session, Story) == 2


def test_cross_language_first_pass_does_not_auto_merge(session: Session) -> None:
    alpha = _source(session, "Alpha")
    beta = _source(session, "Beta")
    anchor = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
    service = StoryClusteringService()

    _, _, first_item, first_trigger = _article(
        session,
        alpha,
        title="India France defence agreement Paris",
        published_at=anchor,
        language="en",
    )
    first = service.cluster(session, first_trigger, first_item)
    _, _, second_item, second_trigger = _article(
        session,
        beta,
        title="India France defence agreement Paris",
        published_at=anchor + timedelta(minutes=30),
        language="hi",
    )
    second = service.cluster(session, second_trigger, second_item)

    assert second.story_id != first.story_id
    assert second.created_story is True


def test_missing_headline_is_not_auto_clustered(session: Session) -> None:
    alpha = _source(session, "Alpha")
    beta = _source(session, "Beta")
    anchor = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
    service = StoryClusteringService()

    _, _, first_item, first_trigger = _article(
        session,
        alpha,
        title=None,
        published_at=anchor,
    )
    first = service.cluster(session, first_trigger, first_item)
    _, _, second_item, second_trigger = _article(
        session,
        beta,
        title=None,
        published_at=anchor + timedelta(minutes=10),
    )
    second = service.cluster(session, second_trigger, second_item)

    assert second.story_id != first.story_id
    assert _count(session, Story) == 2


def test_article_version_reuses_story_and_emits_update_signal(session: Session) -> None:
    source = _source(session, "Alpha")
    anchor = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
    service = StoryClusteringService()
    article, _, first_item, first_trigger = _article(
        session,
        source,
        title="India and France sign defence agreement in Paris",
        published_at=anchor,
    )
    first = service.cluster(session, first_trigger, first_item)

    _, _, second_item, second_trigger = _article(
        session,
        source,
        article=article,
        title=article.title,
        published_at=anchor,
        body="Updated body",
        version_number=2,
    )
    second = service.cluster(session, second_trigger, second_item)

    assert second.story_id == first.story_id
    assert second.created_story is False
    assert second.created_relation is False
    assert second.cluster_method == "existing-article"
    assert _count(session, StorySource) == 1

    outbox = _outbox_event(session, EventType.STORY_CLUSTERED)
    assert outbox.causation_id == second_trigger.event_id
    assert outbox.payload["cluster_method"] == "existing-article"
    assert outbox.payload["article_ids"] == [str(article.id)]


def test_persisted_state_mismatch_is_rejected_before_clustering(session: Session) -> None:
    source = _source(session, "Alpha")
    _, _, item, trigger = _article(
        session,
        source,
        title="India and France sign defence agreement in Paris",
        published_at=datetime(2026, 9, 10, 3, 0, tzinfo=UTC),
    )
    mismatched_item = item.model_copy(update={"content_hash": "f" * 64})

    with pytest.raises(ValueError, match="content hash"):
        StoryClusteringService().cluster(session, trigger, mismatched_item)

    assert _count(session, Story) == 0
    assert _count(session, StorySource) == 0


def test_cluster_key_is_deterministic_for_same_anchor_and_headline(session: Session) -> None:
    source = _source(session, "Alpha")
    anchor = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
    article, _, item, trigger = _article(
        session,
        source,
        title="India and France sign defence agreement in Paris",
        published_at=anchor,
    )
    first = StoryClusteringService().cluster(session, trigger, item)
    first_story = session.get(Story, first.story_id)
    assert first_story is not None

    relation = session.get(StorySource, (first.story_id, article.id))
    assert relation is not None
    session.delete(relation)
    session.delete(first_story)
    session.flush()

    _, _, item2, trigger2 = _article(
        session,
        source,
        title="India and France sign defence agreement in Paris",
        published_at=anchor,
    )
    second = StoryClusteringService().cluster(session, trigger2, item2)
    second_story = session.get(Story, second.story_id)
    assert second_story is not None
    assert first_story.cluster_key == second_story.cluster_key


def test_multiple_existing_story_links_fail_closed(session: Session) -> None:
    source = _source(session, "Alpha")
    article, _, item, trigger = _article(
        session,
        source,
        title="India and France sign defence agreement in Paris",
        published_at=datetime(2026, 9, 10, 3, 0, tzinfo=UTC),
    )
    story_a = Story(canonical_headline="A", status="DISCOVERED")
    story_b = Story(canonical_headline="B", status="DISCOVERED")
    session.add_all([story_a, story_b])
    session.flush()
    session.add_all(
        [
            StorySource(story_id=story_a.id, article_id=article.id, relationship_type="RELATED"),
            StorySource(story_id=story_b.id, article_id=article.id, relationship_type="RELATED"),
        ]
    )
    session.flush()

    with pytest.raises(RuntimeError, match="multiple stories"):
        StoryClusteringService().cluster(session, trigger, item)
