from datetime import UTC, datetime
from uuid import uuid4

from news_ai_collector import (
    CollectedArticle,
    CollectedMediaCandidate,
    DiscoveredArticleHandler,
    FeedMediaOrigin,
    FeedMediaType,
)
from news_ai_database import ArticleDiscovery, Base, EventOutbox
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


def _article(*, media_url: str | None) -> CollectedArticle:
    candidates = ()
    if media_url is not None:
        candidates = (
            CollectedMediaCandidate(
                url=media_url,
                media_type=FeedMediaType.IMAGE,
                origin=FeedMediaOrigin.MEDIA_CONTENT,
                mime_type="image/jpeg",
                credit="District administration",
            ),
        )
    return CollectedArticle(
        source_id=uuid4(),
        source_feed_id=uuid4(),
        url="https://example.com/story",
        title="Incident report",
        media_candidates=candidates,
    )


def test_discovery_persists_candidates_but_keeps_event_payload_small() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    article = _article(media_url="https://example.com/incident.jpg")

    with Session(engine) as session, session.begin():
        result = DiscoveredArticleHandler()(session, article, retrieved_at=datetime.now(UTC))
        discovery = session.get(ArticleDiscovery, result.discovery_id)
        event = session.scalar(select(EventOutbox))
        assert discovery is not None and event is not None
        assert discovery.raw_payload["media_candidates"] == [
            article.media_candidates[0].model_dump(mode="json")
        ]
        assert "media_candidates" not in event.payload

    engine.dispose()


def test_media_less_discovery_preserves_legacy_payload_shape() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session, session.begin():
        result = DiscoveredArticleHandler()(
            session,
            _article(media_url=None),
            retrieved_at=datetime.now(UTC),
        )
        discovery = session.get(ArticleDiscovery, result.discovery_id)
        assert discovery is not None
        assert "media_candidates" not in discovery.raw_payload

    engine.dispose()
