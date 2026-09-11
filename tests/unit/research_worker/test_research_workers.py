from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from news_ai_database import Base, Claim, EventOutbox, Job, JobAttempt, Story
from news_ai_domain import ClaimVerificationStatus, RiskLevel
from news_ai_events import EventEnvelope, EventType, StreamMessage, WorkerRetryPolicy
from news_ai_events.outbox import envelope_from_outbox
from news_ai_evidence import (
    EvidenceAssessment,
    EvidenceEngine,
    EvidenceRelation,
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
from news_ai_research_worker import (
    EVIDENCE_COLLECTION_CONSUMER_GROUP,
    RESEARCH_PLANNING_CONSUMER_GROUP,
    EvidenceCollectionWorker,
    ResearchPlanningWorker,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


@dataclass
class FakeConsumer:
    stream: str
    group: str
    messages: list[StreamMessage]
    acked: list[str] = field(default_factory=list)

    async def ensure_group(self) -> None: ...

    async def read(self) -> list[StreamMessage]:
        result, self.messages = self.messages, []
        return result

    async def claim_stale(self, *, min_idle_ms: int, start_id: str = "0-0"):
        return "0-0", []

    async def ack(self, message: StreamMessage) -> None:
        self.acked.append(message.message_id)


@dataclass
class Provider:
    provider_id: str = "search-a"

    @property
    def capabilities(self) -> SearchProviderCapabilities:
        return SearchProviderCapabilities(
            provider_id=self.provider_id,
            capabilities=frozenset({SearchCapability.WEB, SearchCapability.NEWS}),
            query_families=frozenset(SearchQueryFamily),
            max_results=10,
        )

    async def search(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(
            request_id=request.request_id,
            provider_id=self.provider_id,
            retrieved_at=datetime.now(UTC),
            results=(SearchResult(url=f"https://example.com/{request.request_id}", rank=1),),
        )


class Assessor:
    async def assess(self, candidate: ResearchCandidate, claim_text: str):
        return EvidenceAssessment(
            claim_id=candidate.claim_id,
            candidate_url=candidate.result.url,
            relation=EvidenceRelation.CONTEXT,
            strength_score=Decimal("0.25"),
        )


class FailingAssessor:
    async def assess(self, candidate: ResearchCandidate, claim_text: str):
        raise RuntimeError("temporary assessor outage")


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _engine() -> EvidenceEngine:
    policy = SearchPolicy(
        schema_version=1,
        rules=SearchRules(),
        budgets=SearchBudgets(default_timeout_seconds=90, breaking_news_timeout_seconds=45),
    )
    return EvidenceEngine(
        SearchProviderRegistry([Provider()]),
        policy,
        lambda _request, compatible: compatible[0],
        Assessor(),
    )


def _seed_claim_event(factory: sessionmaker[Session]) -> EventEnvelope:
    with factory() as session, session.begin():
        story = Story(
            canonical_headline="Example",
            status="DISCOVERED",
            risk_level=RiskLevel.LOW,
            story_metadata={},
        )
        session.add(story)
        session.flush()
        claim = Claim(
            story_id=story.id,
            claim_text="Example claim",
            status=ClaimVerificationStatus.UNASSESSED,
            risk_level=RiskLevel.LOW,
            claim_metadata={},
        )
        session.add(claim)
        session.flush()
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


def _stream_message(stream: str, event: EventEnvelope, suffix: str) -> StreamMessage:
    return StreamMessage(stream=stream, message_id=f"1-{suffix}", event=event)


def test_planning_then_collection_workers_ack_only_after_durable_processing() -> None:
    factory = _factory()
    engine = _engine()
    claims_event = _seed_claim_event(factory)
    planning_consumer = FakeConsumer(
        stream="news:stories",
        group=RESEARCH_PLANNING_CONSUMER_GROUP,
        messages=[_stream_message("news:stories", claims_event, "0")],
    )
    planning = ResearchPlanningWorker(planning_consumer, factory, engine)

    planning_result = asyncio.run(planning.run_once())
    assert planning_result.processed == 1
    assert planning_consumer.acked == ["1-0"]

    with factory() as session:
        requested_outbox = session.scalar(
            select(EventOutbox).where(EventOutbox.event_type == EventType.EVIDENCE_REQUESTED.value)
        )
        assert requested_outbox is not None
        requested_event = envelope_from_outbox(requested_outbox)

    collection_consumer = FakeConsumer(
        stream="news:evidence",
        group=EVIDENCE_COLLECTION_CONSUMER_GROUP,
        messages=[_stream_message("news:evidence", requested_event, "1")],
    )
    collector = EvidenceCollectionWorker(collection_consumer, factory, engine)
    collection_result = asyncio.run(collector.run_once())

    assert collection_result.processed == 1
    assert collection_consumer.acked == ["1-1"]
    with factory() as session:
        collected = session.scalar(
            select(EventOutbox).where(EventOutbox.event_type == EventType.EVIDENCE_COLLECTED.value)
        )
        assert collected is not None
        job = session.get(Job, requested_event.aggregate_id)
        assert job is not None
        attempt = session.scalar(select(JobAttempt).where(JobAttempt.job_id == job.id))
        assert job.attempts == 1
        assert attempt is not None and attempt.status == "COMPLETED"


def test_collection_permanent_failure_is_dead_lettered_and_acked() -> None:
    factory = _factory()
    engine = _engine()
    bad_event = EventEnvelope(
        event_type=EventType.EVIDENCE_REQUESTED,
        producer="research-worker",
        producer_version="0.1.0",
        aggregate_type="research_run",
        aggregate_id=uuid4(),
        idempotency_key=f"evidence.requested:{uuid4()}",
        payload={
            "story_id": str(uuid4()),
            "claim_ids": [str(uuid4())],
            "research_scope": {
                "primary_sources": True,
                "independent_corroboration": True,
                "contradiction_search": True,
            },
        },
    )
    consumer = FakeConsumer(
        stream="news:evidence",
        group=EVIDENCE_COLLECTION_CONSUMER_GROUP,
        messages=[_stream_message("news:evidence", bad_event, "2")],
    )
    worker = EvidenceCollectionWorker(consumer, factory, engine)

    result = asyncio.run(worker.run_once())
    assert result.failed == 1
    assert result.dead_lettered == 1
    assert result.failed_message_ids == ("1-2",)
    assert consumer.acked == ["1-2"]


def test_exhausted_research_attempt_updates_job_attempt_and_dead_letters() -> None:
    factory = _factory()
    engine = _engine()
    engine.assessor = FailingAssessor()
    claims_event = _seed_claim_event(factory)
    with factory() as session, session.begin():
        planned = engine.request_research(session, claims_event)
    with factory() as session:
        outbox = session.scalar(select(EventOutbox).where(EventOutbox.event_id == planned.event_id))
        assert outbox is not None
        requested = envelope_from_outbox(outbox)

    consumer = FakeConsumer(
        stream="news:evidence",
        group=EVIDENCE_COLLECTION_CONSUMER_GROUP,
        messages=[_stream_message("news:evidence", requested, "3")],
    )
    worker = EvidenceCollectionWorker(
        consumer,
        factory,
        engine,
        retry_policy=WorkerRetryPolicy(delays_seconds=(1,), jitter_ratio=0),
    )
    result = asyncio.run(worker.run_once())

    assert result.dead_lettered == 1
    assert consumer.acked == ["1-3"]
    with factory() as session:
        job = session.get(Job, planned.research_run_id)
        attempt = session.scalar(
            select(JobAttempt).where(JobAttempt.job_id == planned.research_run_id)
        )
        assert job is not None and job.status == "FAILED" and job.attempts == 1
        assert attempt is not None and attempt.status == "FAILED"
