from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from news_ai_ai import (
    AIFailureReason,
    AIRouteAttempt,
    AIRouteAttemptOutcome,
    AIRoutingExecutionError,
)
from news_ai_common.config import ConfigLoader
from news_ai_database import (
    AIRun,
    Article,
    ArticleVersion,
    Base,
    Claim,
    ClaimEvidence,
    EventOutbox,
    EvidenceGraphRelation,
    EvidenceItem,
    Job,
    ResearchRunClaim,
    Source,
    Story,
)
from news_ai_domain import ClaimVerificationStatus, RiskLevel
from news_ai_events import EventEnvelope, EventType
from news_ai_evidence import (
    RESEARCH_PLANNER_METHODOLOGY_VERSION,
    CandidateSourceType,
    EvidenceAssessment,
    EvidenceAssessmentAIProvenance,
    EvidenceEngine,
    EvidenceRelation,
    ResearchCandidate,
    ResearchCollection,
    ResearchPolicyLoader,
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
    SourceEvidenceResolver,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


@dataclass
class FakeSearchProvider:
    provider_id: str = "search-a"
    fail_news: bool = False
    calls: list[SearchRequest] = field(default_factory=list)

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
        self.calls.append(request)
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


@dataclass
class ExplicitAssessor:
    calls: list[ResearchCandidate] = field(default_factory=list)

    async def assess(
        self,
        candidate: ResearchCandidate,
        claim_text: str,
    ) -> EvidenceAssessment | None:
        self.calls.append(candidate)
        relation = (
            EvidenceRelation.CONTRADICTS
            if candidate.target_role is ResearchTargetRole.CONTRADICTION
            else EvidenceRelation.DIRECT_SUPPORT
        )
        return EvidenceAssessment(
            claim_id=candidate.claim_id,
            candidate_url=candidate.result.url,
            relation=relation,
            directness=("DIRECT" if relation is EvidenceRelation.DIRECT_SUPPORT else "UNKNOWN"),
            strength_score=Decimal("0.60"),
            notes=f"Explicit test assessment for: {claim_text}",
        )


class NoopAssessor:
    async def assess(
        self,
        candidate: ResearchCandidate,
        claim_text: str,
    ) -> EvidenceAssessment | None:
        return None


@dataclass
class PartiallyRateLimitedAssessor(ExplicitAssessor):
    fail_after: int = 1

    async def assess(
        self,
        candidate: ResearchCandidate,
        claim_text: str,
    ) -> EvidenceAssessment | None:
        if len(self.calls) >= self.fail_after:
            raise AIRoutingExecutionError(
                (
                    AIRouteAttempt(
                        provider_id="assessment-ai",
                        model="assessment-model",
                        outcome=AIRouteAttemptOutcome.FAILED,
                        failure_reason=AIFailureReason.RATE_LIMIT,
                    ),
                )
            )
        return await super().assess(candidate, claim_text)


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


def _engine(
    *,
    assessor=None,
    fail_news: bool = False,
    methodology_version: str = RESEARCH_PLANNER_METHODOLOGY_VERSION,
    policy: SearchPolicy | None = None,
) -> EvidenceEngine:
    provider = FakeSearchProvider(fail_news=fail_news)
    return EvidenceEngine(
        SearchProviderRegistry([provider]),
        policy or _policy(),
        lambda _request, compatible: compatible[0],
        assessor or ExplicitAssessor(),
        methodology_version=methodology_version,
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
        payload={
            "story_id": str(story.id),
            "claim_ids": [str(claim.id)],
            "ai_run_id": str(uuid4()),
            "model_id": str(uuid4()),
        },
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
    assert stored_claim.research_generation == 1
    assert stored_claim.current_research_run_id == result.research_run_id
    assert result.claim_generations == {claim.id: 1}


def test_research_request_reuses_semantic_operation_for_new_event_id() -> None:
    factory = _factory()
    engine = _engine()
    _, _, trigger, _, _, first = _prepare(factory, engine)
    replay_data = trigger.model_dump()
    replay_data["event_id"] = uuid4()
    replay_data["idempotency_key"] = f"claims.extracted:{uuid4()}"
    replay = EventEnvelope.model_validate(replay_data)

    with factory() as session, session.begin():
        second = engine.request_research(session, replay)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(func.count()).select_from(EventOutbox)) == 1
        stored_claim = session.get(Claim, second.claim_ids[0])
        assert stored_claim is not None and stored_claim.research_generation == 1
    assert second.created is False
    assert second.research_run_id == first.research_run_id
    assert second.event_id == first.event_id


def test_research_methodology_version_invalidates_semantic_operation() -> None:
    factory = _factory()
    _, _, trigger, _, _, first = _prepare(factory, _engine())

    with factory() as session, session.begin():
        second = _engine(methodology_version="research-planner-v2").request_research(
            session, trigger
        )

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 2
        stored = session.get(Job, second.research_run_id)
        stored_claim = session.get(Claim, second.claim_ids[0])
        assert stored is not None
        assert stored.payload["methodology_version"] == "research-planner-v2"
        assert stored_claim is not None
        assert stored_claim.research_generation == 2
        assert stored_claim.current_research_run_id == second.research_run_id
    assert second.created is True
    assert second.research_run_id != first.research_run_id


def test_evidence_graph_policy_version_invalidates_semantic_operation(monkeypatch) -> None:
    import news_ai_evidence.engine as engine_module

    factory = _factory()
    engine = _engine()
    _, _, trigger, _, _, first = _prepare(factory, engine)
    monkeypatch.setattr(engine_module, "EVIDENCE_GRAPH_POLICY_VERSION", "evidence-graph-policy-v2")
    with factory() as session, session.begin():
        second = engine.request_research(session, trigger)
    assert second.created and second.research_run_id != first.research_run_id


def test_evidence_assessment_methodology_invalidates_semantic_operation(monkeypatch) -> None:
    import news_ai_evidence.engine as engine_module

    factory = _factory()
    engine = _engine()
    _, _, trigger, _, _, first = _prepare(factory, engine)
    monkeypatch.setattr(
        engine_module,
        "EVIDENCE_ASSESSMENT_METHODOLOGY_VERSION",
        "evidence-assessment-methodology-v2",
    )
    with factory() as session, session.begin():
        second = engine.request_research(session, trigger)

    with factory() as session:
        stored = session.get(Job, second.research_run_id)
        assert stored is not None
        assert (
            stored.payload["evidence_assessment_methodology_version"]
            == "evidence-assessment-methodology-v2"
        )
    assert second.created and second.research_run_id != first.research_run_id


def test_in_flight_old_assessment_methodology_requires_replanning(monkeypatch) -> None:
    import news_ai_evidence.engine as engine_module

    factory = _factory()
    engine = _engine()
    _, _, _, requested, task, _ = _prepare(factory, engine)
    collection = asyncio.run(engine.collect(task))
    monkeypatch.setattr(
        engine_module,
        "EVIDENCE_ASSESSMENT_METHODOLOGY_VERSION",
        "evidence-assessment-methodology-v2",
    )

    with factory() as session, pytest.raises(ValueError, match="assessment methodology"):
        engine.load_collection_task(session, requested)
    with (
        pytest.raises(ValueError, match="assessment methodology"),
        factory() as session,
        session.begin(),
    ):
        engine.persist_collection(session, requested, task, collection)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EvidenceItem)) == 0


