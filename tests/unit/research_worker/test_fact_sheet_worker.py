from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from helpers.claim_semantics import classification_run
from news_ai_database import (
    Base,
    Claim,
    FactCheck,
    FactSheet,
    Job,
    ProcessedEvent,
    ResearchRunClaim,
    Story,
)
from news_ai_domain import ClaimVerificationStatus, FactCheckLabel, ReviewState, RiskLevel
from news_ai_events import EventEnvelope, EventType, StreamMessage
from news_ai_evidence import FactSheetGenerator
from news_ai_research_worker import FACT_SHEET_CONSUMER_GROUP, FactSheetWorker
from sqlalchemy import create_engine, func, select
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


def _verified_event(factory: sessionmaker[Session]) -> EventEnvelope:
    with factory() as session, session.begin():
        research_run_id = uuid4()
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
        story = Story(
            canonical_headline="Worker Fact Sheet",
            summary="Persisted summary",
            status="VERIFIED",
            risk_level=RiskLevel.LOW,
            story_metadata={},
        )
        session.add(story)
        session.flush()
        semantic_run = classification_run(session)
        claim = Claim(
            semantic_type="EVENT",
            semantic_state="OBSERVED",
            semantic_policy_version="claim-semantics-policy-v1",
            semantic_ai_run_id=semantic_run,
            story_id=story.id,
            claim_text="A verified claim.",
            claim_type="EVENT",
            status=ClaimVerificationStatus.SUPPORTED,
            risk_level=RiskLevel.LOW,
            claim_metadata={},
            research_generation=1,
            current_research_run_id=research_run_id,
        )
        session.add(claim)
        session.flush()
        check = FactCheck(
            story_id=story.id,
            claim_id=claim.id,
            label=FactCheckLabel.TRUE,
            summary="Supported by evidence.",
            review_required=True,
            review_state=ReviewState.NOT_READY,
            research_run_id=research_run_id,
            research_generation=1,
            methodology_version="fact-check-methodology-v1",
        )
        session.add(check)
        session.flush()
        claim.current_fact_check_id = check.id
        session.add(
            ResearchRunClaim(
                research_run_id=research_run_id,
                claim_id=claim.id,
                research_generation=1,
            )
        )
        story_id = story.id
        check_id = check.id

    return EventEnvelope(
        event_type=EventType.STORY_VERIFIED,
        producer="research-worker",
        producer_version="0.1.0",
        aggregate_type="story",
        aggregate_id=story_id,
        idempotency_key=f"story.verified:{uuid4()}",
        payload={
            "story_id": str(story_id),
            "fact_check_ids": [str(check_id)],
            "confidence_score": None,
            "risk_level": "LOW",
            "review_required": True,
        },
    )


def _message(event: EventEnvelope, message_id: str) -> StreamMessage:
    return StreamMessage(stream="news:stories", message_id=message_id, event=event)


def test_fact_sheet_worker_acks_after_commit_and_replay_is_idempotent() -> None:
    factory = _factory()
    event = _verified_event(factory)
    consumer = FakeConsumer(
        stream="news:stories",
        group=FACT_SHEET_CONSUMER_GROUP,
        messages=[_message(event, "1-0")],
    )
    worker = FactSheetWorker(consumer, factory, FactSheetGenerator())

    result = asyncio.run(worker.run_once())
    assert result.processed == 1
    assert consumer.acked == ["1-0"]

    consumer.messages = [_message(event, "1-1")]
    replay = asyncio.run(worker.run_once())
    assert replay.duplicates == 1
    assert consumer.acked == ["1-0", "1-1"]

    with factory() as session:
        sheet_count = session.scalar(select(func.count()).select_from(FactSheet))
        processed = session.scalar(
            select(ProcessedEvent).where(
                ProcessedEvent.event_id == event.event_id,
                ProcessedEvent.consumer_group == FACT_SHEET_CONSUMER_GROUP,
            )
        )
        assert sheet_count == 1
        assert processed is not None


def test_fact_sheet_worker_dead_letters_invalid_event() -> None:
    factory = _factory()
    event = _verified_event(factory)
    bad_event = event.model_copy(
        update={
            "event_id": uuid4(),
            "payload": {**event.payload, "verification_stage_complete": False},
        }
    )
    consumer = FakeConsumer(
        stream="news:stories",
        group=FACT_SHEET_CONSUMER_GROUP,
        messages=[_message(bad_event, "2-0")],
    )
    worker = FactSheetWorker(consumer, factory, FactSheetGenerator())

    result = asyncio.run(worker.run_once())
    assert result.failed == 1
    assert result.dead_lettered == 1
    assert result.failed_message_ids == ("2-0",)
    assert consumer.acked == ["2-0"]

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(FactSheet)) == 0


def test_fact_sheet_worker_acks_superseded_verification_without_retry() -> None:
    factory = _factory()
    event = _verified_event(factory)
    with factory() as session, session.begin():
        claim = session.scalar(select(Claim).where(Claim.story_id == event.aggregate_id))
        assert claim is not None
        newer_run_id = uuid4()
        session.add(
            Job(
                id=newer_run_id,
                job_type="RESEARCH",
                status="PENDING",
                priority=2,
                payload={},
            )
        )
        session.add(
            ResearchRunClaim(
                research_run_id=newer_run_id,
                claim_id=claim.id,
                research_generation=2,
            )
        )
        claim.research_generation = 2
        claim.current_research_run_id = newer_run_id
        claim.current_fact_check_id = None
        claim.status = ClaimVerificationStatus.UNASSESSED

    consumer = FakeConsumer(
        stream="news:stories",
        group=FACT_SHEET_CONSUMER_GROUP,
        messages=[_message(event, "3-0")],
    )
    result = asyncio.run(FactSheetWorker(consumer, factory, FactSheetGenerator()).run_once())

    assert result.stale == 1
    assert result.retrying == 0
    assert result.dead_lettered == 0
    assert consumer.acked == ["3-0"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(FactSheet)) == 0
