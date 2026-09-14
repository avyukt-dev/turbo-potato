from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from news_ai_database import Base, Claim, EventOutbox, EvidenceItem, Job, Story
from news_ai_domain import ClaimVerificationStatus, RiskLevel
from news_ai_events import EventEnvelope, EventType
from news_ai_evidence import (
    EvidenceEngine,
    ResearchCollection,
    SearchBudgets,
    SearchPolicy,
    SearchProviderRegistry,
    SearchRules,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


class NoopAssessor:
    async def assess(self, candidate, claim_text):
        return None


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _engine() -> EvidenceEngine:
    policy = SearchPolicy(
        schema_version=1,
        rules=SearchRules(),
        budgets=SearchBudgets(
            default_timeout_seconds=90,
            breaking_news_timeout_seconds=45,
        ),
    )
    return EvidenceEngine(
        SearchProviderRegistry(),
        policy,
        lambda _request, compatible: compatible[0],
        NoopAssessor(),
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


def _requested_event(row: EventOutbox) -> EventEnvelope:
    return EventEnvelope(
        event_id=row.event_id,
        event_type=EventType(row.event_type),
        schema_version=row.schema_version,
        occurred_at=_aware(row.occurred_at),
        producer=row.producer,
        producer_version=row.producer_version,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        idempotency_key=row.idempotency_key,
        payload=row.payload,
    )


def test_reasoning_policy_version_invalidates_research_semantic_operation(monkeypatch) -> None:
    import news_ai_evidence.engine as engine_module

    factory = _factory()
    engine = _engine()
    story, claim = _seed(factory)
    trigger = _claims_event(story, claim)

    with factory() as session, session.begin():
        first = engine.request_research(session, trigger)

    monkeypatch.setattr(
        engine_module,
        "REASONING_ROUTING_POLICY_VERSION",
        "reasoning-routing-policy-v2",
    )
    with factory() as session, session.begin():
        second = engine.request_research(session, trigger)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 2
        stored = session.get(Job, second.research_run_id)
        assert stored is not None
        assert stored.payload["reasoning_policy_version"] == "reasoning-routing-policy-v2"
    assert second.created is True
    assert second.research_run_id != first.research_run_id


def test_in_flight_old_reasoning_policy_requires_replanning(monkeypatch) -> None:
    import news_ai_evidence.engine as engine_module

    factory = _factory()
    engine = _engine()
    story, claim = _seed(factory)
    trigger = _claims_event(story, claim)
    with factory() as session, session.begin():
        request = engine.request_research(session, trigger)
    with factory() as session:
        row = session.scalar(select(EventOutbox).where(EventOutbox.event_id == request.event_id))
        assert row is not None
        requested = _requested_event(row)
        task = engine.load_collection_task(session, requested)

    collection = ResearchCollection(research_run_id=task.plan.research_run_id)
    monkeypatch.setattr(
        engine_module,
        "REASONING_ROUTING_POLICY_VERSION",
        "reasoning-routing-policy-v2",
    )

    with factory() as session, pytest.raises(ValueError, match="reasoning policy"):
        engine.load_collection_task(session, requested)
    with pytest.raises(ValueError, match="reasoning policy"), factory() as session, session.begin():
        engine.persist_collection(session, requested, task, collection)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(EvidenceItem)) == 0
