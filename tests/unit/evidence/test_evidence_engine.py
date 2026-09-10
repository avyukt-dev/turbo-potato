from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from news_ai_database import (
    Base,
    Claim,
    ClaimEvidence,
    EvidenceItem,
    EventOutbox,
    Job,
    Story,
)
from news_ai_domain import ClaimVerificationStatus, RiskLevel
from news_ai_events import EventEnvelope, EventType
from news_ai_evidence import (
    CandidateSourceType,
    EvidenceAssessment,
    EvidenceEngine,
    EvidenceRelation,
    ResearchCandidate,
    ResearchTargetRole,
    SearchBudgets,
    SearchCapability,
    SearchPolicy,
    SearchProviderCapabilities,
    SearchProviderRegistry,
    SearchQueryFamily,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchRules,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


@dataclass
class FakeSearchProvider:
    provider_id: str = "search-a"
    fail_news: bool = False

    @property
    def capabilities(self) -> SearchProviderCapabilities:
        return SearchProviderCapabilities(
            provider_id=self.provider_id,
            capabilities=frozenset({SearchCapability.WEB, SearchCapability.NEWS}),
            query_families=frozenset(SearchQueryFamily),
            max_results=10,
            supports_domain_filter=True,
            supports_date_filter=True,
            languages=frozenset({"en"}),
        )

    async def search(self, request: SearchRequest) -> SearchResponse:
        if self.fail_news and request.capability is SearchCapability.NEWS:
            from news_ai_evidence import SearchProviderUnavailableError

            raise SearchProviderUnavailableError("news endpoint unavailable")
        suffix = request.query_family.value.casefold().replace("_", "-")
        return SearchResponse(
            request_id=request.request_id,
            provider_id=self.provider_id,
            retrieved_at=datetime(2026, 9, 10, 8, 0, tzinfo=UTC),
            results=(
                SearchResult(
                    url=f"https://example.com/{suffix}",
                    title=f"Candidate for {request.query_family.value}",
                    snippet="Candidate source text; it is untrusted until assessed.",
                    source_name="Example",
                    candidate_type=CandidateSourceType.NEWS_ARTICLE,
                    rank=1,
                    published_at=datetime(2026, 9, 10, 7, 30, tzinfo=UTC),
                    language="en",
                ),
            ),
        )


class ExplicitAssessor:
    async def assess(
        self,
        candidate: ResearchCandidate,
        claim_text: str,
    ) -> EvidenceAssessment | None:
        relation = (
            EvidenceRelation.CONTRADICTS
            if candidate.target_role is ResearchTargetRole.CONTRADICTION
            else EvidenceRelation.DIRECT_SUPPORT
        )
        return EvidenceAssessment(
            claim_id=candidate.claim_id,
            candidate_url=candidate.result.url,
            relation=relation,
            strength_score=Decimal("0.60"),
            source_level=2,
            independence_group=candidate.result.url,
            notes=f"Explicit test assessment for: {claim_text}",
        )


class NoopAssessor:
    async def assess(
        self,
        candidate: ResearchCandidate,
        claim_text: str,
    ) -> EvidenceAssessment | None:
        return None


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _policy() -> SearchPolicy:
    return SearchPolicy(
        schema_version=1,
        rules=SearchRules(),
        budgets=SearchBudgets(default_timeout_seconds=90, breaking_news_timeout_seconds=45),
    )


def _engine(*, assessor=None, fail_news: bool = False) -> EvidenceEngine:
    provider = FakeSearchProvider(fail_news=fail_news)
    return EvidenceEngine(
        SearchProviderRegistry([provider]),
        _policy(),
        lambda _request, compatible: compatible[0],
        assessor or ExplicitAssessor(),
    )


def _seed(factory: sessionmaker[Session]) -> tuple[Story, Claim]:
    with factory() as session, session.begin():
        story = Story(
            canonical_headline="Policy announcement",
            status="DISCOVERED",
            language="en",
            risk_level=RiskLevel.LOW,
            story_metadata={},
        )
        session.add(story)
        session.flush()
        claim = Claim(
            story_id=story.id,
            claim_text="The government announced a new policy.",
            normalized_claim="the government announced a new policy",
            status=ClaimVerificationStatus.UNASSESSED,
            risk_level=RiskLevel.LOW,
            claim_metadata={},
        )
        session.add(claim)
    return story, claim


def _claims_event(story: Story, claim: Claim) -> EventEnvelope:
    return EventEnvelope(
        event_type=EventType.CLAIMS_EXTRACTED,
        producer="ai-worker",
        producer_version="0.1.0",
        aggregate_type="story",
        aggregate_id=story.id,
        idempotency_key=f"claims.extracted:{uuid4()}",
        payload={"story_id": str(story.id), "claim_ids": [str(claim.id)]},
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _requested_event(outbox: EventOutbox) -> EventEnvelope:
    return EventEnvelope(
        event_id=outbox.event_id,
        event_type=EventType(outbox.event_type),
        schema_version=outbox.schema_version,
        occurred_at=_aware(outbox.occurred_at),
        producer=outbox.producer,
        producer_version=outbox.producer_version,
        aggregate_type=outbox.aggregate_type,
        aggregate_id=outbox.aggregate_id,
        correlation_id=outbox.correlation_id,
        causation_id=outbox.causation_id,
        idempotency_key=outbox.idempotency_key,
        payload=outbox.payload,
    )


def _prepare(factory: sessionmaker[Session], engine: EvidenceEngine):
    story, claim = _seed(factory)
    trigger = _claims_event(story, claim)
    with factory() as session, session.begin():
        request_result = engine.request_research(session, trigger)
    with factory() as session:
        outbox = session.scalar(
            select(EventOutbox).where(EventOutbox.event_id == request_result.event_id)
        )
        assert outbox is not None
        requested = _requested_event(outbox)
        task = engine.load_collection_task(session, requested)
    return story, claim, trigger, requested, task, request_result


def test_research_request_persists_plan_and_requested_event_without_verifying_claim() -> None:
    factory = _factory()
    engine = _engine()
    story, claim, trigger, requested, task, result = _prepare(factory, engine)

    with factory() as session:
        job = session.get(Job, result.research_run_id)
        stored_claim = session.get(Claim, claim.id)

    assert job is not None and job.status == "PENDING"
    assert task.plan.story_id == story.id
    assert all(query.language == "en" for query in task.plan.queries)
    assert task.plan.research_scope["primary_sources"] is True
    assert task.plan.research_scope["contradiction_search"] is True
    assert requested.causation_id == trigger.event_id
    assert requested.correlation_id == trigger.correlation_id
    assert stored_claim is not None
    assert stored_claim.status is ClaimVerificationStatus.UNASSESSED


def test_collection_persists_explicit_relations_and_contradictions() -> None:
    factory = _factory()
    engine = _engine()
    _, claim, _, requested, task, result = _prepare(factory, engine)
    collection = asyncio.run(engine.collect(task))

    with factory() as session, session.begin():
        persisted = engine.persist_collection(session, requested, task, collection)

    with factory() as session:
        evidence = list(session.scalars(select(EvidenceItem)))
        links = list(session.scalars(select(ClaimEvidence)))
        job = session.get(Job, result.research_run_id)
        stored_claim = session.get(Claim, claim.id)
        collected_event = session.scalar(
            select(EventOutbox).where(EventOutbox.event_id == persisted.event_id)
        )

    assert evidence
    assert len(links) == len(evidence)
    assert any(link.relation == EvidenceRelation.CONTRADICTS.value for link in links)
    assert all(item.evidence_metadata["lineage_status"] == "UNASSESSED" for item in evidence)
    assert all(item.evidence_metadata["search_provenance"] for item in evidence)
    assert job is not None and job.status == "COMPLETED"
    assert stored_claim is not None
    assert stored_claim.status is ClaimVerificationStatus.UNASSESSED
    assert collected_event is not None
    assert collected_event.payload["research_run_id"] == str(result.research_run_id)
    assert collected_event.payload["claim_ids"] == [str(claim.id)]


def test_query_intent_alone_never_promotes_candidate_to_evidence() -> None:
    factory = _factory()
    engine = _engine(assessor=NoopAssessor())
    _, claim, _, requested, task, result = _prepare(factory, engine)
    collection = asyncio.run(engine.collect(task))

    assert any(item.target_role is ResearchTargetRole.CONTRADICTION for item in collection.candidates)
    assert collection.assessments == ()

    with factory() as session, session.begin():
        engine.persist_collection(session, requested, task, collection)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EvidenceItem)) == 0
        assert session.scalar(select(func.count()).select_from(ClaimEvidence)) == 0
        job = session.get(Job, result.research_run_id)
        stored_claim = session.get(Claim, claim.id)
        assert job is not None
        assert job.result["unassessed_candidate_count"] == len(collection.candidates)
        assert stored_claim is not None
        assert stored_claim.status is ClaimVerificationStatus.UNASSESSED


def test_partial_search_failure_is_recorded_without_discarding_other_evidence() -> None:
    factory = _factory()
    engine = _engine(fail_news=True)
    _, _, _, requested, task, result = _prepare(factory, engine)
    collection = asyncio.run(engine.collect(task))

    assert len(collection.failures) == 1
    assert collection.failures[0].query_family is SearchQueryFamily.ENTITY_EVENT
    assert collection.candidates

    with factory() as session, session.begin():
        persisted = engine.persist_collection(session, requested, task, collection)

    with factory() as session:
        job = session.get(Job, result.research_run_id)
    assert persisted.query_failure_count == 1
    assert job is not None
    assert len(job.result["query_failures"]) == 1
