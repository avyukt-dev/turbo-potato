from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from news_ai_database import Article, ArticleVersion, Base, Source
from news_ai_evidence import (
    PostgresArticleSearchProvider,
    SearchCapability,
    SearchCapabilityError,
    SearchQueryFamily,
    SearchRequest,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def _seed() -> tuple[sessionmaker[Session], ArticleVersion, ArticleVersion]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 11, tzinfo=UTC)
    with factory() as session, session.begin():
        source = Source(
            name="Corpus News",
            domain="news.example",
            source_type="NEWS",
            authority_level=2,
            language="en",
            source_metadata={},
        )
        session.add(source)
        session.flush()
        first = Article(
            source_id=source.id,
            canonical_url="https://news.example/a",
            title="Independent eyewitness records flood level",
            language="en",
            published_at=now - timedelta(days=2),
        )
        second = Article(
            source_id=source.id,
            canonical_url="https://news.example/b",
            title="Flood level update",
            language="en",
            published_at=now - timedelta(days=1),
        )
        session.add_all([first, second])
        session.flush()
        old = ArticleVersion(
            article_id=first.id,
            version_number=1,
            content_hash="a" * 64,
            body="Earlier flood report.",
            retrieved_at=now - timedelta(days=2),
            version_metadata={},
        )
        current = ArticleVersion(
            article_id=first.id,
            version_number=2,
            content_hash="b" * 64,
            body="An independent eyewitness recorded the flood level at two metres.",
            retrieved_at=now - timedelta(days=1),
            version_metadata={},
        )
        other = ArticleVersion(
            article_id=second.id,
            version_number=1,
            content_hash="c" * 64,
            body="The official flood level remains under review.",
            retrieved_at=now,
            version_metadata={},
        )
        session.add_all([old, current, other])
    return factory, current, other


def _request(**updates: object) -> SearchRequest:
    values: dict[str, object] = {
        "query": "flood level",
        "query_family": SearchQueryFamily.ENTITY_EVENT,
        "capability": SearchCapability.NEWS,
        "language": "en",
        "max_results": 10,
    }
    values.update(updates)
    return SearchRequest(**values)


def test_corpus_provider_returns_latest_version_with_deterministic_provenance() -> None:
    factory, current, _ = _seed()

    def clock() -> datetime:
        return datetime(2026, 9, 11, 12, tzinfo=UTC)

    provider = PostgresArticleSearchProvider(factory, clock=clock)

    first = asyncio.run(provider.search(_request()))
    second = asyncio.run(provider.search(_request(request_id=first.request_id)))

    assert provider.capabilities.capabilities == frozenset({SearchCapability.NEWS})
    assert [item.url for item in first.results] == [item.url for item in second.results]
    assert first.results[0].metadata["article_version_id"] == str(current.id)
    assert first.results[0].metadata["content_hash"] == current.content_hash
    assert "Earlier flood report" not in (first.results[0].snippet or "")
    assert len({item.url for item in first.results}) == len(first.results)


def test_corpus_provider_enforces_filters_limits_and_empty_results() -> None:
    factory, _, _ = _seed()
    provider = PostgresArticleSearchProvider(factory)

    limited = asyncio.run(provider.search(_request(max_results=1)))
    excluded = asyncio.run(provider.search(_request(exclude_domains=("news.example",))))
    wrong_language = asyncio.run(provider.search(_request(language="hi")))
    empty = asyncio.run(provider.search(_request(query="no matching vocabulary")))

    assert len(limited.results) == 1
    assert excluded.results == ()
    assert wrong_language.results == ()
    assert empty.results == ()


def test_corpus_provider_rejects_unsupported_web_capability() -> None:
    factory, _, _ = _seed()
    provider = PostgresArticleSearchProvider(factory)

    with pytest.raises(SearchCapabilityError):
        asyncio.run(provider.search(_request(capability=SearchCapability.WEB)))


def test_corpus_provider_does_not_silently_truncate_before_matching() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 11, tzinfo=UTC)
    with factory() as session, session.begin():
        source = Source(
            name="Large corpus",
            domain="large.example",
            source_type="NEWS",
            authority_level=4,
            language="en",
            source_metadata={},
        )
        session.add(source)
        session.flush()
        for index in range(1, 2002):
            article = Article(
                id=UUID(int=index),
                source_id=source.id,
                canonical_url=f"https://large.example/{index}",
                title="Routine corpus entry",
                language="en",
                published_at=now,
            )
            session.add(article)
            session.add(
                ArticleVersion(
                    article_id=article.id,
                    version_number=1,
                    content_hash=f"{index:064x}",
                    body="Unrelated archived material.",
                    retrieved_at=now,
                    version_metadata={},
                )
            )
        relevant = Article(
            id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
            source_id=source.id,
            canonical_url="https://large.example/relevant",
            title="Needleworthy investigation",
            language="en",
            published_at=now,
        )
        session.add(relevant)
        session.add(
            ArticleVersion(
                article_id=relevant.id,
                version_number=1,
                content_hash="f" * 64,
                body="The uniquely needleworthy record is present.",
                retrieved_at=now,
                version_metadata={},
            )
        )

    response = asyncio.run(
        PostgresArticleSearchProvider(factory).search(_request(query="needleworthy"))
    )

    assert [item.url for item in response.results] == [relevant.canonical_url]
