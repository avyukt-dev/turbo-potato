from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from news_ai_common.config import ConfigLoader
from news_ai_database import (
    Base,
    Claim,
    EventOutbox,
    Job,
    ProcessedEvent,
    ResearchRunClaim,
    Story,
)
from news_ai_domain import ClaimVerificationStatus, RiskLevel
from news_ai_editorial import EditorialConfigLoader
from news_ai_events import EventEnvelope, EventType, StreamMessage
from news_ai_events.outbox import envelope_from_outbox
from news_ai_evidence import FactCheckEngine, FactCheckPolicyLoader
from news_ai_research_worker import (
    FACT_CHECK_CONSUMER_GROUP,
    STORY_VERIFICATION_CONSUMER_GROUP,
    FactCheckWorker,
    StoryVerificationWorker,
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


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _engine() -> FactCheckEngine:
    loader = ConfigLoader(Path("config"))
    return FactCheckEngine(
        FactCheckPolicyLoader(loader).load(),
        EditorialConfigLoader(loader).load().risk_policy,
    )


def _evidence_event(factory: sessionmaker[Session]) -> EventEnvelope:
    research_run_id = uuid4()
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
            research_generation=1,
            current_research_run_id=research_run_id,
        )
        session.add(claim)
        session.add(
            Job(
                id=research_run_id,
                job_type="RESEARCH",
                status="COMPLETED",
                priority=2,
                payload={},
                result={},
            )
        )
        session.flush()
        session.add(
            ResearchRunClaim(
                research_run_id=research_run_id,
                claim_id=claim.id,
                research_generation=1,
            )
        )
    return EventEnvelope(
        event_type=EventType.EVIDENCE_COLLECTED,
        producer="research-worker",
        producer_version="0.1.0",
        aggregate_type="research_run",
        aggregate_id=research_run_id,
        idempotency_key=f"evidence.collected:{research_run_id}",
        payload={
            "story_id": str(story.id),
            "claim_ids": [str(claim.id)],
            "evidence_ids": [],
            "research_run_id": str(research_run_id),
        },
    )


def _message(event: EventEnvelope, suffix: str) -> StreamMessage:
    return StreamMessage(stream="news:evidence", message_id=f"1-{suffix}", event=event)


def test_fact_check_and_story_verification_workers_ack_after_commit() -> None:
    factory = _factory()
    engine = _engine()
    evidence_event = _evidence_event(factory)
    fact_consumer = FakeConsumer(
        stream="news:evidence",
        group=FACT_CHECK_CONSUMER_GROUP,
        messages=[_message(evidence_event, "0")],
    )
    fact_worker = FactCheckWorker(fact_consumer, factory, engine)

    result = asyncio.run(fact_worker.run_once())
    assert result.processed == 1
    assert fact_consumer.acked == ["1-0"]

    with factory() as session:
        completed_outbox = session.scalar(
            select(EventOutbox).where(
                EventOutbox.event_type == EventType.FACT_CHECK_COMPLETED.value
            )
        )
        assert completed_outbox is not None
        completed_event = envelope_from_outbox(completed_outbox)

    story_consumer = FakeConsumer(
        stream="news:evidence",
        group=STORY_VERIFICATION_CONSUMER_GROUP,
        messages=[_message(completed_event, "1")],
    )
    story_worker = StoryVerificationWorker(story_consumer, factory, engine)
    story_result = asyncio.run(story_worker.run_once())
    assert story_result.processed == 1
    assert story_consumer.acked == ["1-1"]

    with factory() as session:
        story = session.get(Story, completed_event.aggregate_id)
        processed_groups = set(session.scalars(select(ProcessedEvent.consumer_group)).all())
        verified = session.scalar(
            select(EventOutbox).where(EventOutbox.event_type == EventType.STORY_VERIFIED.value)
        )
        assert story is not None
        assert story.status == "VERIFIED"
        assert FACT_CHECK_CONSUMER_GROUP in processed_groups
        assert STORY_VERIFICATION_CONSUMER_GROUP in processed_groups
        assert verified is not None


def test_fact_check_permanent_failure_is_dead_lettered_and_acked() -> None:
    factory = _factory()
    bad_event = EventEnvelope(
        event_type=EventType.EVIDENCE_COLLECTED,
        producer="research-worker",
        producer_version="0.1.0",
        aggregate_type="research_run",
        aggregate_id=uuid4(),
        idempotency_key=f"evidence.collected:{uuid4()}",
        payload={
            "story_id": str(uuid4()),
            "claim_ids": [str(uuid4())],
            "evidence_ids": [],
            "research_run_id": str(uuid4()),
        },
    )
    consumer = FakeConsumer(
        stream="news:evidence",
        group=FACT_CHECK_CONSUMER_GROUP,
        messages=[_message(bad_event, "2")],
    )
    worker = FactCheckWorker(consumer, factory, _engine())

    result = asyncio.run(worker.run_once())
    assert result.failed == 1
    assert result.dead_lettered == 1
    assert result.failed_message_ids == ("1-2",)
    assert consumer.acked == ["1-2"]