def test_in_flight_old_graph_policy_requires_replanning(monkeypatch) -> None:
    import news_ai_evidence.engine as engine_module

    factory = _factory()
    engine = _engine()
    _, _, _, requested, task, _ = _prepare(factory, engine)
    collection = asyncio.run(engine.collect(task))
    monkeypatch.setattr(engine_module, "EVIDENCE_GRAPH_POLICY_VERSION", "evidence-graph-policy-v2")
    with factory() as session, pytest.raises(ValueError, match="replanning"):
        engine.load_collection_task(session, requested)
    with pytest.raises(ValueError, match="replanning"), factory() as session, session.begin():
        engine.persist_collection(session, requested, task, collection)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EvidenceItem)) == 0


def test_graph_target_from_another_or_stale_collection_is_rejected() -> None:
    from news_ai_evidence import EvidenceGraphRelationSpec
    from news_ai_evidence.source_policy import (
        CandidateSourceResolution,
        LineageResolution,
        SourcePolicyResolution,
    )

    class InvalidTargetResolver:
        def resolve(self, session, candidates):
            return {
                (candidate.claim_id, candidate.result.url): CandidateSourceResolution(
                    source=None,
                    article=None,
                    version=None,
                    authority=SourcePolicyResolution(effective_level=4, basis="unknown"),
                    lineage=LineageResolution(status="UNRESOLVED", basis="unresolved"),
                    graph_relations=(
                        EvidenceGraphRelationSpec(
                            relation_type="REFERENCES",
                            target_evidence_id=uuid4(),
                            basis="explicit-reference",
                        ),
                    ),
                )
                for candidate in candidates
            }

    factory = _factory()
    engine = _engine()
    engine.source_resolver = InvalidTargetResolver()
    _, _, _, requested, task, _ = _prepare(factory, engine)
    collection = asyncio.run(engine.collect(task))
    with (
        pytest.raises(ValueError, match="outside current claim"),
        factory() as session,
        session.begin(),
    ):
        engine.persist_collection(session, requested, task, collection)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EvidenceItem)) == 0


