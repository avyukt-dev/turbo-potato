from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
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
    SourcePolicyConfig,
)
from news_ai_evidence.source_policy import _shingle_similarity
from pydantic import ValidationError
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


def _resolver(*, threshold: float | None = None) -> SourceEvidenceResolver:
    policy = ResearchPolicyLoader(ConfigLoader("config")).load()
    if threshold is not None:
        policy = policy.__class__(
            source_policy=policy.source_policy,
            corroboration=policy.corroboration.model_copy(
                update={"near_duplicate_similarity_threshold": threshold}
            ),
        )
    return SourceEvidenceResolver(policy)


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


@pytest.mark.parametrize(
    "identity_key",
    ("primary_document_id", "dataset_id", "eyewitness_record_id", "source_record_id"),
)
def test_same_explicit_origin_identity_is_known_shared(identity_key: str) -> None:
    factory = _factory()
    claim_id = uuid4()
    with factory() as session, session.begin():
        first = _record(
            session,
            1,
            body="The first outlet describes the originating record.",
            version_metadata={identity_key: "origin-record:shared"},
        )
        second = _record(
            session,
            2,
            body="A differently worded account cites that same record.",
            version_metadata={identity_key: "origin-record:shared"},
        )
        result = _resolver().resolve(
            session, (_candidate(claim_id, *first), _candidate(claim_id, *second))
        )

    values = tuple(result.values())
    assert {item.lineage.status for item in values} == {LineageStatus.KNOWN_SHARED}
    assert {item.lineage.basis for item in values} == {f"shared-{identity_key}"}
    assert len({item.lineage.independence_group for item in values}) == 1


@pytest.mark.parametrize(
    "identity_key",
    ("primary_document_id", "dataset_id", "eyewitness_record_id", "source_record_id"),
)
def test_distinct_explicit_origin_identities_are_independent(identity_key: str) -> None:
    factory = _factory()
    claim_id = uuid4()
    with factory() as session, session.begin():
        first = _record(
            session,
            1,
            body="The first originating record contains one observation.",
            version_metadata={identity_key: "origin-record:first"},
        )
        second = _record(
            session,
            2,
            body="The second originating record contains another observation.",
            version_metadata={identity_key: "origin-record:second"},
        )
        result = _resolver().resolve(
            session, (_candidate(claim_id, *first), _candidate(claim_id, *second))
        )

    values = tuple(result.values())
    assert {item.lineage.status for item in values} == {LineageStatus.INDEPENDENT}
    assert {item.lineage.basis for item in values} == {f"explicit-{identity_key}"}
    assert len({item.lineage.independence_group for item in values}) == 2


def test_distinct_durable_records_without_positive_origin_are_unresolved() -> None:
    factory = _factory()
    claim_id = uuid4()
    with factory() as session, session.begin():
        records = (
            _record(session, 1, body="A municipal budget meeting discussed bridge repairs."),
            _record(session, 2, body="A coastal forecast described unusually calm conditions."),
            _record(session, 3, body="A theatre company announced its winter programme."),
        )
        result = _resolver().resolve(
            session, tuple(_candidate(claim_id, *record) for record in records)
        )

    assert {item.lineage.status for item in result.values()} == {LineageStatus.UNRESOLVED}
    assert {item.lineage.independence_group for item in result.values()} == {None}
    assert {item.lineage.basis for item in result.values()} == {
        "no-positive-lineage-or-independence-signal"
    }


def test_near_duplicate_similarity_threshold_is_inclusive() -> None:
    first_body = "one two three four five six seven eight nine ten shared report material"
    second_body = first_body + " update"
    similarity = _shingle_similarity(first_body, second_body, 5)
    factory = _factory()
    claim_id = uuid4()
    with factory() as session, session.begin():
        first = _record(session, 1, body=first_body)
        second = _record(session, 2, body=second_body)
        candidates = (_candidate(claim_id, *first), _candidate(claim_id, *second))
        at_threshold = _resolver(threshold=similarity).resolve(session, candidates)
        below_threshold = _resolver(threshold=min(1.0, similarity + 0.001)).resolve(
            session, candidates
        )

    assert {item.lineage.status for item in at_threshold.values()} == {
        LineageStatus.INFERRED_SHARED
    }
    assert {item.lineage.status for item in below_threshold.values()} == {LineageStatus.UNRESOLVED}


def test_transitive_component_preserves_truthful_per_member_basis() -> None:
    factory = _factory()
    claim_id = uuid4()
    shared_text = "one two three four five six seven eight nine ten shared dispatch"
    with factory() as session, session.begin():
        known_only = _record(
            session,
            1,
            body="Text unrelated to the syndicated wording.",
            source_metadata={"wire_origin": "wire:transitive"},
        )
        bridge = _record(
            session,
            2,
            body=shared_text,
            source_metadata={"wire_origin": "wire:transitive"},
        )
        inferred_only = _record(session, 3, body=shared_text + " update")
        result = _resolver().resolve(
            session,
            tuple(_candidate(claim_id, *record) for record in (known_only, bridge, inferred_only)),
        )

    by_url = {item.article.canonical_url: item.lineage for item in result.values()}
    known = by_url[known_only[1].canonical_url]
    bridging = by_url[bridge[1].canonical_url]
    inferred = by_url[inferred_only[1].canonical_url]
    assert known.status is LineageStatus.KNOWN_SHARED
    assert known.basis == "shared-wire_origin"
    assert bridging.status is LineageStatus.KNOWN_SHARED
    assert bridging.basis == "shared-wire_origin"
    assert inferred.status is LineageStatus.INFERRED_SHARED
    assert inferred.basis == "near-duplicate-content"
    assert (
        len({known.independence_group, bridging.independence_group, inferred.independence_group})
        == 1
    )


@pytest.mark.parametrize(
    ("rule", "value"),
    (
        ("primary_is_not_automatic_truth", False),
        ("preserve_source_lineage", False),
        ("discovery_is_not_sufficient_for_serious_claims", False),
        ("unknown_effective_level", SourceAuthorityLevel.ESTABLISHED_SECONDARY),
    ),
)
def test_source_policy_rejects_disabled_canonical_safety_invariants(
    rule: str, value: object
) -> None:
    policy = ResearchPolicyLoader(ConfigLoader("config")).load().source_policy
    raw = policy.model_dump(mode="python")
    raw["rules"][rule] = value

    with pytest.raises(ValidationError):
        SourcePolicyConfig.model_validate(raw)


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
