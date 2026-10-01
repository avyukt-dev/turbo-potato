from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from news_ai_api.main import create_app
from news_ai_api.telegram_review import (
    TelegramBotTransport,
    TelegramReviewController,
    TelegramUpdate,
    create_telegram_review_router,
)
from news_ai_common.config import AppSettings
from news_ai_domain import ReviewState, RiskLevel
from news_ai_review import (
    ArtifactType,
    ReviewActionResult,
    ReviewCapability,
    ReviewConfigurationError,
    ReviewConflictError,
    ReviewerPrincipal,
    ReviewQueueItem,
    ReviewQueuePage,
)
from pydantic import ValidationError

_REVIEWER_ID = UUID("11111111-1111-1111-1111-111111111111")
_TELEGRAM_USER_ID = 123456
_CHAT_ID = -100123456


class FakeTelegramTransport:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.photos: list[dict] = []
        self.answers: list[dict] = []
        self.cleared: list[dict] = []
        self.fail_photo_at: int | None = None

    async def send_message(self, **arguments) -> None:
        self.messages.append(arguments)

    async def send_photo(self, **arguments) -> None:
        if self.fail_photo_at == len(self.photos) + 1:
            raise RuntimeError("normalized Telegram media failure")
        self.photos.append(arguments)

    async def answer_callback_query(self, **arguments) -> None:
        self.answers.append(arguments)

    async def clear_reply_markup(self, **arguments) -> None:
        self.cleared.append(arguments)


class FakeReviewService:
    def __init__(self, variant_id: UUID | None = None) -> None:
        self.variant_id = variant_id or uuid4()
        self.decisions: list[dict] = []
        self.raise_conflict = False

    def queue(self, **_filters) -> ReviewQueuePage:
        return ReviewQueuePage(
            items=(
                ReviewQueueItem(
                    artifact_type=ArtifactType.CONTENT_VARIANT,
                    artifact_id=self.variant_id,
                    artifact_version=2,
                    content_draft_id=uuid4(),
                    story_id=uuid4(),
                    fact_sheet_id=uuid4(),
                    fact_sheet_version=1,
                    title="Reviewed title",
                    platform="INSTAGRAM",
                    format="CAROUSEL",
                    language="en",
                    risk_level=RiskLevel.HIGH,
                    sensitive_topics=("PUBLIC_SAFETY",),
                    review_state=ReviewState.READY_FOR_REVIEW,
                    review_ready_at=datetime(2026, 9, 23, tzinfo=UTC),
                ),
            ),
            offset=0,
            limit=1,
            total=1,
        )

    def detail(self, **_arguments):
        return SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "artifact_id": str(self.variant_id),
                "artifact_version": 2,
                "risk_level": "HIGH",
                "sensitive_topics": ["PUBLIC_SAFETY"],
                "current_review_state": "READY_FOR_REVIEW",
                "current_reviewable": True,
                "content_variant": {
                    "content_variant_id": str(self.variant_id),
                    "version": 2,
                    "title": "Reviewed title",
                    "body": "Slide one heading\nSlide-only consequential statement",
                    "caption": "Reviewed caption",
                    "structured_payload": {
                        "slides": [
                            {
                                "position": 1,
                                "heading": "Slide one heading",
                                "body": "Slide-only consequential statement",
                                "claim_ids": ["22222222-2222-2222-2222-222222222222"],
                            }
                        ],
                        "hashtags": ["#reviewed", "#publicsafety"],
                    },
                    "claim_ids_used": ["22222222-2222-2222-2222-222222222222"],
                    "source_ids_used": ["33333333-3333-3333-3333-333333333333"],
                    "media_asset_ids": ["44444444-4444-4444-4444-444444444444"],
                    "media_provenance": [
                        {
                            "id": "44444444-4444-4444-4444-444444444444",
                            "file_hash": "a" * 64,
                            "asset_type": "IMAGE",
                            "mime_type": "image/jpeg",
                            "media_format": "JPEG",
                            "public_url": "https://media.example/reviewed.jpg",
                            "visual_check_status": "PASSED",
                        }
                    ],
                },
                "fact_sheet": {
                    "headline": "Canonical headline",
                    "summary": "Canonical summary",
                    "claims": [
                        {
                            "claim_id": "22222222-2222-2222-2222-222222222222",
                            "status": "UNVERIFIED",
                            "claim_text": "An uncertain claim",
                            "evidence_ids": ["55555555-5555-5555-5555-555555555555"],
                            "contradictory_evidence_ids": ["66666666-6666-6666-6666-666666666666"],
                        }
                    ],
                    "evidence": [
                        {
                            "evidence_id": "55555555-5555-5555-5555-555555555555",
                            "relation": "DIRECT_SUPPORT",
                            "excerpt": "Supporting evidence excerpt",
                        },
                        {
                            "evidence_id": "66666666-6666-6666-6666-666666666666",
                            "relation": "CONTRADICTS",
                            "excerpt": "Contradictory evidence excerpt",
                        },
                    ],
                    "unresolved_questions": ["What remains unknown?"],
                    "sources": [
                        {
                            "source_id": "33333333-3333-3333-3333-333333333333",
                            "name": "Example News",
                            "source_level": 2,
                            "url": "https://example.com/report",
                        },
                        {
                            "source_id": "33333333-3333-3333-3333-333333333333",
                            "name": "Example News duplicate",
                            "source_level": 2,
                            "url": "https://example.com/report",
                        },
                        {
                            "source_id": "77777777-7777-7777-7777-777777777777",
                            "name": "Unrelated source",
                            "source_level": 2,
                            "url": "https://unrelated.example/report",
                        },
                    ],
                },
                "editorial_brief": {"human_review_required": True},
                "quality_check": {
                    "passed": True,
                    "semantic_validation_passed": True,
                    "semantic_findings": [
                        {
                            "severity": "WARNING",
                            "code": "AUDIT_WARNING",
                            "message": "Reviewer-visible quality warning",
                        }
                    ],
                    "notes": "Quality review notes",
                },
                "generation_ai_provenance": {
                    "provider": "fake",
                    "model": "deterministic",
                    "prompt_version": "v2",
                },
                "quality_ai_provenance": None,
            }
        )

    def decide(self, **arguments) -> ReviewActionResult:
        if self.raise_conflict:
            raise ReviewConflictError("stale review")
        self.decisions.append(arguments)
        return ReviewActionResult(
            review_decision_id=uuid4(),
            artifact_type=ArtifactType.CONTENT_VARIANT,
            artifact_id=arguments["artifact_id"],
            artifact_version=arguments["request"].artifact_version,
            decision=arguments["decision"],
            reviewer_id=arguments["principal"].reviewer_id,
            decided_at=datetime(2026, 9, 23, tzinfo=UTC),
            current_review_state=arguments["decision"],
        )