def test_research_generations_advance_per_claim_in_multi_claim_story() -> None:
    factory = _factory()
    engine = _engine()
    story, first_claim = _seed(factory)
    with factory() as session, session.begin():
        second_claim = Claim(
            story_id=story.id,
            claim_text="A second independent claim.",
            status=ClaimVerificationStatus.UNASSESSED,
            risk_level=RiskLevel.LOW,
            claim_metadata={},
        )
        session.add(second_claim)
        session.flush()
        second_id = second_claim.id

    both = _claims_event(story, first_claim)
    both.payload["claim_ids"] = [str(first_claim.id), str(second_id)]
    with factory() as session, session.begin():
        initial = engine.request_research(session, both)

    one = _claims_event(story, first_claim)
    one.payload["ai_run_id"] = str(uuid4())
    with factory() as session, session.begin():
        newer = engine.request_research(session, one)

    with factory() as session:
        stored_first = session.get(Claim, first_claim.id)
        stored_second = session.get(Claim, second_id)
        mappings = list(session.scalars(select(ResearchRunClaim)))
        assert stored_first is not None and stored_first.research_generation == 2
        assert stored_first.current_research_run_id == newer.research_run_id
        assert stored_second is not None and stored_second.research_generation == 1
        assert stored_second.current_research_run_id == initial.research_run_id
        assert len(mappings) == 3


def test_multi_claim_collection_persists_only_claims_still_current_for_that_run() -> None:
    factory = _factory()
    engine = _engine()
    story, first_claim = _seed(factory)
    with factory() as session, session.begin():
        second_claim = Claim(
            story_id=story.id,
            claim_text="A second independent claim.",
            status=ClaimVerificationStatus.UNASSESSED,
            risk_level=RiskLevel.LOW,
            claim_metadata={},
        )
        session.add(second_claim)
        session.flush()
        second_id = second_claim.id

    both = _claims_event(story, first_claim)
    both.payload["claim_ids"] = [str(first_claim.id), str(second_id)]
    with factory() as session, session.begin():
        initial = engine.request_research(session, both)
    with factory() as session:
        row = session.scalar(select(EventOutbox).where(EventOutbox.event_id == initial.event_id))
        assert row is not None
        requested = _requested_event(row)
        task = engine.load_collection_task(session, requested)
    collection = asyncio.run(engine.collect(task))

    one = _claims_event(story, first_claim)
    one.payload["ai_run_id"] = str(uuid4())
    with factory() as session, session.begin():
        newer = engine.request_research(session, one)
    with factory() as session, session.begin():
        persisted = engine.persist_collection(session, requested, task, collection)

    assert persisted.current_claim_ids == (second_id,)
    assert persisted.stale_claim_ids == (first_claim.id,)
    with factory() as session:
        collected = session.scalar(
            select(EventOutbox).where(EventOutbox.event_id == persisted.event_id)
        )
        first = session.get(Claim, first_claim.id)
        second = session.get(Claim, second_id)
        assert collected is not None
        assert collected.payload["claim_ids"] == [str(second_id)]
        assert first is not None and first.current_research_run_id == newer.research_run_id
        assert second is not None
        assert second.current_research_run_id == initial.research_run_id


