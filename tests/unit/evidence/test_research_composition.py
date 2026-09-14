from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest
from news_ai_ai import (
    AIRequest,
    AIResponse,
    AIResponseFormat,
    AIRoutingPolicyError,
    AITaskType,
    ProviderCapabilities,
    ProviderLocality,
)
from news_ai_ai_worker import build_production_content_stack, build_production_quality_stack
from news_ai_common.config import AppSettings
from news_ai_database import Base
from news_ai_evidence import PostgresArticleSearchProvider
from news_ai_research_worker import build_production_research_stack
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


class RedisBoundary:
    pass


@dataclass
class ConfiguredAIProvider:
    tasks: frozenset[AITaskType]
    provider_id: str

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            locality=(
                ProviderLocality.CLOUD if self.provider_id == "groq" else ProviderLocality.LOCAL
            ),
            task_types=self.tasks,
            response_formats=frozenset({AIResponseFormat.STRUCTURED}),
            models=frozenset(
                {("openai/gpt-oss-120b" if self.provider_id == "groq" else "local-news-ai")}
            ),
        )

    async def execute(self, request: AIRequest) -> AIResponse:
        raise AssertionError("composition must not perform inference")


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _prompt_checksum(relative_path: str) -> str:
    text = (Path("config") / relative_path).read_text(encoding="utf-8").strip()
    return hashlib.sha256(text.encode()).hexdigest()


def _providers(tasks: frozenset[AITaskType]) -> tuple[ConfiguredAIProvider, ...]:
    return (
        ConfiguredAIProvider(tasks, "groq"),
        ConfiguredAIProvider(tasks, "local-llama"),
    )


def test_production_research_stack_uses_production_boundaries() -> None:
    tasks = frozenset({AITaskType.CLAIM_EXTRACTION, AITaskType.EVIDENCE_ASSESSMENT})

    stack = build_production_research_stack(
        AppSettings(config_dir="config"),
        session_factory=_factory(),
        redis_client=RedisBoundary(),
        consumer_name="composition-test",
        ai_providers=_providers(tasks),
    )

    search = stack.search_registry.get("postgres-article-corpus")
    assert isinstance(search, PostgresArticleSearchProvider)
    assert stack.evidence_engine.source_resolver is stack.source_resolver
    assert stack.claim_worker.service.router is stack.ai_router
    assert stack.claim_worker.service.prompt.prompt_id == "claim-extraction"
    assert stack.claim_worker.service.prompt.version == "v4"
    assert stack.claim_worker.service.prompt.checksum == _prompt_checksum(
        "prompts/claim-extraction/v4.txt"
    )
    assert stack.evidence_engine.assessor.prompt.prompt_id == "evidence-assessment"
    assert stack.evidence_engine.assessor.prompt.version == "v2"
    assert stack.evidence_engine.assessor.prompt.checksum == _prompt_checksum(
        "prompts/evidence-assessment/v2.txt"
    )
    assert not hasattr(stack, "content_worker")
    assert stack.fact_sheet_worker.generator.requested_platforms == ("INSTAGRAM",)
    assert stack.fact_sheet_worker.generator.requested_formats == ("CAROUSEL",)


def test_composition_fails_when_required_evidence_ai_route_is_unavailable() -> None:
    providers = _providers(frozenset({AITaskType.CLAIM_EXTRACTION}))

    with pytest.raises(AIRoutingPolicyError, match="no configured AI provider"):
        build_production_research_stack(
            AppSettings(config_dir="config"),
            session_factory=_factory(),
            redis_client=RedisBoundary(),
            consumer_name="composition-test",
            ai_providers=providers,
        )


def test_production_content_stack_uses_official_router_and_worker_boundaries() -> None:
    providers = _providers(frozenset({AITaskType.CONTENT_GENERATION}))
    stack = build_production_content_stack(
        AppSettings(config_dir="config"),
        session_factory=_factory(),
        redis_client=RedisBoundary(),
        consumer_name="content-composition-test",
        ai_providers=providers,
    )
    assert stack.service.router is stack.ai_router
    assert stack.service.prompt.prompt_id == "content-generation"
    assert stack.service.prompt.version == "v4"
    assert stack.service.prompt.checksum == _prompt_checksum("prompts/content/v4.txt")
    assert stack.worker.service is stack.service
    assert stack.worker.consumer.group == "content-worker"


def test_production_content_stack_owns_content_generation_route_requirement() -> None:
    providers = _providers(frozenset({AITaskType.CLAIM_EXTRACTION}))
    with pytest.raises(AIRoutingPolicyError, match="no configured AI provider"):
        build_production_content_stack(
            AppSettings(config_dir="config"),
            session_factory=_factory(),
            redis_client=RedisBoundary(),
            consumer_name="content-composition-test",
            ai_providers=providers,
        )


def test_production_quality_stack_uses_official_router_and_worker_boundaries() -> None:
    providers = _providers(frozenset({AITaskType.QUALITY_CHECKING}))
    stack = build_production_quality_stack(
        AppSettings(config_dir="config"),
        session_factory=_factory(),
        redis_client=RedisBoundary(),
        consumer_name="quality-composition-test",
        ai_providers=providers,
    )
    assert stack.service.router is stack.ai_router
    assert stack.service.prompt.prompt_id == "content-quality"
    assert stack.service.prompt.version == "v4"
    assert stack.service.prompt.checksum == _prompt_checksum("prompts/quality/v4.txt")
    assert stack.worker.service is stack.service
    assert stack.worker.consumer.group == "quality-worker"


def test_production_quality_stack_owns_quality_route_requirement() -> None:
    providers = _providers(frozenset({AITaskType.CONTENT_GENERATION}))
    with pytest.raises(AIRoutingPolicyError, match="no configured AI provider"):
        build_production_quality_stack(
            AppSettings(config_dir="config"),
            session_factory=_factory(),
            redis_client=RedisBoundary(),
            consumer_name="quality-composition-test",
            ai_providers=providers,
        )