def _controller(service=None, transport=None, *, capabilities=None):
    service = service or FakeReviewService()
    transport = transport or FakeTelegramTransport()
    controller = TelegramReviewController(
        service,
        transport,
        chat_id=_CHAT_ID,
        reviewer_user_id=_TELEGRAM_USER_ID,
        principal=ReviewerPrincipal(
            reviewer_id=_REVIEWER_ID,
            capabilities=capabilities
            or frozenset(
                {ReviewCapability.VIEW, ReviewCapability.REVIEW, ReviewCapability.APPROVE}
            ),
        ),
    )
    return controller, service, transport


def _message_update(*, user_id: int = _TELEGRAM_USER_ID, text: str = "/review") -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "from": {"id": user_id},
            "chat": {"id": _CHAT_ID, "type": "supergroup"},
            "text": text,
        },
    }


def _callback_update(variant_id: UUID, *, user_id: int = _TELEGRAM_USER_ID) -> dict:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "callback-1",
            "from": {"id": user_id},
            "message": {
                "message_id": 11,
                "chat": {"id": _CHAT_ID, "type": "supergroup"},
            },
            "data": f"approve:{variant_id.hex}:2",
        },
    }


def test_review_command_returns_publication_preview_sources_and_exact_version_button() -> None:
    controller, service, transport = _controller()
    asyncio.run(controller.handle(TelegramUpdate.model_validate(_message_update())))

    rendered = "\n".join(message["text"] for message in transport.messages)
    assert "Publication preview" in rendered
    assert "Reviewed title" in rendered
    assert "Slide one heading" in rendered
    assert "Slide-only consequential statement" in rendered
    assert "Reviewed caption" in rendered
    assert "#publicsafety" in rendered
    assert "Sources" in rendered
    assert "• Example News: https://example.com/report" in rendered
    assert rendered.count("https://example.com/report") == 1
    assert "https://unrelated.example/report" not in rendered
    assert str(service.variant_id) not in rendered
    assert "Canonical headline" not in rendered
    assert "Reviewer-visible quality warning" not in rendered
    assert "deterministic" not in rendered
    assert "content_variant_id" not in rendered
    assert "{" not in rendered
    assert transport.photos == [
        {
            "chat_id": _CHAT_ID,
            "photo": "https://media.example/reviewed.jpg",
            "caption": "Media preview 1/1",
        }
    ]
    assert "a" * 64 not in transport.photos[0]["caption"]
    assert all("reply_markup" not in message for message in transport.messages[:-1])
    message = transport.messages[-1]
    assert message["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == (
        f"approve:{service.variant_id.hex}:2"
    )


