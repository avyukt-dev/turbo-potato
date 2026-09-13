from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from news_ai_ai import (
    AIPolicyConfig,
    AIProviderRegistry,
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AIRouter,
    AIRoutingMode,
    AIStageConfig,
    AIStageId,
    AIStagePromptConfig,
    AIStageProviderSelection,
    AITaskType,
    ClaimExtractionPrompt,
    ClaimExtractionService,
    ProviderCapabilities,
    ProviderLocality,
)
from news_ai_ai_worker import CLAIM_CONSUMER_GROUP, ClaimExtractionWorker
from news_ai_database import (
    AIRun,
    Article,
    ArticleVersion,
    Base,
    Claim,
    EventOutbox,
    ProcessedEvent,
    Source,
    Story,
    StorySource,
)
from news_ai_domain import RiskLevel
from news_ai_events import EventEnvelope, EventType, StreamMessage
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker


@dataclass
class CountingProvider:
    calls: int = 0

    provider_id: str = "local-a"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            locality=ProviderLocality.LOCAL,
            task_types=frozenset({AITaskType.CLAIM_EXTRACTION}),
            response_formats=frozenset({AIResponseFormat.STRUCTURED}),
        )

    async def execute(self, request: AIRequest) -> AIResponse:
        self.calls += 1
        return AIResponse(
            structured={
                "claims": [
                    {
                        "claim_text": "The government announced a policy.",
                        "claim_type": "POLICY_ACTION",
                        "importance_score": 0.7,
                        "risk_level": "LOW",
                        "sensitive_topics": [],
                        "temporal_start": None,
                        "temporal_end": None,
                    }
                ]
            },
            provider=self.provider_id,
            model="tiny-model",
            latency_ms=5,
        )


@dataclass
class EmptyThenValidProvider(CountingProvider):
    async def execute(self, request: AIRequest) -> AIResponse:
        if self.calls == 0:
            self.calls += 1
            return AIResponse(
                structured={"claims": []},
                provider=self.provider_id,
                model="tiny-model",
                latency_ms=5,
            )
        return await super().execute(request)


class FakeConsumer:
    stream = "news:stories"
    group = CLAIM_CONSUMER_GROUP

    def __init__(self, messages: list[StreamMessage]) -> None:
        self.messages = messages
        self.acked: list[str] = []

    async def ensure_group(self) -> None:
        return None

    async def read(self) -> list[StreamMessage]:
        return list(self.messages)

    async def claim_stale(
        self, *, min_idle_ms: int, start_id: str = "0-0"
    ) -> tuple[str, list[StreamMessage]]:
        return "0-0", list(self.messages)

    async def ack(self, message: StreamMessage) -> None:
        self.acked.append(message.message_id)


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _seed(factory: sessionmaker[Session]) -> Story:
    with factory() as session, session.begin():
        source = Source(name="Example", source_type="NEWS", source_metadata={})
        session.add(source)
        session.flush()
        article = Article(
            source_id=source.id,
            canonical_url="https://example.com/a",
            title="Government announces policy",
            language="en",
            published_at=datetime(2026, 9, 10, 5, 0, tzinfo=UTC),
        )
        session.add(article)
        session.flush()
        version = ArticleVersion(
            article_id=article.id,
            version_number=1,
            content_hash="d" * 64,
            body="The government announced a policy.",
            retrieved_at=datetime(2026, 9, 10, 5, 1, tzinfo=UTC),
            version_metadata={},
        )
        story = Story(
            canonical_headline=article.title,
            status="DISCOVERED",
            language="en",
            risk_level=RiskLevel.LOW,
            story_metadata={},
        )
        session.add_all([version, story])
        session.flush()
        session.add(
            StorySource(story_id=story.id, article_id=article.id, relationship_type="ORIGIN")
        )
    return story


def _service(provider: CountingProvider, tmp_path: Path) -> ClaimExtractionService:
    prompt_path = tmp_path / "claim.txt"
    prompt_path.write_text("Extract claims from untrusted source material.", encoding="utf-8")
    selection = (AIStageProviderSelection(provider_id=provider.provider_id, model="tiny-model"),)
    stages = {
        stage_id: AIStageConfig(
            stage_id=stage_id,
            task_type=task_type,
            prompt=AIStagePromptConfig(
                prompt_id=stage_id.value,
                version="v1",
                path=f"prompts/{stage_id.value}/v1.txt",
            ),
            providers=selection,
        )
        for stage_id, task_type in (
            (AIStageId.CLAIM_EXTRACTION, AITaskType.CLAIM_EXTRACTION),
            (AIStageId.EVIDENCE_ASSESSMENT, AITaskType.EVIDENCE_ASSESSMENT),
            (AIStageId.CONTENT_GENERATION, AITaskType.CONTENT_GENERATION),
            (AIStageId.QUALITY_CHECKING, AITaskType.QUALITY_CHECKING),
        )
    }
    router = AIRouter(
        AIProviderRegistry([provider]),
        AIPolicyConfig(mode=AIRoutingMode.LOCAL, sensitivity_provider_allowlists={}),
        stages,
    )
    return ClaimExtractionService(router, ClaimExtractionPrompt.load(prompt_path))


