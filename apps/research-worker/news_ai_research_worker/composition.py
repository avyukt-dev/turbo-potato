"""Production composition for the claim-research-verification stack."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from news_ai_ai import (
    AIProvider,
    AIRequest,
    AIResponseFormat,
    AIRouter,
    AITaskType,
    ClaimExtractionPrompt,
    ClaimExtractionService,
    build_ai_router,
)
from news_ai_ai_worker import CLAIM_CONSUMER_GROUP, ClaimExtractionWorker
from news_ai_common.config import AppSettings, ConfigLoader
from news_ai_editorial import EditorialConfigLoader
from news_ai_events import EventType, RedisStreamConsumer
from news_ai_events.streams import stream_for_event
from news_ai_evidence import (
    AIRouterEvidenceAssessor,
    EvidenceAssessmentPrompt,
    EvidenceEngine,
    FactCheckEngine,
    FactCheckPolicyLoader,
    FactSheetGenerator,
    PostgresArticleSearchProvider,
    ResearchPolicyLoader,
    SearchPolicyLoader,
    SearchProviderRegistry,
    SourceEvidenceResolver,
)
from sqlalchemy.orm import Session

from .fact_sheet import FACT_SHEET_CONSUMER_GROUP, FactSheetWorker
from .verification import (
    FACT_CHECK_CONSUMER_GROUP,
    STORY_VERIFICATION_CONSUMER_GROUP,
    FactCheckWorker,
    StoryVerificationWorker,
)
from .worker import (
    EVIDENCE_COLLECTION_CONSUMER_GROUP,
    RESEARCH_PLANNING_CONSUMER_GROUP,
    EvidenceCollectionWorker,
    ResearchPlanningWorker,
)


@dataclass(frozen=True, slots=True)
class ProductionResearchStack:
    ai_router: AIRouter
    search_registry: SearchProviderRegistry
    source_resolver: SourceEvidenceResolver
    evidence_engine: EvidenceEngine
    claim_worker: ClaimExtractionWorker
    research_planning_worker: ResearchPlanningWorker
    evidence_collection_worker: EvidenceCollectionWorker
    fact_check_worker: FactCheckWorker
    story_verification_worker: StoryVerificationWorker
    fact_sheet_worker: FactSheetWorker


def build_production_research_stack(
    settings: AppSettings,
    *,
    session_factory: Callable[[], Session],
    redis_client: Any,
    consumer_name: str,
    ai_providers: Iterable[AIProvider] | None = None,
) -> ProductionResearchStack:
    """Build production classes from repository config and injected infrastructure."""

    if not consumer_name.strip():
        raise ValueError("research consumer name must not be blank")
    loader = ConfigLoader(settings.config_dir)
    router = build_ai_router(loader, providers=ai_providers)
    for task_type in (AITaskType.CLAIM_EXTRACTION, AITaskType.EVIDENCE_ASSESSMENT):
        router.candidate_provider_ids(
            AIRequest(
                task_type=task_type,
                system_prompt="composition route validation",
                input={},
                response_format=AIResponseFormat.STRUCTURED,
            )
        )
    prompt_root = Path(settings.config_dir) / "prompts"
    claim_service = ClaimExtractionService(
        router,
        ClaimExtractionPrompt.load(prompt_root / "claim-extraction" / "v1.txt"),
    )
    assessor = AIRouterEvidenceAssessor(
        router,
        EvidenceAssessmentPrompt.load(prompt_root / "evidence-assessment" / "v1.txt"),
    )
    search_registry = SearchProviderRegistry((PostgresArticleSearchProvider(session_factory),))
    source_resolver = SourceEvidenceResolver(ResearchPolicyLoader(loader).load())
    evidence_engine = EvidenceEngine(
        search_registry,
        SearchPolicyLoader(loader).load(),
        _select_first_compatible_provider,
        assessor,
        source_resolver=source_resolver,
    )
    fact_check_engine = FactCheckEngine(
        FactCheckPolicyLoader(loader).load(),
        EditorialConfigLoader(loader).load().risk_policy,
    )

    def consumer(event_type: EventType, group: str) -> RedisStreamConsumer:
        return RedisStreamConsumer(
            redis_client,
            stream=stream_for_event(event_type),
            group=group,
            consumer=consumer_name,
        )

    return ProductionResearchStack(
        ai_router=router,
        search_registry=search_registry,
        source_resolver=source_resolver,
        evidence_engine=evidence_engine,
        claim_worker=ClaimExtractionWorker(
            consumer(EventType.STORY_CREATED, CLAIM_CONSUMER_GROUP),
            session_factory,
            claim_service,
        ),
        research_planning_worker=ResearchPlanningWorker(
            consumer(EventType.CLAIMS_EXTRACTED, RESEARCH_PLANNING_CONSUMER_GROUP),
            session_factory,
            evidence_engine,
        ),
        evidence_collection_worker=EvidenceCollectionWorker(
            consumer(EventType.EVIDENCE_REQUESTED, EVIDENCE_COLLECTION_CONSUMER_GROUP),
            session_factory,
            evidence_engine,
        ),
        fact_check_worker=FactCheckWorker(
            consumer(EventType.EVIDENCE_COLLECTED, FACT_CHECK_CONSUMER_GROUP),
            session_factory,
            fact_check_engine,
        ),
        story_verification_worker=StoryVerificationWorker(
            consumer(EventType.FACT_CHECK_COMPLETED, STORY_VERIFICATION_CONSUMER_GROUP),
            session_factory,
            fact_check_engine,
        ),
        fact_sheet_worker=FactSheetWorker(
            consumer(EventType.STORY_VERIFIED, FACT_SHEET_CONSUMER_GROUP),
            session_factory,
            FactSheetGenerator(),
        ),
    )


def _select_first_compatible_provider(_request: Any, compatible: tuple[str, ...]) -> str:
    if not compatible:
        raise ValueError("no compatible search provider")
    return compatible[0]
