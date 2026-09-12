"""Production composition boundary for the Stage-21 Content Engine."""

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
from sqlalchemy.orm import Session

from .content_worker import CONTENT_CONSUMER_GROUP, ContentGenerationWorker


@dataclass(frozen=True, slots=True)
class ProductionContentStack:
    ai_router: AIRouter
    service: ContentGenerationService
    worker: ContentGenerationWorker


def build_production_content_stack(
    settings: AppSettings,
    *,
    session_factory: Callable[[], Session],
    redis_client: Any,
    consumer_name: str,
    ai_providers: Iterable[AIProvider] | None = None,
) -> ProductionContentStack:
    if not consumer_name.strip():
        raise ValueError("content consumer name must not be blank")
    loader = ConfigLoader(settings.config_dir)
    router = build_ai_router(loader, providers=ai_providers)
    router.candidate_provider_ids(
        AIRequest(
            task_type=AITaskType.CONTENT_GENERATION,
            system_prompt="composition route validation",
            input={},
            response_format=AIResponseFormat.STRUCTURED,
        )
    )
    editorial_loader = EditorialConfigLoader(loader)
    style = editorial_loader.load_content_style()
    service = ContentGenerationService(
        router,
        ContentGenerationPrompt.load(Path(settings.config_dir) / "prompts" / "content" / "v1.txt"),
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
