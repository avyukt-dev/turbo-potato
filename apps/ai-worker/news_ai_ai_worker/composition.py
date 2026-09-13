"""Separate production composition boundaries for content generation and quality."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from news_ai_ai import (
    AIProvider,
    AIRouter,
    AIStageConfigLoader,
    AITaskType,
    build_ai_router,
)
from news_ai_common.config import AppSettings, ConfigLoader
from news_ai_content import (
    ContentGenerationPrompt,
    ContentGenerationService,
)
from news_ai_editorial import EditorialConfigLoader
from news_ai_events import EventType, RedisStreamConsumer
from news_ai_events.streams import stream_for_event
from news_ai_quality import QualityAssessmentService, QualityPrompt
from sqlalchemy.orm import Session

from .content_worker import CONTENT_CONSUMER_GROUP, ContentGenerationWorker
from .quality_worker import QUALITY_CONSUMER_GROUP, QualityWorker


@dataclass(frozen=True, slots=True)
class ProductionContentStack:
    ai_router: AIRouter
    service: ContentGenerationService
    worker: ContentGenerationWorker


@dataclass(frozen=True, slots=True)
class ProductionQualityStack:
    ai_router: AIRouter
    service: QualityAssessmentService
    worker: QualityWorker


def build_production_content_stack(
    settings: AppSettings,
    *,
    session_factory: Callable[[], Session],
    redis_client: Any,
    consumer_name: str,
    ai_providers: Iterable[AIProvider] | None = None,
    ai_router: AIRouter | None = None,
) -> ProductionContentStack:
    if not consumer_name.strip():
        raise ValueError("content consumer name must not be blank")
    loader = ConfigLoader(settings.config_dir)
    if ai_router is not None and ai_providers is not None:
        raise ValueError("inject either an AI router or providers")
    router = ai_router or build_ai_router(loader, providers=ai_providers)
    router.validate_stage(AITaskType.CONTENT_GENERATION)
    stage_loader = AIStageConfigLoader(loader)
    stage = router.stage_config(AITaskType.CONTENT_GENERATION)
    prompt = ContentGenerationPrompt.load(
        stage_loader.resolve_prompt(stage), version=stage.prompt.version
    )
    if prompt.prompt_id != stage.prompt.prompt_id:
        raise ValueError("content-generation prompt identity does not match stage configuration")
    editorial_loader = EditorialConfigLoader(loader)
    style = editorial_loader.load_content_style()
    service = ContentGenerationService(
        router,
        prompt,
        style,
        editorial_loader.load_publishing_policy(),
    )
    consumer = RedisStreamConsumer(
        redis_client,
        stream=stream_for_event(EventType.CONTENT_REQUESTED),
        group=CONTENT_CONSUMER_GROUP,
        consumer=consumer_name,
    )
    return ProductionContentStack(
        ai_router=router,
        service=service,
        worker=ContentGenerationWorker(consumer, session_factory, service),
    )


def build_production_quality_stack(
    settings: AppSettings,
    *,
    session_factory: Callable[[], Session],
    redis_client: Any,
    consumer_name: str,
    ai_providers: Iterable[AIProvider] | None = None,
    ai_router: AIRouter | None = None,
) -> ProductionQualityStack:
    if not consumer_name.strip():
        raise ValueError("quality consumer name must not be blank")
    loader = ConfigLoader(settings.config_dir)
    if ai_router is not None and ai_providers is not None:
        raise ValueError("inject either an AI router or providers")
    router = ai_router or build_ai_router(loader, providers=ai_providers)
    router.validate_stage(AITaskType.QUALITY_CHECKING)
    stage_loader = AIStageConfigLoader(loader)
    stage = router.stage_config(AITaskType.QUALITY_CHECKING)
    prompt = QualityPrompt.load(stage_loader.resolve_prompt(stage), version=stage.prompt.version)
    if prompt.prompt_id != stage.prompt.prompt_id:
        raise ValueError("quality prompt identity does not match stage configuration")
    editorial_loader = EditorialConfigLoader(loader)
    service = QualityAssessmentService(
        router,
        prompt,
        editorial_loader.load_content_style(),
        editorial_loader.load_publishing_policy(),
    )
    consumer = RedisStreamConsumer(
        redis_client,
        stream=stream_for_event(EventType.CONTENT_GENERATED),
        group=QUALITY_CONSUMER_GROUP,
        consumer=consumer_name,
    )
    return ProductionQualityStack(
        ai_router=router,
        service=service,
        worker=QualityWorker(consumer, session_factory, service),
    )
