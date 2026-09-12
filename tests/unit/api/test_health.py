from pathlib import Path

from fastapi.testclient import TestClient
from news_ai_api.main import create_app
from news_ai_api.readiness import _check_ai_router
from news_ai_common.config import AppSettings


async def healthy_dependencies(_settings: AppSettings) -> dict[str, bool]:
    return {"postgres": True, "redis": True, "ai_router": True}


async def unhealthy_redis(_settings: AppSettings) -> dict[str, bool]:
    return {"postgres": True, "redis": False, "ai_router": True}


def test_health_is_live(tmp_path: Path) -> None:
    client = TestClient(
        create_app(AppSettings(config_dir=tmp_path), readiness_probe=healthy_dependencies)
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_requires_ai_router(tmp_path: Path) -> None:
    async def unhealthy_router(_settings: AppSettings) -> dict[str, bool]:
        return {"postgres": True, "redis": True, "ai_router": False}

    client = TestClient(
        create_app(AppSettings(config_dir=tmp_path), readiness_probe=unhealthy_router)
    )

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "postgres": True,
        "redis": True,
        "ai_router": False,
    }


def test_ready_requires_postgres_and_redis(tmp_path: Path) -> None:
    client = TestClient(
        create_app(AppSettings(config_dir=tmp_path), readiness_probe=unhealthy_redis)
    )

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["postgres"] is True
    assert response.json()["redis"] is False


def test_ready_requires_postgres_without_leaking_dependency_errors(tmp_path: Path) -> None:
    async def unhealthy_postgres(_settings: AppSettings) -> dict[str, bool]:
        return {"postgres": False, "redis": True, "ai_router": True}

    client = TestClient(
        create_app(AppSettings(config_dir=tmp_path), readiness_probe=unhealthy_postgres)
    )

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "postgres": False,
        "redis": True,
        "ai_router": True,
    }
    assert "password" not in response.text.casefold()


def test_ready_when_all_required_dependencies_are_healthy(tmp_path: Path) -> None:
    client = TestClient(
        create_app(AppSettings(config_dir=tmp_path), readiness_probe=healthy_dependencies)
    )

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "postgres": True,
        "redis": True,
        "ai_router": True,
    }


def test_ai_router_readiness_requires_content_generation_route(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    (models / "providers.yaml").write_text(
        """schema_version: 1
llama_cpp:
  - provider_id: local-llama
    base_url: http://127.0.0.1:8080
    model: local-news-ai
    task_types: [CLAIM_EXTRACTION, EVIDENCE_ASSESSMENT, CONTENT_GENERATION]
    request_timeout_seconds: 120
    health_timeout_seconds: 2
    max_context_tokens: 8192
""",
        encoding="utf-8",
    )
    (models / "routing.yaml").write_text(
        """schema_version: 1
mode: LOCAL
routes:
  CLAIM_EXTRACTION: {providers: [local-llama]}
  EVIDENCE_ASSESSMENT: {providers: [local-llama]}
sensitivity_provider_allowlists: {}
""",
        encoding="utf-8",
    )
    assert _check_ai_router(AppSettings(config_dir=tmp_path)) is False


def test_canonical_ai_router_readiness_resolves_all_stage21_tasks() -> None:
    assert _check_ai_router(AppSettings(config_dir=Path("config"))) is True
