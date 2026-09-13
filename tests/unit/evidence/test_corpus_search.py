from __future__ import annotations

import asyncio
import threading
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
from news_ai_evidence.corpus import (
    _aware_utc,
    _bounded_snippet,
    _BoundedCandidates,
    _CorpusCandidate,
    _lexical_score,
    _matches_filters,
    _tokens,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed() -> tuple[sessionmaker[Session], ArticleVersion, ArticleVersion]:
    engine = _engine()
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
    engine = _engine()
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


def _legacy_reference(factory, request):
    latest = (
        select(
            ArticleVersion.article_id,
            func.max(ArticleVersion.version_number).label("version_number"),
        )
        .group_by(ArticleVersion.article_id)
        .subquery()
    )
    tokens = _tokens(request.query)
    with factory() as session:
        rows = list(
            session.execute(
                select(Article, ArticleVersion, Source)
                .join(Source, Source.id == Article.source_id)
                .join(latest, latest.c.article_id == Article.id)
                .join(
                    ArticleVersion,
                    (ArticleVersion.article_id == latest.c.article_id)
                    & (ArticleVersion.version_number == latest.c.version_number),
                )
                .where(Source.is_active.is_(True))
                .order_by(Article.id, ArticleVersion.id)
            )
        )
    ranked = []
    for article, version, source in rows:
        if not _matches_filters(request, article, source):
            continue
        score = _lexical_score(tokens, " ".join(x for x in (article.title, version.body) if x))
        if score:
            ranked.append((score, article.canonical_url, str(version.id), article, version, source))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    expected, seen = [], set()
    for score, url, _, article, version, source in ranked:
        if url in seen:
            continue
        seen.add(url)
        expected.append(
            (
                url,
                score,
                str(article.id),
                str(version.id),
                str(source.id),
                version.version_number,
                version.content_hash,
                _bounded_snippet(version.body or article.title or "", tokens),
                article.title,
                source.name,
                article.language,
                _aware_utc(article.published_at).isoformat() if article.published_at else None,
                _aware_utc(version.retrieved_at).isoformat(),
            )
        )
        if len(expected) == request.max_results:
            break
    return expected


def _seed_parity_corpus():
    engine = _engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 11, tzinfo=UTC)
    with factory() as session, session.begin():
        sources = [
            Source(id=UUID(int=101), name="Alpha", domain="ALPHA.EXAMPLE", source_type="NEWS"),
            Source(id=UUID(int=102), name="Beta", domain="beta.example", source_type="NEWS"),
            Source(id=UUID(int=103), name="Fallback", domain=None, source_type="NEWS"),
            Source(
                id=UUID(int=104),
                name="Inactive",
                domain="off.example",
                source_type="NEWS",
                is_active=False,
            ),
        ]
        session.add_all(sources)
        rows = [
            (
                1,
                101,
                "https://shared.example/report",
                "Alpha delta",
                "en-US",
                -2,
                ["obsolete alpha delta", "current alpha"],
            ),
            (
                2,
                102,
                "https://shared.example/report",
                "Alpha delta",
                "en",
                -1,
                ["alpha delta body"],
            ),
            (3, 101, "https://alpha.example/title", "Alpha delta title", "en-IN", 0, ["body only"]),
            (4, 102, "https://beta.example/body", "Other title", "en", 1, ["alpha delta body"]),
            (5, 103, "https://fallback.example/item", "Alpha title", "en", 0, ["delta body"]),
            (6, 104, "https://off.example/item", "Alpha delta", "en", 0, ["alpha delta"]),
            (
                7,
                101,
                "https://alpha.example/enochian",
                "Alpha delta",
                "enochian",
                0,
                ["alpha delta"],
            ),
        ]
        for number, source_number, url, title, language, day, bodies in rows:
            article = Article(
                id=UUID(int=number),
                source_id=UUID(int=source_number),
                canonical_url=url,
                title=title,
                language=language,
                published_at=now + timedelta(days=day),
            )
            session.add(article)
            for version_number, body in enumerate(bodies, start=1):
                session.add(
                    ArticleVersion(
                        id=UUID(int=number * 100 + version_number),
                        article_id=article.id,
                        version_number=version_number,
                        content_hash=f"{number * 100 + version_number:064x}",
                        body=body,
                        retrieved_at=now + timedelta(hours=version_number),
                        version_metadata={},
                    )
                )
    return factory, now


