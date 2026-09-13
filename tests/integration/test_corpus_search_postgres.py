"""Real PostgreSQL streaming and completeness checks for collected-corpus search."""

import asyncio
import os
import tracemalloc
from datetime import UTC, datetime
from uuid import UUID

import pytest
from news_ai_database import Article, ArticleVersion, Base, Source
from news_ai_evidence import (
    PostgresArticleSearchProvider,
    SearchCapability,
    SearchQueryFamily,
    SearchRequest,
)
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def factory():
    url = os.getenv("NEWS_AI_DATABASE_URL")
    if not url or os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("explicit disposable PostgreSQL 16 service required")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as connection:
        tables = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
        connection.exec_driver_sql(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")
    try:
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()


def _request(query="needleworthy", *, max_results=10):
    return SearchRequest(
        query=query,
        query_family=SearchQueryFamily.ENTITY_EVENT,
        capability=SearchCapability.NEWS,
        max_results=max_results,
    )


def _seed(factory, *, total, all_match):
    now = datetime(2026, 9, 13, tzinfo=UTC)
    source_id = UUID(int=9000)
    with factory() as session, session.begin():
        session.add(
            Source(id=source_id, name="Synthetic", domain="synthetic.example", source_type="NEWS")
        )
        articles, versions = [], []
        for index in range(total):
            article_id = UUID(int=index + 1)
            match = all_match or index == total - 1
            body = (
                f"Needleworthy synthetic record number {index}."
                if match
                else f"Unrelated synthetic archive number {index}."
            )
            articles.append(
                {
                    "id": article_id,
                    "source_id": source_id,
                    "canonical_url": f"https://synthetic.example/{index:05d}",
                    "title": "Synthetic record",
                    "language": "en",
                    "published_at": now,
                }
            )
            versions.append(
                {
                    "id": UUID(int=100_000 + index),
                    "article_id": article_id,
                    "version_number": 1,
                    "content_hash": f"{index + 1:064x}",
                    "body": body,
                    "retrieved_at": now,
                    "version_metadata": {},
                }
            )
        session.execute(insert(Article), articles)
        session.execute(insert(ArticleVersion), versions)


def test_postgres_streams_complete_corpus_and_finds_late_match(factory):
    _seed(factory, total=2001, all_match=False)
    options = []

    @event.listens_for(factory.kw["bind"], "before_cursor_execute")
    def observe(connection, cursor, statement, parameters, context, executemany):
        if "article_versions" in statement and statement.lstrip().upper().startswith("SELECT"):
            options.append(context.execution_options)

    response = asyncio.run(PostgresArticleSearchProvider(factory).search(_request()))

    assert [result.url for result in response.results] == ["https://synthetic.example/02000"]
    assert any(item.get("stream_results") is True for item in options)
    assert any(item.get("yield_per") == 256 for item in options)


def test_postgres_all_match_corpus_has_bounded_python_memory_and_exact_top_k(factory):
    _seed(factory, total=3000, all_match=True)
    tracemalloc.start()
    response = asyncio.run(PostgresArticleSearchProvider(factory).search(_request(max_results=7)))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert [result.url for result in response.results] == [
        f"https://synthetic.example/{index:05d}" for index in range(7)
    ]
    assert peak / 1024**2 < 15
