from pathlib import Path

from fastapi.testclient import TestClient

from news_ai_api.main import create_app
from news_ai_common.config import AppSettings


def test_health_is_live(tmp_path: Path) -> None:
    client = TestClient(create_app(AppSettings(config_dir=tmp_path)))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_requires_config_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    client = TestClient(create_app(AppSettings(config_dir=missing)))

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["configuration"] is False
