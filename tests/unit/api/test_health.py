from pathlib import Path

from fastapi.testclient import TestClient
from news_ai_api.main import create_app
from news_ai_common.config import AppSettings


async def healthy_dependencies(_settings: AppSettings) -> dict[str, bool]:
    return {"postgres": True, "redis": True}


async def unhealthy_redis(_settings: AppSettings) -> dict[str, bool]:
    return {"postgres": True, "redis": False}


def test_health_is_live(tmp_path: Path) -> None:
    client = TestClient(
        create_app(AppSettings(config_dir=tmp_path), readiness_probe=healthy_dependencies)
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_requires_config_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    client = TestClient(
        create_app(AppSettings(config_dir=missing), readiness_probe=healthy_dependencies)
    )

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["configuration"] is False


def test_ready_requires_postgres_and_redis(tmp_path: Path) -> None:
    client = TestClient(
        create_app(AppSettings(config_dir=tmp_path), readiness_probe=unhealthy_redis)
    )

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["postgres"] is True
    assert response.json()["checks"]["redis"] is False


def test_ready_when_all_required_dependencies_are_healthy(tmp_path: Path) -> None:
    client = TestClient(
        create_app(AppSettings(config_dir=tmp_path), readiness_probe=healthy_dependencies)
    )

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
