from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from news_ai_ai import (
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AITaskType,
    ProviderCapabilities,
    ProviderLocality,
)
from news_ai_ai_worker import build_production_content_stack, build_production_quality_stack
from news_ai_api.main import create_app
from news_ai_collector import CollectedArticle, DiscoveredArticleHandler
from news_ai_common.config import AppSettings, ConfigLoader
from news_ai_database import (
    AIRun,
    Article,
    ArticleVersion,
    AuditLog,
    Base,
    Claim,
    ContentDraft,
    ContentQualityCheck,
    ContentVariant,
    EventOutbox,
    EvidenceItem,
    FactCheck,
    FactSheet,
    ProcessedEvent,
    Publication,
    ReviewDecisionRecord,
    Source,
    SourceFeed,
    Story,
)
from news_ai_domain import ClaimVerificationStatus, FactCheckLabel, ReviewState
from news_ai_editorial import EditorialConfigLoader
from news_ai_events import (
    EventType,
    OutboxDispatcher,
    RedisStreamConsumer,
    RedisStreamPublisher,
)
from news_ai_events.outbox import envelope_from_outbox
from news_ai_events.streams import STREAM_BY_EVENT, stream_for_event
from news_ai_processor import (
    NORMALIZER_CONSUMER_GROUP,
    PROCESSOR_CONSUMER_GROUP,
    NormalizerEventWorker,
    ProcessorEventWorker,
    StoryClusteringService,
)
from news_ai_research_worker import build_production_research_stack
from news_ai_review import ApprovalEligibilityService
from redis.asyncio import Redis
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("NEWS_AI_DATABASE_URL")
REDIS_URL = os.getenv("NEWS_AI_REDIS_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not REDIS_URL,
    reason="PostgreSQL and Redis integration dependencies are not configured",
)


@dataclass
class DeterministicResearchAI:
    quality_defect: bool = False
    provider_id: str = "local-llama"
    requests: list[AIRequest] = field(default_factory=list)

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            locality=ProviderLocality.LOCAL,
            task_types=frozenset(
                {
                    AITaskType.CLAIM_EXTRACTION,
                    AITaskType.EVIDENCE_ASSESSMENT,
                    AITaskType.CONTENT_GENERATION,
                    AITaskType.QUALITY_CHECKING,
                }
            ),
            response_formats=frozenset({AIResponseFormat.STRUCTURED}),
            models=frozenset({"deterministic-research-model"}),
        )

    async def execute(self, request: AIRequest) -> AIResponse:
        self.requests.append(request)
        if request.task_type is AITaskType.CLAIM_EXTRACTION:
            structured = {
                "claims": [
                    {
                        "claim_text": "The river gauge measured two metres.",
                        "claim_type": "MEASUREMENT",
                        "importance_score": 0.9,
                        "risk_level": "HIGH",
                        "sensitive_topics": [],
                        "temporal_start": None,
                        "temporal_end": None,
                    },
                    {
                        "claim_text": "An uncorroborated evacuation estimate was final.",
                        "claim_type": "ESTIMATE",
                        "importance_score": 0.7,
                        "risk_level": "LOW",
                        "sensitive_topics": [],
                        "temporal_start": None,
                        "temporal_end": None,
                    },
                ]
            }
        elif request.task_type is AITaskType.EVIDENCE_ASSESSMENT:
            claim_text = request.input["claim_text"]
            snippet = request.input["candidate_snippet"]
            unverified = "uncorroborated" in claim_text.casefold()
            structured = {
                "claim_id": request.input["claim_id"],
                "candidate_url": request.input["candidate_url"],
                "relation": "CONTEXT" if unverified else "DIRECT_SUPPORT",
                "strength_score": 0.2 if unverified else 0.95,
                "relevant_excerpt": None if unverified else snippet[:1000],
                "notes": "Context only." if unverified else "The reviewed text states the reading.",
            }
        elif request.task_type is AITaskType.CONTENT_GENERATION:
            brief = request.input["editorial_brief"]
            claim = brief["claims"][0]
            structured = {
                "story_id": brief["story_id"],
                "fact_sheet_id": brief["fact_sheet_id"],
                "fact_sheet_version": brief["fact_sheet_version"],
                "platform": "INSTAGRAM",
                "format": "CAROUSEL",
                "language": request.input["generation_language"],
                "title": "What the flood records show",
                "slides": [
                    {
                        "position": 1,
                        "heading": "The measured claim",
                        "body": claim["text"],
                        "claim_ids": [claim["claim_id"]],
                    },
                    {
                        "position": 2,
                        "heading": "Evidence and limits",
                        "body": "The evidence supports this claim with stated limitations.",
                        "claim_ids": [claim["claim_id"]],
                    },
                ],
                "caption": (
                    "The uncorroborated evacuation estimate is final and confirmed."
                    if self.quality_defect
                    else "The verification stage is complete; uncertainty remains explicit."
                ),
                "hashtags": ["#EvidenceFirst"],
                "claim_ids_used": [claim["claim_id"]],
            }
        elif request.task_type is AITaskType.QUALITY_CHECKING:
            variant = request.input["content_artifact"]
            defect = "final and confirmed" in variant["caption"]
            structured = {
                "content_variant_id": variant["content_variant_id"],
                "factual_accuracy_passed": not defect,
                "source_alignment_passed": True,
                "citation_alignment_passed": True,
                "style_passed": True,
                "unsupported_claims": (
                    ["The final, confirmed evacuation estimate is unsupported."] if defect else []
                ),
                "fabricated_quotes": [],
                "incorrect_names": [],
                "incorrect_dates": [],
                "incorrect_numbers": [],
                "missing_context": (
                    ["The estimate is uncorroborated and provisional."] if defect else []
                ),
                "defamation_risk": False,
                "sensitive_topic_error": False,
                "notes": ["Assessment remained bounded to the immutable Fact Sheet."],
            }
        else:  # pragma: no cover - capability contract prevents this branch
            raise AssertionError(f"unexpected AI task {request.task_type}")
        return AIResponse(
            structured=structured,
            provider=self.provider_id,
            model="deterministic-research-model",
            latency_ms=1,
        )


