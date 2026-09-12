from __future__ import annotations

from dataclasses import dataclass

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
    provider_id: str = "local-llama"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            locality=ProviderLocality.LOCAL,
            task_types=self.tasks,
            response_formats=frozenset({AIResponseFormat.STRUCTURED}),
        )

    async def execute(self, request: AIRequest) -> AIResponse:
        raise AssertionError("composition must not perform inference")


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_production_research_stack_uses_production_boundaries() -> None:
    provider = ConfiguredAIProvider(
        frozenset({AITaskType.CLAIM_EXTRACTION, AITaskType.EVIDENCE_ASSESSMENT})
    )

    stack = build_production_research_stack(
        AppSettings(config_dir="config"),
        session_factory=_factory(),
        redis_client=RedisBoundary(),
        consumer_name="composition-test",
        ai_providers=(provider,),
    )

    search = stack.search_registry.get("postgres-article-corpus")
    assert isinstance(search, PostgresArticleSearchProvider)
    assert stack.evidence_engine.source_resolver is stack.source_resolver
    assert stack.claim_worker.service.router is stack.ai_router


def test_composition_fails_when_required_evidence_ai_route_is_unavailable() -> None:
    provider = ConfiguredAIProvider(frozenset({AITaskType.CLAIM_EXTRACTION}))

    with pytest.raises(AIRoutingPolicyError, match="no configured AI provider"):
        build_production_research_stack(
            AppSettings(config_dir="config"),
            session_factory=_factory(),
            redis_client=RedisBoundary(),
            consumer_name="composition-test",
            ai_providers=(provider,),
        )
