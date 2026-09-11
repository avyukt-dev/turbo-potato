from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from news_ai_common.config import ConfigLoader
from news_ai_database import Article, ArticleVersion, Base, Source
from news_ai_evidence import (
    CandidateSourceType,
    LineageStatus,
    ResearchCandidate,
    ResearchPolicyLoader,
    ResearchTargetRole,
    SearchQueryFamily,
    SearchResult,
    SourceAuthorityLevel,
    SourceEvidenceResolver,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _candidate(
    claim_id: UUID,
    source: Source,
    article: Article,
    version: ArticleVersion,
) -> ResearchCandidate:
    return ResearchCandidate(
        claim_id=claim_id,
        query_family=SearchQueryFamily.ENTITY_EVENT,
        target_role=ResearchTargetRole.INDEPENDENT_REPORTING,
        request_id=uuid4(),
        provider_id="postgres-article-corpus",
        retrieved_at=version.retrieved_at,
        result=SearchResult(
            url=article.canonical_url,
            title=article.title,
            snippet=version.body,
            source_name=source.name,
            candidate_type=CandidateSourceType.NEWS_ARTICLE,
            rank=1,
            published_at=article.published_at,
            updated_at=version.retrieved_at,
            language=article.language,
            metadata={
                "source_id": str(source.id),
                "article_id": str(article.id),
                "article_version_id": str(version.id),
                "content_hash": version.content_hash,
            },
        ),
    )


def _record(
    session: Session,
    index: int,
    *,
    source: Source | None = None,
    body: str,
    content_hash: str | None = None,
    authority_level: int | None = None,
    source_metadata: dict[str, object] | None = None,
    version_metadata: dict[str, object] | None = None,
) -> tuple[Source, Article, ArticleVersion]:
    source = source or Source(
        name=f"Publisher {index}",
        domain=f"publisher-{index}.example",
        source_type="NEWS",
        authority_level=authority_level,
        source_metadata=source_metadata or {},
    )
    if source.id is None:
        session.add(source)
        session.flush()
    article = Article(
        source_id=source.id,
        canonical_url=f"https://publisher-{index}.example/report-{index}",
        title=f"Report {index}",
        language="en",
        published_at=datetime(2026, 9, 11, tzinfo=UTC),
    )
    session.add(article)
    session.flush()
    version = ArticleVersion(
        article_id=article.id,
        version_number=1,
        content_hash=content_hash or f"{index:064x}",
        body=body,
        retrieved_at=datetime(2026, 9, 11, tzinfo=UTC),
        version_metadata=version_metadata or {},
    )
    session.add(version)
    session.flush()
    return source, article, version


def _resolver() -> SourceEvidenceResolver:
    return SourceEvidenceResolver(ResearchPolicyLoader(ConfigLoader("config")).load())


def test_wire_republication_and_exact_duplicates_form_one_group() -> None:
    factory = _factory()
    claim_id = uuid4()
    with factory() as session, session.begin():
        rows = [
            _record(
                session,
                index,
                body="Wire copy reports the same event with identical facts.",
                content_hash="a" * 64,
                source_metadata={"wire_origin": "agency:wire-123"},
            )
            for index in range(1, 11)
        ]
        candidates = tuple(_candidate(claim_id, *row) for row in rows)
        result = _resolver().resolve(session, candidates)

    resolutions = tuple(result.values())
    assert {item.lineage.status for item in resolutions} == {LineageStatus.KNOWN_SHARED}
    assert len({item.lineage.independence_group for item in resolutions}) == 1


def test_shared_police_statement_and_research_paper_are_not_multiple_confirmations() -> None:
    factory = _factory()
    claim_id = uuid4()
    with factory() as session, session.begin():
        police = [
            _record(
                session,
                index,
                body=f"Outlet {index} quotes the police statement.",
                version_metadata={"citation_source_url": "https://police.example/statement/7"},
            )
            for index in range(1, 6)
        ]
        paper = [
            _record(
                session,
                index + 10,
                body=f"Outlet {index} describes one paper.",
                version_metadata={"primary_document_id": "doi:10.1234/example"},
            )
            for index in range(1, 4)
        ]
        result = _resolver().resolve(
            session, tuple(_candidate(claim_id, *row) for row in police + paper)
        )

    groups = {item.lineage.independence_group for item in result.values()}
    assert len(groups) == 2
    assert all(item.lineage.status is LineageStatus.KNOWN_SHARED for item in result.values())


def test_near_duplicate_syndication_and_same_publisher_group_conservatively() -> None:
    factory = _factory()
    claim_id = uuid4()
    base = "one two three four five six seven eight nine ten shared report material"
    with factory() as session, session.begin():
        first = _record(session, 1, body=base)
        second = _record(session, 2, body=base + " update")
        publisher = Source(
            name="Single Publisher",
            domain="single.example",
            source_type="NEWS",
            source_metadata={},
        )
        session.add(publisher)
        session.flush()
        third = _record(session, 3, source=publisher, body="Distinct first dispatch text")
        fourth = _record(session, 4, source=publisher, body="Different follow-up text")
        result = _resolver().resolve(
            session,
            tuple(_candidate(claim_id, *row) for row in (first, second, third, fourth)),
        )

    values = list(result.values())
    assert values[0].lineage.status is LineageStatus.INFERRED_SHARED
    assert values[0].lineage.independence_group == values[1].lineage.independence_group
    assert values[2].lineage.status is LineageStatus.KNOWN_SHARED
    assert values[2].lineage.independence_group == values[3].lineage.independence_group


def test_distinct_primary_records_remain_distinct_and_unknown_authority_is_conservative() -> None:
    factory = _factory()
    claim_id = uuid4()
    with factory() as session, session.begin():
        first = _record(
            session,
            1,
            body="Eyewitness A independently recorded the event.",
            authority_level=1,
            version_metadata={"primary_document_id": "eyewitness-record:a"},
        )
        second = _record(
            session,
            2,
            body="Eyewitness B independently recorded another observation.",
            version_metadata={"primary_document_id": "eyewitness-record:b"},
        )
        result = _resolver().resolve(
            session, (_candidate(claim_id, *first), _candidate(claim_id, *second))
        )

    values = list(result.values())
    assert {item.lineage.status for item in values} == {LineageStatus.INDEPENDENT}
    assert len({item.lineage.independence_group for item in values}) == 2
    assert values[0].authority.effective_level is SourceAuthorityLevel.PRIMARY
    assert values[1].authority.effective_level is SourceAuthorityLevel.DISCOVERY


def test_unresolved_candidate_never_gets_an_independence_group() -> None:
    factory = _factory()
    claim_id = uuid4()
    with factory() as session:
        candidate = ResearchCandidate(
            claim_id=claim_id,
            query_family=SearchQueryFamily.EXACT_CLAIM,
            target_role=ResearchTargetRole.GENERAL_CONTEXT,
            request_id=uuid4(),
            provider_id="external-future-provider",
            retrieved_at=datetime(2026, 9, 11, tzinfo=UTC),
            result=SearchResult(
                url="https://unknown.example/item",
                rank=1,
                metadata={},
            ),
        )
        resolution = _resolver().resolve(session, (candidate,))[(claim_id, candidate.result.url)]

    assert resolution.lineage.status is LineageStatus.UNRESOLVED
    assert resolution.lineage.independence_group is None
    assert resolution.authority.effective_level is SourceAuthorityLevel.DISCOVERY