def test_review_packet_rejects_credential_bearing_source_url() -> None:
    controller, service, transport = _controller()
    original_detail = service.detail

    def invalid_detail(**arguments):
        payload = original_detail(**arguments).model_dump()
        payload["fact_sheet"]["sources"][0]["url"] = "https://user:secret@example.com/report"
        return SimpleNamespace(model_dump=lambda **_kwargs: payload)

    service.detail = invalid_detail
    with pytest.raises(ValueError, match="invalid source URL"):
        asyncio.run(controller.handle(TelegramUpdate.model_validate(_message_update())))

    assert transport.messages == []
    assert transport.photos == []


def test_review_packet_is_losslessly_chunked_with_button_only_at_end() -> None:
    controller, service, transport = _controller()
    original_detail = service.detail

    def long_detail(**arguments):
        detail = original_detail(**arguments)
        payload = detail.model_dump()
        marker = "exact-boundary-content-"
        payload["content_variant"]["structured_payload"]["slides"][0]["body"] = marker + (
            "x" * 9000
        )
        return SimpleNamespace(model_dump=lambda **_kwargs: payload)

    service.detail = long_detail
    asyncio.run(controller.handle(TelegramUpdate.model_validate(_message_update())))

    assert all(len(message["text"]) <= 4096 for message in transport.messages)
    rendered = "\n".join(message["text"] for message in transport.messages)
    assert "exact-boundary-content-" in rendered
    assert rendered.count("x") >= 9000
    assert all("reply_markup" not in message for message in transport.messages[:-1])
    assert "reply_markup" in transport.messages[-1]


def test_media_preview_failure_never_exposes_approval_action() -> None:
    transport = FakeTelegramTransport()
    transport.fail_photo_at = 1
    controller, _service, transport = _controller(transport=transport)

    with pytest.raises(RuntimeError, match="normalized Telegram media failure"):
        asyncio.run(controller.handle(TelegramUpdate.model_validate(_message_update())))

    assert transport.photos == []
    assert transport.messages
    assert all("reply_markup" not in message for message in transport.messages)


def test_inconsistent_media_provenance_fails_before_packet_delivery() -> None:
    controller, service, transport = _controller()
    original_detail = service.detail

    def invalid_detail(**arguments):
        detail = original_detail(**arguments)
        payload = detail.model_dump()
        payload["content_variant"]["media_provenance"][0]["id"] = str(uuid4())
        return SimpleNamespace(model_dump=lambda **_kwargs: payload)

    service.detail = invalid_detail
    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(controller.handle(TelegramUpdate.model_validate(_message_update())))

    assert transport.messages == []
    assert transport.photos == []


def test_authorized_callback_uses_canonical_exact_version_review_service() -> None:
    controller, service, transport = _controller()
    asyncio.run(
        controller.handle(TelegramUpdate.model_validate(_callback_update(service.variant_id)))
    )

    assert len(service.decisions) == 1
    decision = service.decisions[0]
    assert decision["artifact_id"] == service.variant_id
    assert decision["request"].artifact_version == 2
    assert decision["decision"] is ReviewState.APPROVED
    assert decision["principal"].reviewer_id == _REVIEWER_ID
    assert decision["idempotency_key"] == ("telegram:" + hashlib.sha256(b"callback-1").hexdigest())
    assert transport.cleared == [{"chat_id": _CHAT_ID, "message_id": 11}]
    assert transport.answers[0]["text"] == "Exact content version approved."