def test_partial_stale_collection_skips_queries_and_assessment_for_stale_claim() -> None:
    factory = _factory()
    provider = FakeSearchProvider()
    assessor = ExplicitAssessor()
    engine = EvidenceEngine(
        SearchProviderRegistry([provider]),
        _policy(),
        lambda _request, compatible: compatible[0],
        assessor,
    )
    story, first_claim = _seed(factory)
    with factory() as session, session.begin():
        second_claim = Claim(
            story_id=story.id,
            claim_text="A second current claim.",
            status=ClaimVerificationStatus.UNASSESSED,
            risk_level=RiskLevel.LOW,
            claim_metadata={},
        )
        session.add(second_claim)
        session.flush()
        second_id = second_claim.id
    both = _claims_event(story, first_claim)
    both.payload["claim_ids"] = [str(first_claim.id), str(second_id)]
    with factory() as session, session.begin():
        initial = engine.request_research(session, both)
    with factory() as session:
        row = session.scalar(select(EventOutbox).where(EventOutbox.event_id == initial.event_id))
        assert row is not None
        requested = _requested_event(row)
        task = engine.load_collection_task(session, requested)
    newer_event = _claims_event(story, first_claim)
    newer_event.payload["ai_run_id"] = str(uuid4())
    with factory() as session, session.begin():
        engine.request_research(session, newer_event)
    with factory() as session:
        currency = engine.collection_currency(session, task)

    asyncio.run(engine.collect(task, active_claim_ids=currency.current_claim_ids))

    assert currency.current_claim_ids == (second_id,)
    assert currency.stale_claim_ids == (first_claim.id,)
    assert provider.calls and {request.claim_id for request in provider.calls} == {second_id}
    assert assessor.calls and {candidate.claim_id for candidate in assessor.calls} == {second_id}


def test_research_policy_change_invalidates_semantic_operation_independently() -> None:
    factory = _factory()
    _, _, trigger, _, _, first = _prepare(factory, _engine())
    changed_policy = _policy().model_copy(
        update={"rules": _policy().rules.model_copy(update={"search_counterclaims": False})}
    )

    with factory() as session, session.begin():
        second = _engine(policy=changed_policy).request_research(session, trigger)

    assert second.created is True
    assert second.research_run_id != first.research_run_id


def test_research_request_allows_materially_changed_claim_input() -> None:
    factory = _factory()
    engine = _engine()
    _, claim, trigger, _, _, first = _prepare(factory, engine)
    with factory() as session, session.begin():
        stored = session.get(Claim, claim.id)
        assert stored is not None
        stored.claim_text = "Materially corrected claim text."
    changed_data = trigger.model_dump()
    changed_data["event_id"] = uuid4()
    changed_data["idempotency_key"] = f"claims.extracted:{uuid4()}"
    changed = EventEnvelope.model_validate(changed_data)

    with factory() as session, session.begin():
        second = engine.request_research(session, changed)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 2
    assert second.created is True
    assert second.research_run_id != first.research_run_id


