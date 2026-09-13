import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from integration.test_production_research_pipeline import DeterministicResearchAI
from news_ai_ai.routing import AIRoutingPolicyError
from news_ai_collector import RSSCollector
from news_ai_common.config import AppSettings
from news_ai_events.consumer_contracts import EVENT_CONSUMER_CONTRACTS
from news_ai_pipeline import build_production_pipeline_stack
from news_ai_pipeline.composition import ProductionPipelineStack, _close_resources
from unit.pipeline.test_runner import fast_config


def test_production_composition_reuses_stacks_shared_router_and_closed_groups(monkeypatch):
    client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr("news_ai_pipeline.composition.Redis.from_url", lambda *a, **k: client)
    provider = DeterministicResearchAI()

    async def scenario():
        stack = await build_production_pipeline_stack(
            AppSettings(database_url="sqlite://", redis_url="redis://localhost"),
            ai_providers=(provider, DeterministicResearchAI(provider_id="local-llama")),
            config=fast_config(),
        )
        try:
            assert stack.research.ai_router is stack.content.ai_router is stack.quality.ai_router
            assert len(stack.workers) == 10
            contracts = {(item.stream, item.consumer_group) for item in EVENT_CONSUMER_CONTRACTS}
            actual = {(w.consumer.stream, w.consumer.group) for w in stack.workers.values()}
            assert actual == contracts - {("news:publishing", "publisher")}
            assert isinstance(stack.collector.collector, RSSCollector)
            assert all(
                w.consumer.consumer.startswith("news-pipeline-") for w in stack.workers.values()
            )
            assert all(w.consumer.block_ms == 1 for w in stack.workers.values())
            assert not stack.owned_ai_providers
            acquisition = stack.owned_content_acquirer
            assert acquisition is not None
            acquisition.close = AsyncMock(wraps=acquisition.close)
            assert stack.workers["normalizer"].content_acquirer is acquisition
        finally:
            await stack.close()
            await stack.close()
        client.aclose.assert_awaited_once()
        acquisition.close.assert_awaited_once()

    asyncio.run(scenario())


def test_missing_required_ai_route_closes_startup_resources(monkeypatch):
    engine, client = Mock(), SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr("news_ai_pipeline.composition.create_database_engine", lambda *a: engine)
    monkeypatch.setattr("news_ai_pipeline.composition.Redis.from_url", lambda *a, **k: client)

    async def scenario():
        with pytest.raises(AIRoutingPolicyError):
            await build_production_pipeline_stack(
                AppSettings(database_url="sqlite://", redis_url="redis://localhost"),
                ai_providers=(),
                config=fast_config(),
            )
        client.aclose.assert_awaited_once()
        engine.dispose.assert_called_once()

    asyncio.run(scenario())


def test_invalid_redis_configuration_disposes_already_created_engine(monkeypatch):
    engine = Mock()
    monkeypatch.setattr("news_ai_pipeline.composition.create_database_engine", lambda *a: engine)

    async def scenario():
        with pytest.raises(ValueError):
            await build_production_pipeline_stack(
                AppSettings(database_url="sqlite://", redis_url="invalid"),
                ai_providers=(),
                config=fast_config(),
            )
        engine.dispose.assert_called_once()

    asyncio.run(scenario())


def test_postgres_startup_driver_connection_is_bounded(monkeypatch):
    engine, client = Mock(), SimpleNamespace(aclose=AsyncMock())
    captured = []

    def create(url):
        captured.append(url)
        return engine

    monkeypatch.setattr("news_ai_pipeline.composition.create_database_engine", create)
    monkeypatch.setattr("news_ai_pipeline.composition.Redis.from_url", lambda *a, **k: client)

    async def scenario():
        with pytest.raises(AIRoutingPolicyError):
            await build_production_pipeline_stack(
                AppSettings(
                    database_url="postgresql+psycopg://localhost/test",
                    redis_url="redis://localhost",
                    readiness_timeout_seconds=3.5,
                ),
                ai_providers=(),
                config=fast_config(),
            )
        assert captured[0].query["connect_timeout"] == "4"
        engine.dispose.assert_called_once()

    asyncio.run(scenario())


def test_cleanup_attempts_every_owned_resource_even_after_failure():
    broken = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("SECRET")))
    other = SimpleNamespace(close=AsyncMock())
    client, engine = SimpleNamespace(aclose=AsyncMock()), Mock()

    async def scenario():
        with pytest.raises(RuntimeError, match="pipeline resource cleanup unavailable") as caught:
            await _close_resources((broken, other), client, engine)
        assert "SECRET" not in str(caught.value)
        other.close.assert_awaited_once()
        client.aclose.assert_awaited_once()
        engine.dispose.assert_called_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["postgres", "redis", "group", None])
def test_production_dependency_readiness_checks_database_redis_and_all_groups(failure):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    if failure == "postgres":
        session.execute.side_effect = RuntimeError("SECRET")
    client = SimpleNamespace(ping=AsyncMock(return_value=failure != "redis"))
    worker = SimpleNamespace(ensure_ready=AsyncMock())
    if failure == "group":
        worker.ensure_ready.side_effect = RuntimeError("SECRET")
    stack = ProductionPipelineStack(
        settings=AppSettings(),
        config=fast_config(),
        engine=Mock(),
        session_factory=lambda: session,
        redis_client=client,
        collector=None,
        dispatcher=None,
        research=None,
        content=None,
        quality=None,
        workers={"normalizer": worker},
    )

    async def scenario():
        if failure:
            with pytest.raises(RuntimeError):
                await stack.ensure_ready()
        else:
            await stack.ensure_ready()
            session.execute.assert_called_once()
            client.ping.assert_awaited_once()
            worker.ensure_ready.assert_awaited_once()
        if failure == "postgres":
            client.ping.assert_not_awaited()
        if failure in ("postgres", "redis"):
            worker.ensure_ready.assert_not_awaited()

    asyncio.run(scenario())