def test_unauthorized_user_cannot_view_or_approve() -> None:
    controller, service, transport = _controller()
    asyncio.run(controller.handle(TelegramUpdate.model_validate(_message_update(user_id=999999))))
    asyncio.run(
        controller.handle(
            TelegramUpdate.model_validate(_callback_update(service.variant_id, user_id=999999))
        )
    )
    assert service.decisions == []
    assert transport.messages == []
    assert transport.answers[0]["text"] == "This reviewer is not authorized."


def test_stale_callback_is_not_approved_and_keeps_button_for_refresh() -> None:
    controller, service, transport = _controller()
    service.raise_conflict = True
    asyncio.run(
        controller.handle(TelegramUpdate.model_validate(_callback_update(service.variant_id)))
    )
    assert service.decisions == []
    assert transport.cleared == []
    assert "not applied" in transport.answers[0]["text"]


def test_webhook_requires_constant_secret_and_never_exposes_it() -> None:
    controller, _service, transport = _controller()
    app = FastAPI()
    app.include_router(create_telegram_review_router(controller, "telegram-webhook-secret"))
    client = TestClient(app)

    missing = client.post("/api/v1/integrations/telegram/review/webhook", json=_message_update())
    wrong = client.post(
        "/api/v1/integrations/telegram/review/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        json=_message_update(),
    )
    accepted = client.post(
        "/api/v1/integrations/telegram/review/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-webhook-secret"},
        json=_message_update(),
    )
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 200
    assert len(transport.messages) > 1
    assert "telegram-webhook-secret" not in missing.text + wrong.text + accepted.text


def test_enabled_telegram_review_requires_complete_typed_configuration() -> None:
    with pytest.raises(ValidationError, match="configuration is incomplete"):
        AppSettings(telegram_review_enabled=True)
    with pytest.raises(ValidationError, match="unsupported characters"):
        AppSettings(telegram_webhook_secret="invalid secret with spaces")

    settings = AppSettings(
        database_url=None,
        telegram_review_enabled=True,
        telegram_bot_token="123456:secret",
        telegram_webhook_secret="valid_webhook-secret",
        telegram_review_chat_id=-100123,
        telegram_reviewer_user_id=123456,
        reviewer_id=_REVIEWER_ID,
    )
    assert settings.telegram_review_enabled is True


def test_api_composes_telegram_review_without_exposing_bearer_review_routes() -> None:
    controller, service, transport = _controller()
    settings = AppSettings(
        telegram_review_enabled=True,
        telegram_bot_token="123456:secret",
        telegram_webhook_secret="telegram-secret",
        telegram_review_chat_id=_CHAT_ID,
        telegram_reviewer_user_id=_TELEGRAM_USER_ID,
        reviewer_id=_REVIEWER_ID,
    )
    app = create_app(
        settings,
        review_service=service,  # type: ignore[arg-type]
        telegram_review_controller=controller,
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/integrations/telegram/review/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json=_message_update(),
    )
    assert response.status_code == 200
    assert len(transport.messages) > 1
    assert "reply_markup" in transport.messages[-1]
    assert client.get("/api/v1/review/queue").status_code == 404


def test_enabled_telegram_review_fails_closed_without_review_database() -> None:
    settings = AppSettings(
        database_url=None,
        telegram_review_enabled=True,
        telegram_bot_token="123456:secret",
        telegram_webhook_secret="telegram-secret",
        telegram_review_chat_id=_CHAT_ID,
        telegram_reviewer_user_id=_TELEGRAM_USER_ID,
        reviewer_id=_REVIEWER_ID,
    )
    with pytest.raises(ReviewConfigurationError, match="database configuration"):
        create_app(settings)


def test_bot_transport_normalizes_failure_without_secret_leakage(monkeypatch) -> None:
    import httpx

    secret = "123456:SUPER_SECRET_TELEGRAM_TOKEN"

    class BrokenClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_arguments):
            return None

        async def post(self, url, **_arguments):
            request = httpx.Request("POST", url)
            raise httpx.ConnectError(f"failed request to {url}", request=request)

    monkeypatch.setattr(
        "news_ai_api.telegram_review.httpx.AsyncClient",
        lambda **_arguments: BrokenClient(),
    )
    transport = TelegramBotTransport(secret)
    with pytest.raises(RuntimeError, match="Bot API is unavailable") as caught:
        asyncio.run(transport.send_message(chat_id=_CHAT_ID, text="review"))
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
