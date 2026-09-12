from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from news_ai_api.auth import ReviewerTokenAuthenticator
from news_ai_api.main import create_app
from news_ai_common.config import AppSettings
from news_ai_database import Base, ContentVariant, SocialAccount, SocialAccountStatus
from news_ai_domain import ReviewState
from news_ai_publishing import PublicationError, SchedulerConfig
from news_ai_scheduler import build_production_scheduler_stack
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unit.publishing.test_scheduler import Clock, request, seed_candidate, stack
from unit.review.test_review_service import _factory


def client(service, actor, capability="publish"):
    settings = AppSettings(
        environment="test",
        database_url="",
        reviewer_id=actor.reviewer_id,
        review_api_token="synthetic-review-token",
        review_capabilities=capability,
    )
    return TestClient(
        create_app(
            settings,
            publication_service=service,
            reviewer_authenticator=ReviewerTokenAuthenticator(settings),
        )
    )


def test_publication_api_auth_closed_request_safe_state_and_scheduling_only():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory, clock = sessionmaker(engine, expire_on_commit=False), Clock()
    variant, account, actor = seed_candidate(factory)
    service, scheduler = stack(factory, clock)
    body = request(variant, account, clock).model_dump(mode="json")
    api = client(service, actor)
    assert api.post("/api/v1/publications", json=body).status_code == 401
    headers = {"Authorization": "Bearer synthetic-review-token"}
    assert (
        client(service, actor, "approve,review,view")
        .post("/api/v1/publications", json=body, headers=headers)
        .status_code
        == 403
    )
    response = api.post("/api/v1/publications", json=body, headers=headers)
    assert response.status_code == 201
    data = response.json()
    assert "credential_reference" not in data and "external_post_id" not in data
    identifier = data["id"]
    assert api.get(f"/api/v1/publications/{identifier}", headers=headers).status_code == 200
    assert (
        api.post(
            "/api/v1/publications", json={**body, "status": "PUBLISHED"}, headers=headers
        ).status_code
        == 422
    )
    assert (
        api.post(f"/api/v1/publications/{identifier}/publish-now", headers=headers).status_code
        == 200
    )
    assert scheduler.scan() == 1
    assert (
        api.post(
            f"/api/v1/publications/{identifier}/cancel",
            json={"reason": "Do not publish"},
            headers=headers,
        ).json()["status"]
        == "CANCELLED"
    )
    assert api.post(f"/api/v1/publications/{identifier}/retry", headers=headers).status_code == 404
    assert (
        api.get(f"/api/v1/publications/{identifier}/attempts", headers=headers).status_code == 404
    )
    missing = api.get(f"/api/v1/publications/{uuid4()}", headers=headers)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "PUBLICATION_NOT_FOUND"
    assert "synthetic-review-token" not in str(data)


@pytest.mark.parametrize(
    "state", [ReviewState.READY_FOR_REVIEW, ReviewState.REJECTED, ReviewState.CHANGES_REQUESTED]
)
def test_nonapproved_content_is_not_schedulable(state):
    factory, clock = _factory(), Clock()
    variant, account, actor = seed_candidate(factory)
    service, _ = stack(factory, clock)
    with factory() as session, session.begin():
        session.get(ContentVariant, variant).review_state = state
    with pytest.raises(PublicationError) as exc:
        service.create(request(variant, account, clock), actor)
    assert exc.value.code == "CONTENT_NOT_APPROVED"


@pytest.mark.parametrize("mutation", ["paused", "platform", "capability"])
def test_account_must_be_active_matching_and_capable(mutation):
    factory, clock = _factory(), Clock()
    variant, account, actor = seed_candidate(factory)
    service, _ = stack(factory, clock)
    with factory() as session, session.begin():
        destination = session.get(SocialAccount, account)
        if mutation == "paused":
            destination.status = SocialAccountStatus.PAUSED
        elif mutation == "platform":
            destination.platform = "TELEGRAM"
        else:
            destination.capabilities = {"image": True}
    with pytest.raises(PublicationError):
        service.create(request(variant, account, clock), actor)


def test_scheduler_composition_uses_repository_config_without_social_credentials(monkeypatch):
    factory, clock = _factory(), Clock()
    monkeypatch.delenv("NEWS_AI_PUBLISHING_PAUSED", raising=False)
    monkeypatch.delenv("NEWS_AI_INSTAGRAM_ACCESS_TOKEN", raising=False)
    composed = build_production_scheduler_stack(
        AppSettings(environment="test", config_dir="config", database_url=""),
        clock=clock,
        session_factory=factory,
    )
    assert composed.scheduler.config.publishing_paused is True
    assert not hasattr(composed, "adapter")
    assert composed.scheduler.scan() == 0
    monkeypatch.setenv("NEWS_AI_PUBLISHING_PAUSED", "not-a-boolean")
    with pytest.raises(ValueError):
        build_production_scheduler_stack(
            AppSettings(environment="test", config_dir="config", database_url=""),
            session_factory=factory,
        )
    with pytest.raises(ValidationError):
        SchedulerConfig(poll_interval_seconds=0, batch_size=1, publishing_paused=False)
    with pytest.raises(ValidationError):
        SchedulerConfig(
            poll_interval_seconds=30, batch_size=1, publishing_paused=False, unknown="ignored"
        )