@pytest.mark.parametrize(
    "updates",
    [
        {},
        {"max_results": 1},
        {"query": "delta"},
        {"query": "absent"},
        {"language": "en-GB"},
        {"include_domains": ("fallback.example",)},
        {"exclude_domains": ("alpha.example",)},
        {"published_after": datetime(2026, 9, 11, tzinfo=UTC)},
        {"published_before": datetime(2026, 9, 10, tzinfo=UTC)},
    ],
)
def test_streamed_search_matches_legacy_ranking_filters_and_provenance(updates):
    factory, _ = _seed_parity_corpus()
    request = _request(**{"query": "alpha delta", **updates})
    expected = _legacy_reference(factory, request)
    response = asyncio.run(PostgresArticleSearchProvider(factory).search(request))
    actual = [
        (
            item.url,
            _lexical_score(
                _tokens(request.query), " ".join(x for x in (item.title, item.snippet) if x)
            ),
            item.metadata["article_id"],
            item.metadata["article_version_id"],
            item.metadata["source_id"],
            item.metadata["article_version_number"],
            item.metadata["content_hash"],
            item.snippet,
            item.title,
            item.source_name,
            item.language,
            item.metadata["published_at"],
            item.metadata["source_retrieved_at"],
        )
        for item in response.results
    ]
    assert actual == expected
    assert [item.rank for item in response.results] == list(range(1, len(actual) + 1))
    if not updates:
        duplicate = next(item for item in response.results if item.url.endswith("/report"))
        assert duplicate.metadata["article_version_id"] == str(UUID(int=102))


def test_bounded_candidate_state_never_exceeds_top_k_and_keeps_best_duplicate():
    retained = _BoundedCandidates(3)
    now = datetime.now(UTC)
    for number in range(100):
        retained.consider(
            _CorpusCandidate(
                score=1 + number % 3,
                url=f"https://example.com/{number % 50}",
                version_id=UUID(int=1000 - number),
                article_id=UUID(int=number + 1),
                source_id=UUID(int=1),
                title=None,
                snippet="bounded",
                source_name="Source",
                published_at=None,
                retrieved_at=now,
                language=None,
                version_number=1,
                content_hash=f"{number:064x}",
            )
        )
    assert retained.maximum_size == len(retained.by_url) == 3
    assert retained.ordered() == sorted(retained.by_url.values(), key=lambda item: item.order_key)


def test_search_runs_sync_session_work_off_event_loop():
    factory, _, _ = _seed()
    provider = PostgresArticleSearchProvider(factory)
    entered, release = threading.Event(), threading.Event()
    event_loop_thread = threading.get_ident()
    search_threads = []
    original = provider._search_sync

    def blocked(request, tokens):
        search_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        return original(request, tokens)

    provider._search_sync = blocked

    async def scenario():
        search = asyncio.create_task(provider.search(_request()))
        await asyncio.to_thread(entered.wait, 5)
        ticked = False

        async def tick():
            nonlocal ticked
            await asyncio.sleep(0)
            ticked = True

        await tick()
        assert ticked and not search.done()
        release.set()
        await search
        assert search_threads and search_threads[0] != event_loop_thread

    asyncio.run(scenario())


def test_query_without_lexical_tokens_returns_empty_without_opening_session():
    calls = 0

    def forbidden_factory():
        nonlocal calls
        calls += 1
        raise AssertionError("tokenless search must not open a database session")

    response = asyncio.run(
        PostgresArticleSearchProvider(forbidden_factory).search(_request(query="___"))
    )
    assert response.results == () and calls == 0