def test_superseded_collection_cannot_persist_evidence_or_emit_downstream_event() -> None:
    factory = _factory()
    engine = _engine()
    _, claim, trigger, requested, task, first = _prepare(factory, engine)
    collection = asyncio.run(engine.collect(task))

    newer_data = trigger.model_dump()
    newer_data["event_id"] = uuid4()
    newer_data["idempotency_key"] = f"claims.extracted:{uuid4()}"
    newer_data["payload"] = {**trigger.payload, "ai_run_id": str(uuid4())}
    with factory() as session, session.begin():
        newer = engine.request_research(session, EventEnvelope.model_validate(newer_data))
    with factory() as session, session.begin():
        stale = engine.persist_collection(session, requested, task, collection)

    with factory() as session:
        stored_claim = session.get(Claim, claim.id)
        old_job = session.get(Job, first.research_run_id)
        assert stored_claim is not None
        assert stored_claim.current_research_run_id == newer.research_run_id
        assert stored_claim.research_generation == 2
        assert stored_claim.status is ClaimVerificationStatus.UNASSESSED
        assert old_job is not None and old_job.status == "SUPERSEDED"
        assert session.scalar(select(func.count()).select_from(EvidenceItem)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventOutbox)
                .where(EventOutbox.event_type == EventType.EVIDENCE_COLLECTED)
            )
            == 0
        )
    assert stale.event_id is None
    assert stale.current_claim_ids == ()
    assert stale.stale_claim_ids == (claim.id,)


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
    assert all(item.evidence_metadata["lineage_status"] == "UNRESOLVED" for item in evidence)
    assert all(item.evidence_metadata["independence_group"] is None for item in evidence)
    assert all(item.evidence_metadata["search_provenance"] for item in evidence)
    assert job is not None and job.status == "COMPLETED"
    assert stored_claim is not None
    assert stored_claim.status is ClaimVerificationStatus.UNASSESSED
    assert collected_event is not None
    assert collected_event.payload["research_run_id"] == str(result.research_run_id)
    assert collected_event.payload["claim_ids"] == [str(claim.id)]


def test_collection_persists_exact_source_version_lineage_and_ai_run() -> None:
    factory = _factory()
    engine = _engine()
    engine.source_resolver = SourceEvidenceResolver(
        ResearchPolicyLoader(ConfigLoader("config")).load()
    )
    _, claim, _, requested, task, _ = _prepare(factory, engine)
    collected = asyncio.run(engine.collect(task))
    candidate = collected.candidates[0]
    with factory() as session, session.begin():
        source = Source(
            name="Explicit primary record",
            domain="records.example",
            source_type="PRIMARY",
            authority_level=1,
            source_metadata={},
        )
        session.add(source)
        session.flush()
        article = Article(
            source_id=source.id,
            canonical_url="https://records.example/document",
            title="Official document",
            language="en",
        )
        session.add(article)
        session.flush()
        version = ArticleVersion(
            article_id=article.id,
            version_number=1,
            content_hash="d" * 64,
            body="The government announced a new policy.",
            retrieved_at=datetime(2026, 9, 11, tzinfo=UTC),
            version_metadata={"primary_document_id": "document:policy-1"},
        )
        session.add(version)
        session.flush()
        version_id = version.id
        source_id = source.id

    result = candidate.result.model_copy(
        update={
            "url": article.canonical_url,
            "snippet": version.body,
            "metadata": {
                "source_id": str(source.id),
                "article_id": str(article.id),
                "article_version_id": str(version.id),
                "content_hash": version.content_hash,
            },
        }
    )
    candidate = candidate.model_copy(update={"result": result})
    assessment = EvidenceAssessment(
        claim_id=claim.id,
        candidate_url=result.url,
        relation=EvidenceRelation.DIRECT_SUPPORT,
        directness="DIRECT",
        strength_score=Decimal("0.90"),
        relevant_excerpt="government announced a new policy",
        ai_provenance=EvidenceAssessmentAIProvenance(
            provider_id="assessment-ai",
            model_name="assessment-model-v1",
            locality="LOCAL",
            capabilities={"task": "EVIDENCE_ASSESSMENT"},
            prompt_id="evidence-assessment",
            prompt_version="v1",
            prompt_checksum="e" * 64,
            input_hash="f" * 64,
            input_artifact_ids=(f"article_version:{version_id}",),
            output_payload={"relation": "DIRECT_SUPPORT"},
            latency_ms=3,
            routing_attempts=({"provider_id": "assessment-ai", "outcome": "SUCCESS"},),
        ),
    )
    collection = ResearchCollection(
        research_run_id=task.plan.research_run_id,
        candidates=(candidate,),
        assessments=(assessment,),
        successful_query_count=1,
    )

    with factory() as session, session.begin():
        persisted = engine.persist_collection(session, requested, task, collection)

    with factory() as session:
        evidence = session.get(EvidenceItem, persisted.evidence_ids[0])
        ai_run = session.scalar(select(AIRun))
        assert evidence is not None
        assert evidence.source_id == source_id
        assert evidence.content_hash == "d" * 64
        assert evidence.evidence_metadata["article_version_id"] == str(version_id)
        assert evidence.evidence_metadata["lineage_status"] == "INDEPENDENT"
        assert evidence.evidence_metadata["source_level"] == 1
        assert evidence.evidence_metadata["assessment_ai_run_ids"] == [str(ai_run.id)]
        link = session.scalar(select(ClaimEvidence))
        assert link.directness == "DIRECT"
        assert link.origin_role == "ORIGINAL"
        assert link.provenance_state == "DURABLE_VERSION_PRESERVED"
        assert link.semantics_policy_version == "evidence-graph-policy-v1"
        edge = session.scalar(select(EvidenceGraphRelation))
        assert edge.relation_type == "REFERENCES"
        assert edge.external_reference == "document:policy-1"
        assert edge.research_run_id == task.plan.research_run_id
        assert edge.research_generation == 1
        assert ai_run is not None and ai_run.task_type == "EVIDENCE_ASSESSMENT"