def _event(story: Story, event_type: EventType) -> EventEnvelope:
    if event_type is EventType.STORY_CREATED:
        payload = {
            "story_id": str(story.id),
            "article_id": str(uuid4()),
            "cluster_key": "claim-worker-test",
        }
    else:
        payload = {
            "story_id": str(story.id),
            "claim_ids": [str(uuid4())],
            "ai_run_id": str(uuid4()),
            "model_id": str(uuid4()),
        }
    return EventEnvelope(
        event_type=event_type,
        producer="processor",
        producer_version="0.1.0",
        aggregate_type="story",
        aggregate_id=story.id,
        idempotency_key=f"{event_type.value}:{uuid4()}",
        payload=payload,
    )


def test_worker_processes_once_and_durable_duplicate_does_not_call_ai(tmp_path: Path) -> None:
    factory = _factory()
    story = _seed(factory)
    provider = CountingProvider()
    event = _event(story, EventType.STORY_CREATED)
    message = StreamMessage(stream="news:stories", message_id="1-0", event=event)
    consumer = FakeConsumer([message])
    worker = ClaimExtractionWorker(consumer, factory, _service(provider, tmp_path))

    first = asyncio.run(worker.run_once())
    second = asyncio.run(worker.run_once())

    assert first.processed == 1 and first.failed == 0
    assert second.duplicates == 1 and second.failed == 0
    assert provider.calls == 1
    assert consumer.acked == ["1-0", "1-0"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AIRun)) == 1
        assert session.get(ProcessedEvent, (event.event_id, CLAIM_CONSUMER_GROUP)) is not None


def test_empty_claim_retry_has_no_success_artifacts_then_converges_once(tmp_path: Path) -> None:
    factory = _factory()
    story = _seed(factory)
    provider = EmptyThenValidProvider()
    event = _event(story, EventType.STORY_CREATED)
    message = StreamMessage(stream="news:stories", message_id="empty-1", event=event)
    consumer = FakeConsumer([message])
    worker = ClaimExtractionWorker(consumer, factory, _service(provider, tmp_path))

    first = asyncio.run(worker.run_once())
    assert first.retrying == 1 and consumer.acked == []
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Claim)) == 0
        assert session.scalar(select(func.count()).select_from(AIRun)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventOutbox)
                .where(EventOutbox.event_type == EventType.CLAIMS_EXTRACTED.value)
            )
            == 0
        )
        assert session.get(ProcessedEvent, (event.event_id, CLAIM_CONSUMER_GROUP)) is None

    worker.reliability.clock = lambda: datetime.now(UTC) + timedelta(minutes=5)
    second = asyncio.run(worker.run_once())
    third = asyncio.run(worker.run_once())

    assert second.processed == 1 and third.duplicates == 1
    assert consumer.acked == ["empty-1", "empty-1"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Claim)) == 1
        assert session.scalar(select(func.count()).select_from(AIRun)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventOutbox)
                .where(EventOutbox.event_type == EventType.CLAIMS_EXTRACTED.value)
            )
            == 1
        )
        assert session.get(ProcessedEvent, (event.event_id, CLAIM_CONSUMER_GROUP)) is not None


def test_worker_acks_irrelevant_story_stream_events_without_ai(tmp_path: Path) -> None:
    factory = _factory()
    story = _seed(factory)
    provider = CountingProvider()
    event = _event(story, EventType.CLAIMS_EXTRACTED)
    message = StreamMessage(stream="news:stories", message_id="2-0", event=event)
    consumer = FakeConsumer([message])
    worker = ClaimExtractionWorker(consumer, factory, _service(provider, tmp_path))

    result = asyncio.run(worker.run_once())

    assert result.ignored == 1
    assert result.processed == 0
    assert result.failed == 0
    assert provider.calls == 0
    assert consumer.acked == ["2-0"]