@pytest.mark.parametrize(
    ("quality_defect", "expected_pass", "review_decision"),
    [
        (False, True, ReviewState.APPROVED),
        (False, True, ReviewState.CHANGES_REQUESTED),
        (True, False, None),
    ],
)
def test_real_postgres_redis_pipeline_reaches_quality_boundary(
    quality_defect: bool,
    expected_pass: bool,
    review_decision: ReviewState | None,
) -> None:
    assert DATABASE_URL is not None and REDIS_URL is not None
    if os.getenv("NEWS_AI_ENVIRONMENT") != "test":
        pytest.skip("destructive pipeline isolation is restricted to the test environment")
    asyncio.run(
        _run_pipeline(DATABASE_URL, REDIS_URL, quality_defect, expected_pass, review_decision)
    )


async def _run_pipeline(
    database_url: str,
    redis_url: str,
    quality_defect: bool,
    expected_pass: bool,
    review_decision: ReviewState | None,
) -> None:
    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(redis_url, decode_responses=True)
    streams = tuple(sorted(set(STREAM_BY_EVENT.values())))
    await redis.delete(*streams)
    with engine.begin() as connection:
        tables = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
        connection.exec_driver_sql(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")
    ai = DeterministicResearchAI(quality_defect=quality_defect)
    settings = AppSettings(
        config_dir="config",
        database_url=database_url,
        redis_url=redis_url,
        review_api_token="integration-review-token",
        reviewer_id=UUID("22222222-2222-2222-2222-222222222222"),
        review_capabilities="view,review,approve",
    )
    stack = build_production_research_stack(
        settings,
        session_factory=factory,
        redis_client=redis,
        consumer_name="production-e2e",
        ai_providers=(ai,),
    )
    content_stack = build_production_content_stack(
        settings,
        session_factory=factory,
        redis_client=redis,
        consumer_name="production-content-e2e",
        ai_providers=(ai,),
    )
    quality_stack = build_production_quality_stack(
        settings,
        session_factory=factory,
        redis_client=redis,
        consumer_name="production-quality-e2e",
        ai_providers=(ai,),
    )
    normalizer = NormalizerEventWorker(
        RedisStreamConsumer(
            redis,
            stream=stream_for_event(EventType.ARTICLE_DISCOVERED),
            group=NORMALIZER_CONSUMER_GROUP,
            consumer="production-e2e",
            block_ms=50,
        ),
        factory,
    )
    processor = ProcessorEventWorker(
        RedisStreamConsumer(
            redis,
            stream=stream_for_event(EventType.ARTICLE_NORMALIZED),
            group=PROCESSOR_CONSUMER_GROUP,
            consumer="production-e2e",
            block_ms=50,
        ),
        factory,
        StoryClusteringService(),
    )
    workers = (
        normalizer,
        processor,
        stack.claim_worker,
        stack.research_planning_worker,
        stack.evidence_collection_worker,
        stack.fact_check_worker,
        stack.story_verification_worker,
        stack.fact_sheet_worker,
        content_stack.worker,
        quality_stack.worker,
    )
    try:
        for worker in workers:
            await worker.ensure_ready()

        now = datetime(2026, 9, 11, 8, tzinfo=UTC)
        with factory() as session, session.begin():
            source = Source(
                name="Origin News",
                domain="origin.example",
                source_type="NEWS",
                authority_level=4,
                source_metadata={"wire_origin": "wire:flood-42"},
            )
            session.add(source)
            session.flush()
            feed = SourceFeed(
                source_id=source.id,
                name="Origin feed",
                feed_url="https://origin.example/feed",
                feed_type="RSS",
                is_active=True,
                configuration={},
            )
            session.add(feed)
            session.flush()
            discovery = DiscoveredArticleHandler()(
                session,
                CollectedArticle(
                    source_id=source.id,
                    source_feed_id=feed.id,
                    url="https://origin.example/flood-report",
                    title="River gauge flood report",
                    published_at=now,
                    language="en",
                    summary="The river gauge report includes a provisional estimate.",
                    body=(
                        "The river gauge measured two metres. An uncorroborated evacuation "
                        "estimate was described as provisional, not final."
                    ),
                ),
                retrieved_at=now,
            )
            discovery_event_id = discovery.event_id

            syndicated_source = Source(
                name="Syndicated News",
                domain="syndicated.example",
                source_type="NEWS",
                authority_level=4,
                source_metadata={"wire_origin": "wire:flood-42"},
            )
            session.add(syndicated_source)
            session.flush()
            syndicated_article = Article(
                source_id=syndicated_source.id,
                canonical_url="https://syndicated.example/flood-copy",
                title="River gauge flood report copy",
                language="en",
                published_at=now,
            )
            session.add(syndicated_article)
            session.flush()
            session.add(
                ArticleVersion(
                    article_id=syndicated_article.id,
                    version_number=1,
                    content_hash="9" * 64,
                    body=(
                        "The river gauge measured two metres. The evacuation estimate remains "
                        "uncorroborated and provisional."
                    ),
                    retrieved_at=now,
                    version_metadata={"originating_url": "https://wire.example/flood-42"},
                )
            )
            primary_record_source = Source(
                name="Community records archive",
                domain="records.example",
                source_type="NEWS",
                authority_level=4,
                source_metadata={},
            )
            session.add(primary_record_source)
            session.flush()
            primary_record = Article(
                source_id=primary_record_source.id,
                canonical_url="https://records.example/gauge-reading-17",
                title="River gauge measurement record",
                language="en",
                published_at=now,
            )
            session.add(primary_record)
            session.flush()
            session.add(
                ArticleVersion(
                    article_id=primary_record.id,
                    version_number=1,
                    content_hash="8" * 64,
                    body="The river gauge measured two metres in measurement record 17.",
                    retrieved_at=now,
                    version_metadata={"primary_document_id": "gauge-record:17"},
                )
            )

        dispatcher = OutboxDispatcher(factory, RedisStreamPublisher(redis), batch_size=100)

        async def dispatch_and_run(worker: object) -> object:
            dispatched = await dispatcher.dispatch_once()
            assert dispatched.published >= 1
            result = await worker.run_once()
            assert result.received >= 1
            assert result.failed == 0
            assert result.dead_lettered == 0
            return result

        await dispatch_and_run(normalizer)
        await dispatch_and_run(processor)
        await dispatch_and_run(stack.claim_worker)
        await dispatch_and_run(stack.research_planning_worker)
        await dispatch_and_run(stack.evidence_collection_worker)
        await dispatch_and_run(stack.fact_check_worker)
        await dispatch_and_run(stack.story_verification_worker)
        await dispatch_and_run(stack.fact_sheet_worker)
        await dispatch_and_run(content_stack.worker)
        await dispatch_and_run(quality_stack.worker)
        await dispatcher.dispatch_once()

        with factory() as session:
            events = list(
                session.scalars(
                    select(EventOutbox)
                    .where(EventOutbox.correlation_id.is_not(None))
                    .order_by(EventOutbox.created_at, EventOutbox.id)
                )
            )
            by_type: dict[str, list[EventOutbox]] = {}
            for event in events:
                by_type.setdefault(event.event_type, []).append(event)
            discovered = next(
                item
                for item in by_type[EventType.ARTICLE_DISCOVERED.value]
                if item.event_id == discovery_event_id
            )
            normalized = next(
                item
                for item in by_type[EventType.ARTICLE_NORMALIZED.value]
                if item.causation_id == discovered.event_id
            )
            story_created = next(
                item
                for event_type in (EventType.STORY_CREATED, EventType.STORY_CLUSTERED)
                for item in by_type.get(event_type.value, [])
                if item.causation_id == normalized.event_id
            )
            claims_extracted = next(
                item
                for item in by_type[EventType.CLAIMS_EXTRACTED.value]
                if item.causation_id == story_created.event_id
            )
            evidence_requested = next(
                item
                for item in by_type[EventType.EVIDENCE_REQUESTED.value]
                if item.causation_id == claims_extracted.event_id
            )
            evidence_collected = next(
                item
                for item in by_type[EventType.EVIDENCE_COLLECTED.value]
                if item.causation_id == evidence_requested.event_id
            )
            completed = [
                item
                for item in by_type[EventType.FACT_CHECK_COMPLETED.value]
                if item.causation_id == evidence_collected.event_id
            ]
            verified = next(
                item
                for item in by_type[EventType.STORY_VERIFIED.value]
                if item.causation_id in {item.event_id for item in completed}
            )
            content = next(
                item
                for item in by_type[EventType.CONTENT_REQUESTED.value]
                if item.causation_id == verified.event_id
            )
            generated = next(
                item
                for item in by_type[EventType.CONTENT_GENERATED.value]
                if item.causation_id == content.event_id
            )
            quality_event = next(
                item
                for item in by_type[EventType.CONTENT_QUALITY_CHECKED.value]
                if item.causation_id == generated.event_id
            )
            story = session.get(Story, story_created.aggregate_id)
            claims = list(session.scalars(select(Claim).where(Claim.story_id == story.id)))
            check_ids = tuple(UUID(item) for item in verified.payload["fact_check_ids"])
            checks = list(session.scalars(select(FactCheck).where(FactCheck.id.in_(check_ids))))
            sheet = session.get(FactSheet, UUID(content.payload["fact_sheet_id"]))
            assert sheet is not None
            draft = session.get(ContentDraft, UUID(generated.payload["content_draft_id"]))
            assert draft is not None
            variants = list(
                session.scalars(
                    select(ContentVariant).where(ContentVariant.content_draft_id == draft.id)
                )
            )
            content_run = session.get(AIRun, draft.created_by_ai_run_id)
            quality_checks = list(
                session.scalars(
                    select(ContentQualityCheck).where(
                        ContentQualityCheck.content_draft_id == draft.id
                    )
                )
            )
            assert content_run is not None
            evidence_ids = tuple(UUID(item["evidence_id"]) for item in sheet.evidence_snapshot)
            evidence = list(
                session.scalars(select(EvidenceItem).where(EvidenceItem.id.in_(evidence_ids)))
            )

            assert {claim.status for claim in claims} <= set(ClaimVerificationStatus)
            assert ClaimVerificationStatus.UNVERIFIED in {claim.status for claim in claims}
            assert FactCheckLabel.UNVERIFIED in {check.label for check in checks}
            high_claim = next(claim for claim in claims if claim.risk_level == "HIGH")
            high_check = next(check for check in checks if check.claim_id == high_claim.id)
            assert high_claim.status is ClaimVerificationStatus.PARTIALLY_SUPPORTED
            assert high_check.label is FactCheckLabel.PARTIALLY_TRUE
            assert "independent_support_groups=2" in (high_check.reasoning_summary or "")
            assert "authority_qualified_support_groups=0" in (high_check.reasoning_summary or "")
            assert story.status == "VERIFIED"
            assert content.payload["fact_sheet_id"] == str(sheet.id)
            assert content.aggregate_id == sheet.id
            assert content.idempotency_key == f"content.requested:{sheet.id}:{sheet.version}"
            assert draft.fact_sheet_id == sheet.id
            assert draft.fact_sheet_version == sheet.version
            assert draft.review_state is (
                ReviewState.READY_FOR_REVIEW if expected_pass else ReviewState.NOT_READY
            )
            assert draft.review_required is True
            assert draft.risk_level == sheet.risk_level
            assert draft.sensitive_topics == sheet.sensitive_topics
            assert len(variants) == 1
            assert variants[0].platform == "INSTAGRAM"
            assert variants[0].format == "CAROUSEL"
            assert variants[0].review_state is (
                ReviewState.READY_FOR_REVIEW if expected_pass else ReviewState.NOT_READY
            )
            assert variants[0].media_asset_ids == []
            assert generated.payload["content_variant_ids"] == [str(variants[0].id)]
            assert generated.payload["ai_run_id"] == str(draft.created_by_ai_run_id)
            assert content_run.task_type == AITaskType.CONTENT_GENERATION.value
            assert content_run.prompt_id == "content-generation"
            assert content_run.prompt_version == "v1"
            assert content_run.prompt_checksum
            assert len(quality_checks) == 1
            quality_check = quality_checks[0]
            assert quality_check.passed is expected_pass
            assert quality_check.review_required is True
            assert quality_check.content_variant_id == variants[0].id
            assert quality_check.content_variant_version == variants[0].version
            assert quality_check.fact_sheet_id == sheet.id
            assert quality_check.fact_sheet_version == sheet.version
            assert quality_check.methodology_version == "quality-gate-methodology-v2"
            quality_run = session.get(AIRun, quality_check.ai_run_id)
            assert quality_run.task_type == AITaskType.QUALITY_CHECKING.value
            assert quality_run.prompt_id == "content-quality"
            assert quality_run.prompt_version == "v1"
            assert quality_run.prompt_checksum
            assert quality_event.aggregate_type == "content_draft"
            assert quality_event.aggregate_id == draft.id
            assert quality_event.correlation_id == generated.correlation_id
            assert quality_event.payload["passed"] is expected_pass
            assert quality_event.payload["review_required"] is True
            assert quality_event.payload["fact_check_passed"] is expected_pass
            assert quality_event.payload["source_check_passed"] is True
            assert quality_event.payload["style_check_passed"] is True
            assert session.scalar(
                select(ProcessedEvent).where(
                    ProcessedEvent.event_id == generated.event_id,
                    ProcessedEvent.consumer_group == "quality-worker",
                )
            )
            fact_sheet_claim_ids = {item["claim_id"] for item in sheet.claims_snapshot}
            assert set(variants[0].claim_ids_used) <= fact_sheet_claim_ids
            selected_claim_ids = set(variants[0].claim_ids_used)
            expected_source_ids = {
                item["source_id"]
                for item in sheet.evidence_snapshot
                if item["claim_id"] in selected_claim_ids and item["source_id"] is not None
            }
            assert set(variants[0].source_ids_used) == expected_source_ids
            claims_by_id = {claim.id: claim for claim in claims}
            assert all(
                check.research_generation == claims_by_id[check.claim_id].research_generation
                and claims_by_id[check.claim_id].current_fact_check_id == check.id
                for check in checks
            )
            assert all(item.research_run_id is not None for item in checks)
            reviewed = [item for item in evidence if item.source_id is not None]
            assert reviewed
            assert all(item.evidence_metadata["article_version_id"] for item in reviewed)
            assert all(item.evidence_metadata["assessment_ai_run_ids"] for item in reviewed)
            per_claim_groups: dict[str, set[str]] = {}
            for item in reviewed:
                assessment = item.evidence_metadata["relationship_assessments"][0]
                per_claim_groups.setdefault(assessment["claim_id"], set()).add(
                    item.evidence_metadata["independence_group"]
                )
            high_groups = per_claim_groups[str(high_claim.id)]
            assert len(high_groups) == 2
            high_evidence = [
                item
                for item in reviewed
                if item.evidence_metadata["relationship_assessments"][0]["claim_id"]
                == str(high_claim.id)
            ]
            assert all(item.evidence_metadata["source_level"] == 4 for item in high_evidence)
            assert (
                len(
                    {
                        item.evidence_metadata["independence_group"]
                        for item in high_evidence
                        if item.evidence_metadata["lineage_status"] == "KNOWN_SHARED"
                    }
                )
                == 1
            )
            assert all(
                entry["article_version_id"] and entry["content_hash"]
                for entry in sheet.evidence_snapshot
            )
            assessment_calls = [
                request
                for request in ai.requests
                if request.task_type is AITaskType.EVIDENCE_ASSESSMENT
            ]
            assert len(evidence) == len(assessment_calls)
            content_calls = [
                request
                for request in ai.requests
                if request.task_type is AITaskType.CONTENT_GENERATION
            ]
            assert len(content_calls) == 1
            assert content_calls[0].input_artifact_ids == (
                f"fact_sheet:{sheet.id}:v{sheet.version}",
            )
            assert content_calls[0].input["generation_language"] == story.language
            quality_calls = [
                request
                for request in ai.requests
                if request.task_type is AITaskType.QUALITY_CHECKING
            ]
            assert len(quality_calls) == 1
            assert quality_calls[0].input_artifact_ids == (
                f"fact_sheet:{sheet.id}:v{sheet.version}",
                f"content_variant:{variants[0].id}:v{variants[0].version}",
            )
            assert not any(item.event_type.startswith("publication.") for item in events)

            variant_id = variants[0].id
            draft_id = draft.id
            sheet_id = sheet.id
            sheet_version = sheet.version
            quality_check_id = quality_check.id
            ai_run_count_before_review = session.scalar(select(func.count()).select_from(AIRun))

        semantic_quality_replay = envelope_from_outbox(generated).model_copy(
            update={"event_id": uuid4(), "idempotency_key": f"replay:{uuid4()}"}
        )
        await redis.xadd(
            stream_for_event(EventType.CONTENT_GENERATED),
            {"event": semantic_quality_replay.model_dump_json()},
        )
        duplicate_quality = await quality_stack.worker.run_once()
        assert duplicate_quality.duplicates == 1
        with factory() as session:
            assert len(list(session.scalars(select(ContentQualityCheck)))) == 1
            assert (
                len(
                    list(
                        session.scalars(
                            select(EventOutbox).where(
                                EventOutbox.event_type == EventType.CONTENT_QUALITY_CHECKED.value
                            )
                        )
                    )
                )
                == 1
            )

        semantic_replay = envelope_from_outbox(content).model_copy(
            update={"event_id": uuid4(), "idempotency_key": f"replay:{uuid4()}"}
        )
        await redis.xadd(
            stream_for_event(EventType.CONTENT_REQUESTED),
            {"event": semantic_replay.model_dump_json()},
        )
        duplicate_content = await content_stack.worker.run_once()
        assert duplicate_content.duplicates == 1
        with factory() as session:
            assert len(list(session.scalars(select(ContentDraft)))) == 1
            assert len(list(session.scalars(select(ContentVariant)))) == 1
            assert (
                len(
                    list(
                        session.scalars(
                            select(EventOutbox).where(
                                EventOutbox.event_type == EventType.CONTENT_GENERATED.value
                            )
                        )
                    )
                )
                == 1
            )

        if review_decision is not None:
            client = TestClient(create_app(settings))
            action = "approve" if review_decision is ReviewState.APPROVED else "request-changes"
            body = {"artifact_version": 1}
            if review_decision is ReviewState.CHANGES_REQUESTED:
                body["reason"] = "Clarify the provisional estimate before publication."
            response = client.post(
                f"/api/v1/review/content_variant/{variant_id}/{action}",
                headers={
                    "Authorization": "Bearer integration-review-token",
                    "Idempotency-Key": f"e2e-review-{review_decision.value.casefold()}",
                },
                json=body,
            )
            assert response.status_code == 200, response.text
            assert response.json()["decision"] == review_decision.value
            with factory() as session:
                decision = session.scalar(
                    select(ReviewDecisionRecord).where(
                        ReviewDecisionRecord.artifact_id == variant_id
                    )
                )
                audit = session.scalar(
                    select(AuditLog).where(AuditLog.review_decision_id == decision.id)
                )
                reviewed_variant = session.get(ContentVariant, variant_id)
                reviewed_draft = session.get(ContentDraft, draft_id)
                assert decision.artifact_version == reviewed_variant.version == 1
                assert decision.content_draft_id == draft_id
                assert decision.fact_sheet_id == sheet_id
                assert decision.fact_sheet_version == sheet_version
                assert decision.quality_check_id == quality_check_id
                assert decision.reviewer_id == UUID("22222222-2222-2222-2222-222222222222")
                assert decision.artifact_snapshot
                assert len(decision.artifact_hash) == 64
                assert audit.action == review_decision.value
                assert reviewed_variant.review_state is review_decision
                assert reviewed_draft.review_state is review_decision
                eligibility = ApprovalEligibilityService(
                    factory,
                    EditorialConfigLoader(ConfigLoader("config")).load_publishing_policy(),
                )
                assert eligibility.is_exact_version_approved(variant_id) is (
                    review_decision is ReviewState.APPROVED
                )
                assert session.scalar(select(func.count()).select_from(AIRun)) == (
                    ai_run_count_before_review
                )
                assert not any(
                    item.event_type.startswith("publication.")
                    for item in session.scalars(select(EventOutbox))
                )
        # Stage 25 adds scheduling persistence, but human approval still does
        # not create a publication or invoke an adapter automatically.
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(Publication)) == 0
        for stream in streams:
            assert all(
                '"event_type":"publication.' not in fields.get("event", "")
                for _message_id, fields in await redis.xrange(stream)
            )

        if not expected_pass:
            quality_call_count = len(
                [
                    request
                    for request in ai.requests
                    if request.task_type is AITaskType.QUALITY_CHECKING
                ]
            )
            with factory() as session, session.begin():
                session.add(
                    FactSheet(
                        story_id=sheet.story_id,
                        version=sheet.version + 1,
                        headline=sheet.headline,
                        summary=sheet.summary,
                        risk_level=sheet.risk_level,
                        sensitive_topics=sheet.sensitive_topics,
                        semantic_key=f"superseding-sheet:{uuid4()}",
                    )
                )
            stale_replay = envelope_from_outbox(generated).model_copy(
                update={"event_id": uuid4(), "idempotency_key": f"stale:{uuid4()}"}
            )
            await redis.xadd(
                stream_for_event(EventType.CONTENT_GENERATED),
                {"event": stale_replay.model_dump_json()},
            )
            stale_quality = await quality_stack.worker.run_once()
            assert stale_quality.stale == 1
            with factory() as session:
                assert session.get(ContentDraft, draft.id).review_state is ReviewState.NOT_READY
                assert (
                    session.get(ContentVariant, variants[0].id).review_state
                    is ReviewState.NOT_READY
                )
                assert len(list(session.scalars(select(ContentQualityCheck)))) == 1
                assert (
                    session.scalar(
                        select(func.count())
                        .select_from(EventOutbox)
                        .where(EventOutbox.event_type == EventType.CONTENT_QUALITY_CHECKED.value)
                    )
                    == 1
                )
            assert (
                len(
                    [
                        request
                        for request in ai.requests
                        if request.task_type is AITaskType.QUALITY_CHECKING
                    ]
                )
                == quality_call_count
            )

        for worker in workers:
            pending = await redis.xpending(worker.consumer.stream, worker.consumer.group)
            assert pending["pending"] == 0

        duplicate_event = envelope_from_outbox(claims_extracted)
        await redis.xadd(
            stream_for_event(EventType.CLAIMS_EXTRACTED),
            {"event": duplicate_event.model_dump_json()},
        )
        duplicate = await stack.research_planning_worker.run_once()
        assert duplicate.duplicates == 1
        with factory() as session:
            assert session.scalar(
                select(ProcessedEvent).where(
                    ProcessedEvent.event_id == claims_extracted.event_id,
                    ProcessedEvent.consumer_group == "research-planner",
                )
            )
    finally:
        await redis.delete(*streams)
        await redis.aclose()
        engine.dispose()
