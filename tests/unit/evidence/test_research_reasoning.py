from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import news_ai_evidence.engine as engine_module
import pytest
from news_ai_ai import (
    REASONING_ROUTING_POLICY_VERSION,
    AIReasoningEffort,
    AIReasoningReason,
)
from news_ai_database import Base, Claim, EventOutbox, Job, Story
from news_ai_domain import (
    CLAIM_SEMANTICS_POLICY_VERSION,
    ClaimSemanticState,
    ClaimSemanticType,
    ClaimVerificationStatus,
    RiskLevel,
)
from news_ai_events import EventEnvelope, EventType
from news_ai_evidence import (
    CandidateSourceType,
    EvidenceEngine,
    ResearchCandidate,
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
class SingleResultSearchProvider:
    provider_id: str = "search-a"

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
        return SearchResponse(
            request_id=request.request_id,
            provider_id=self.provider_id,
            retrieved_at=datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
            results=(
                SearchResult(
                    url=f"https://example.com/{request.query_family.value.casefold()}",
                    title="Candidate",
                    snippet="Candidate source text.",
                    candidate_type=CandidateSourceType.NEWS_ARTICLE,
                    rank=1,
                    language="en",
                ),
            ),
        )


@dataclass
class RecordingAssessor:
    candidates: list[ResearchCandidate] = field(default_factory=list)

    async def assess(self, candidate: ResearchCandidate, claim_text: str):
        self.candidates.append(candidate)
        return None


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _policy() -> SearchPolicy:
    return SearchPolicy(
        schema_version=1,
        rules=SearchRules(),
        budgets=SearchBudgets(
            default_timeout_seconds=90,
            breaking_news_timeout_seconds=45,
        ),
    )


def _engine(assessor: RecordingAssessor | None = None) -> EvidenceEngine:
    provider = SingleResultSearchProvider()
    return EvidenceEngine(
        SearchProviderRegistry([provider]),
        _policy(),
        lambda _request, compatible: compatible[0],
        assessor or RecordingAssessor(),
    )


def _seed(
    factory: sessionmaker[Session],
    *,
    story_risk: RiskLevel = RiskLevel.LOW,
    claim_risk: RiskLevel = RiskLevel.LOW,
    semantic_type: ClaimSemanticType | None = None,
) -> tuple[Story, Claim]:
    with factory() as session, session.begin():
        story = Story(
            canonical_headline="Policy announcement",
            status="DISCOVERED",
            language="en",
            risk_level=story_risk,
            story_metadata={},
        )
        session.add(story)
        session.flush()
        claim = Claim(
            story_id=story.id,
            claim_text="The government announced a new policy.",
            normalized_claim="the government announced a new policy",
            semantic_type=semantic_type,
            semantic_state=(ClaimSemanticState.OBSERVED if semantic_type is not None else None),
            semantic_policy_version=(
                CLAIM_SEMANTICS_POLICY_VERSION if semantic_type is not None else None
            ),
            semantic_ai_run_id=(uuid4() if semantic_type is not None else None),
            status=ClaimVerificationStatus.UNASSESSED,
            risk_level=claim_risk,
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


def _requested_event(row: EventOutbox) -> EventEnvelope:
    occurred_at = row.occurred_at
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    return EventEnvelope(
        event_id=row.event_id,
        event_type=EventType(row.event_type),
        schema_version=row.schema_version,
        occurred_at=occurred_at,
        producer=row.producer,
        producer_version=row.producer_version,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        idempotency_key=row.idempotency_key,
        payload=row.payload,
    )


@pytest.mark.parametrize(
    ("story_risk", "claim_risk", "semantic_type", "expected_reason"),
    (
        (RiskLevel.HIGH, RiskLevel.LOW, None, AIReasoningReason.HIGH_RISK),
        (RiskLevel.LOW, RiskLevel.CRITICAL, None, AIReasoningReason.HIGH_RISK),
        (
            RiskLevel.LOW,
            RiskLevel.LOW,
            ClaimSemanticType.ATTRIBUTION,
            AIReasoningReason.ATTRIBUTION_OR_INTENT,
        ),
        (
            RiskLevel.LOW,
            RiskLevel.LOW,
            ClaimSemanticType.POLICY_COMMITMENT,
            AIReasoningReason.ATTRIBUTION_OR_INTENT,
        ),
        (
            RiskLevel.LOW,
            RiskLevel.LOW,
            ClaimSemanticType.CAUSAL,
            AIReasoningReason.CAUSAL_REASONING,
        ),
    ),
)
def test_research_reasoning_uses_durable_pre_research_signals(
    story_risk: RiskLevel,
    claim_risk: RiskLevel,
    semantic_type: ClaimSemanticType | None,
    expected_reason: AIReasoningReason,
) -> None:
    story = Story(
        canonical_headline="Headline",
        status="DISCOVERED",
        risk_level=story_risk,
        story_metadata={},
    )
    claim = Claim(
        story_id=uuid4(),
        claim_text="Claim",
        status=ClaimVerificationStatus.UNASSESSED,
        risk_level=claim_risk,
        semantic_type=semantic_type,
        claim_metadata={},
    )

    decision = engine_module._research_reasoning_decision(story, claim)

    assert decision.effort is AIReasoningEffort.HIGH
    assert expected_reason in decision.reasons


def test_routine_research_stays_medium() -> None:
    story = Story(
        canonical_headline="Headline",
        status="DISCOVERED",
        risk_level=RiskLevel.LOW,
        story_metadata={},
    )
    claim = Claim(
        story_id=uuid4(),
        claim_text="Claim",
        status=ClaimVerificationStatus.UNASSESSED,
        risk_level=RiskLevel.LOW,
        claim_metadata={},
    )

    decision = engine_module._research_reasoning_decision(story, claim)

    assert decision.effort is AIReasoningEffort.MEDIUM
    assert decision.reasons == ()


def test_research_job_persists_and_applies_exact_claim_reasoning() -> None:
    factory = _factory()
    assessor = RecordingAssessor()
    engine = _engine(assessor)
    story, claim = _seed(
        factory,
        story_risk=RiskLevel.HIGH,
        semantic_type=ClaimSemanticType.ATTRIBUTION,
    )
    trigger = _claims_event(story, claim)

    with factory() as session, session.begin():
        requested = engine.request_research(session, trigger)
    with factory() as session:
        job = session.get(Job, requested.research_run_id)
        row = session.scalar(select(EventOutbox).where(EventOutbox.event_id == requested.event_id))
        assert job is not None and row is not None
        stored = job.payload["reasoning_by_claim"][str(claim.id)]
        task = engine.load_collection_task(session, _requested_event(row))

    assert stored == {
        "policy_version": REASONING_ROUTING_POLICY_VERSION,
        "effort": "high",
        "reasons": ["HIGH_RISK", "ATTRIBUTION_OR_INTENT"],
    }
    decision = task.reasoning_by_claim[claim.id]
    assert decision.effort is AIReasoningEffort.HIGH
    assert decision.reasons == (
        AIReasoningReason.HIGH_RISK,
        AIReasoningReason.ATTRIBUTION_OR_INTENT,
    )

    asyncio.run(engine.collect(task))

    assert assessor.candidates
    assert all(candidate.reasoning == decision for candidate in assessor.candidates)


def test_claim_risk_change_invalidates_research_semantic_operation() -> None:
    factory = _factory()
    engine = _engine()
    story, claim = _seed(factory)
    trigger = _claims_event(story, claim)

    with factory() as session, session.begin():
        first = engine.request_research(session, trigger)
    with factory() as session, session.begin():
        stored = session.get(Claim, claim.id)
        assert stored is not None
        stored.risk_level = RiskLevel.HIGH

    replay_data = trigger.model_dump()
    replay_data["event_id"] = uuid4()
    replay_data["idempotency_key"] = f"claims.extracted:{uuid4()}"
    replay = EventEnvelope.model_validate(replay_data)
    with factory() as session, session.begin():
        second = engine.request_research(session, replay)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 2
    assert second.created is True
    assert second.research_run_id != first.research_run_id