def test_query_intent_alone_never_promotes_candidate_to_evidence() -> None:
    factory = _factory()
    engine = _engine(assessor=NoopAssessor())
    _, claim, _, requested, task, result = _prepare(factory, engine)
    collection = asyncio.run(engine.collect(task))

    assert any(
        item.target_role is ResearchTargetRole.CONTRADICTION for item in collection.candidates
    )
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


def test_partial_assessment_rate_limit_retains_useful_assessments() -> None:
    factory = _factory()
    assessor = PartiallyRateLimitedAssessor(fail_after=1)
    engine = _engine(assessor=assessor)
    _, _, _, requested, task, result = _prepare(factory, engine)

    collection = asyncio.run(engine.collect(task))

    assert collection.disposition.value == "PARTIAL"
    assert len(collection.assessments) == 1
    assert collection.failures
    assert collection.failures[0].error_code == "AI_ASSESSMENT_FAILED"
    assert {item.error_code for item in collection.failures[1:]} <= {"AI_ASSESSMENT_DEFERRED"}
    assert all(item.retryable for item in collection.failures)
    assert len(assessor.calls) == 1

    with factory() as session, session.begin():
        persisted = engine.persist_collection(session, requested, task, collection)

    with factory() as session:
        job = session.get(Job, result.research_run_id)
        assert session.scalar(select(func.count()).select_from(EvidenceItem)) == 1
    assert persisted.query_failure_count == len(collection.failures)
    assert job is not None and len(job.result["query_failures"]) == len(collection.failures)


def test_total_assessment_rate_limit_remains_retryable() -> None:
    factory = _factory()
    engine = _engine(assessor=PartiallyRateLimitedAssessor(fail_after=0))
    _, _, _, _, task, _ = _prepare(factory, engine)

    collection = asyncio.run(engine.collect(task))

    assert collection.disposition.value == "RETRYABLE_FAILURE"
    assert collection.assessments == ()
    assert collection.successful_query_count == 0
    assert collection.failures and all(item.retryable for item in collection.failures)
    assert len(engine.assessor.calls) == 0
