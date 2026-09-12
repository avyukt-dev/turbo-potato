from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from news_ai_api.auth import ReviewerTokenAuthenticator
from news_ai_api.dependencies import build_production_review_stack
from news_ai_api.main import create_app
from news_ai_common.config import AppSettings
from news_ai_review import ReviewActionResult, ReviewConfigurationError, ReviewQueuePage
from pydantic import ValidationError


class FakeReviewService:
    def __init__(self) -> None:
        self.decisions: list[dict] = []

    def queue(self, **_filters) -> ReviewQueuePage:
        return ReviewQueuePage(items=(), offset=0, limit=50, total=0)

    def detail(self, **_arguments):  # pragma: no cover - action tests do not use detail
        raise AssertionError("unexpected detail call")

    def decide(self, **arguments) -> ReviewActionResult:
        self.decisions.append(arguments)
        return ReviewActionResult(
            review_decision_id=uuid4(),
            artifact_type="content_variant",
            artifact_id=arguments["artifact_id"],
            artifact_version=arguments["request"].artifact_version,
            decision=arguments["decision"],
            reviewer_id=arguments["principal"].reviewer_id,
            reason=arguments["request"].reason,
            decided_at=datetime(2026, 9, 12, tzinfo=UTC),
            current_review_state=arguments["decision"],
        )


def _client(capabilities: str = "view,review,approve") -> tuple[TestClient, FakeReviewService]:
    service = FakeReviewService()
    settings = AppSettings(
        config_dir=Path("config"),
        review_api_token="super-secret-review-token",
        reviewer_id=UUID("11111111-1111-1111-1111-111111111111"),
        review_capabilities=capabilities,
    )
    app = create_app(
        settings,
        review_service=service,  # type: ignore[arg-type]
        reviewer_authenticator=ReviewerTokenAuthenticator(settings),
    )
    return TestClient(app), service


def _headers(key: str = "operation-1") -> dict[str, str]:
    return {
        "Authorization": "Bearer super-secret-review-token",
        "Idempotency-Key": key,
    }


def test_review_authentication_and_queue_authorization() -> None:
    client, _service = _client()
    missing = client.get("/api/v1/review/queue")
    invalid = client.get("/api/v1/review/queue", headers={"Authorization": "Bearer wrong-token"})
    valid = client.get("/api/v1/review/queue", headers=_headers())
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code == 200
    assert "super-secret-review-token" not in (missing.text + invalid.text + valid.text)


@pytest.mark.parametrize(
    ("path", "capabilities"),
    [
        ("approve", "view,review"),
        ("reject", "view,approve"),
        ("request-changes", "view,approve"),
    ],
)
def test_review_actions_enforce_capabilities(path: str, capabilities: str) -> None:
    client, service = _client(capabilities)
    response = client.post(
        f"/api/v1/review/content_variant/{uuid4()}/{path}",
        headers=_headers(),
        json={"artifact_version": 1, "reason": "Reason where required"},
    )
    assert response.status_code == 403
    assert service.decisions == []


def test_approve_uses_server_principal_and_rejects_client_identity_fields() -> None:
    client, service = _client()
    variant_id = uuid4()
    valid = client.post(
        f"/api/v1/review/content_variant/{variant_id}/approve",
        headers=_headers(),
        json={"artifact_version": 1},
    )
    injected = client.post(
        f"/api/v1/review/content_variant/{variant_id}/approve",
        headers=_headers("operation-2"),
        json={"artifact_version": 1, "reviewer_id": str(uuid4())},
    )
    assert valid.status_code == 200
    assert valid.json()["reviewer_id"] == "11111111-1111-1111-1111-111111111111"
    assert injected.status_code == 422
    assert len(service.decisions) == 1


def test_authenticator_uses_constant_time_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings(
        review_api_token="token",
        reviewer_id=uuid4(),
        review_capabilities="view",
    )
    authenticator = ReviewerTokenAuthenticator(settings)
    observed: list[tuple[bytes, bytes]] = []

    def compared(left: bytes, right: bytes) -> bool:
        observed.append((left, right))
        return left == right

    monkeypatch.setattr("news_ai_api.auth.hmac.compare_digest", compared)
    from fastapi.security import HTTPAuthorizationCredentials

    principal = authenticator.authenticate(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    )
    assert principal.reviewer_id == settings.reviewer_id
    assert observed == [(b"token", b"token")]


def test_invalid_reviewer_uuid_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEWS_AI_REVIEWER_ID", "not-a-uuid")
    with pytest.raises(ValidationError):
        AppSettings()


def test_production_review_composition_requires_auth_and_builds_services() -> None:
    with pytest.raises(ReviewConfigurationError):
        build_production_review_stack(
            AppSettings(database_url="postgresql+psycopg://localhost/news_ai")
        )
    stack = build_production_review_stack(
        AppSettings(
            config_dir=Path("config"),
            database_url="postgresql+psycopg://localhost/news_ai",
            review_api_token="token",
            reviewer_id=uuid4(),
        )
    )
    assert stack.service is not None
    assert stack.eligibility is not None
    stack.engine.dispose()
