"""HTTP API entrypoint.

Liveness/readiness remain thin, while authenticated review routes delegate to the Stage-23
application service and PostgreSQL transaction boundary.
"""

import asyncio
import logging
from contextlib import suppress
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from news_ai_common.config import AppSettings
from news_ai_events import RedisStreamConsumer
from news_ai_publishing import PublicationError, PublicationService
from news_ai_review import (
    ApprovalEligibilityService,
    ReviewConfigurationError,
    ReviewError,
    ReviewService,
)
from news_ai_runtime.metrics import collect_metrics
from news_ai_social.config import SocialSettings, load_instagram_config
from redis.asyncio import Redis

from .auth import ReviewerTokenAuthenticator
from .dependencies import build_production_review_stack
from .publications import create_publication_router
from .readiness import ReadinessProbe, run_dependency_checks
from .review import create_review_router
from .telegram_review import (
    TELEGRAM_REVIEW_CONSUMER_GROUP,
    TelegramReviewController,
    TelegramReviewNotificationWorker,
    build_telegram_review_controller,
    create_telegram_review_router,
)


def create_app(
    settings: AppSettings | None = None,
    *,
    readiness_probe: ReadinessProbe = run_dependency_checks,
    review_service: ReviewService | None = None,
    reviewer_authenticator: ReviewerTokenAuthenticator | None = None,
    publication_service: PublicationService | None = None,
    telegram_review_controller: TelegramReviewController | None = None,
    metrics_probe=collect_metrics,
) -> FastAPI:
    resolved_settings = settings or AppSettings()
    application = FastAPI(title="News AI Social Media Manager", version="0.1.0")

    @application.exception_handler(PublicationError)
    async def publication_error(request: Request, exc: PublicationError) -> JSONResponse:
        status_code = {"PUBLICATION_NOT_FOUND": 404, "FORBIDDEN": 403, "INVALID_SCHEDULE": 422}.get(
            exc.code, 409
        )
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "request_id": str(request.state.request_id),
                }
            },
        )

    @application.middleware("http")
    async def request_identity(request: Request, call_next):
        request.state.request_id = uuid4()
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(request.state.request_id)
        return response

    @application.exception_handler(ReviewError)
    async def review_error(request: Request, exc: ReviewError) -> JSONResponse:
        status_code = 404 if exc.code == "REVIEW_ARTIFACT_NOT_FOUND" else 409
        if exc.code == "UNSUPPORTED_ARTIFACT_TYPE":
            status_code = 400
        elif exc.code == "REVIEW_VALIDATION_ERROR":
            status_code = 422
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "request_id": str(request.state.request_id),
                }
            },
        )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "error": {
                    "code": detail.get("code", "HTTP_ERROR"),
                    "message": detail.get("message", "Request failed"),
                    "request_id": str(request.state.request_id),
                }
            },
        )

    @application.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "request_id": str(request.state.request_id),
                }
            },
        )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/ready", response_model=None)
    async def ready() -> JSONResponse:
        dependencies = await readiness_probe(resolved_settings)
        postgres = dependencies.get("postgres", False)
        redis = dependencies.get("redis", False)
        ai_router = dependencies.get("ai_router", False)
        ready_state = postgres and redis and ai_router
        payload = {
            "status": "ready" if ready_state else "not_ready",
            "postgres": postgres,
            "redis": redis,
            "ai_router": ai_router,
        }
        return JSONResponse(status_code=200 if ready_state else 503, content=payload)

    @application.get("/metrics", response_model=None)
    async def metrics() -> Response:
        return Response(
            await metrics_probe(resolved_settings),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    resolved_review_service = review_service
    review_api_configured = (
        resolved_settings.review_api_token is not None and resolved_settings.reviewer_id is not None
    )
    telegram_review_configured = (
        resolved_settings.telegram_review_enabled
        and resolved_settings.telegram_bot_token is not None
        and resolved_settings.telegram_webhook_secret is not None
        and resolved_settings.telegram_review_chat_id is not None
        and resolved_settings.telegram_reviewer_user_id is not None
        and resolved_settings.reviewer_id is not None
    )
    if (
        resolved_review_service is None
        and resolved_settings.database_url
        and resolved_settings.environment != "test"
        and not (review_api_configured or telegram_review_configured)
    ):
        raise ReviewConfigurationError("production review authentication is not configured")
    if (
        resolved_review_service is None
        and resolved_settings.database_url
        and (review_api_configured or telegram_review_configured)
    ):
        review_stack = build_production_review_stack(resolved_settings)
        application.state.review_stack = review_stack
        resolved_review_service = review_stack.service
        reviewer_authenticator = review_stack.authenticator
        if publication_service is None and (
            review_api_configured or resolved_settings.telegram_auto_publish_on_approval
        ):
            publication_service = PublicationService(
                resolved_review_service.session_factory,
                ApprovalEligibilityService(
                    resolved_review_service.session_factory,
                    resolved_review_service.publishing_policy,
                ),
                platform_config=load_instagram_config(resolved_settings.config_dir),
            )
    if (
        publication_service is None
        and resolved_review_service is not None
        and resolved_settings.telegram_auto_publish_on_approval
    ):
        publication_service = PublicationService(
            resolved_review_service.session_factory,
            ApprovalEligibilityService(
                resolved_review_service.session_factory,
                resolved_review_service.publishing_policy,
            ),
            platform_config=load_instagram_config(resolved_settings.config_dir),
        )
    if resolved_review_service is not None and review_api_configured:
        authenticator = reviewer_authenticator or ReviewerTokenAuthenticator(resolved_settings)
        application.include_router(create_review_router(resolved_review_service, authenticator))
    if resolved_review_service is not None and telegram_review_configured:
        publication_account_identifier = None
        if resolved_settings.telegram_auto_publish_on_approval:
            social_settings = SocialSettings(environment=resolved_settings.environment)
            publication_account_identifier = social_settings.instagram_account_id
        controller = telegram_review_controller or build_telegram_review_controller(
            resolved_settings,
            resolved_review_service,
            publication_service=(
                publication_service if resolved_settings.telegram_auto_publish_on_approval else None
            ),
            publication_account_identifier=publication_account_identifier,
        )
        application.include_router(
            create_telegram_review_router(
                controller,
                resolved_settings.telegram_webhook_secret.get_secret_value(),
            )
        )
        if resolved_settings.telegram_review_push_enabled:
            redis_client = Redis.from_url(
                resolved_settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=resolved_settings.readiness_timeout_seconds,
            )
            worker = TelegramReviewNotificationWorker(
                RedisStreamConsumer(
                    redis_client,
                    stream="news:content",
                    group=TELEGRAM_REVIEW_CONSUMER_GROUP,
                    consumer=f"api-telegram-{uuid4().hex}",
                    block_ms=5_000,
                    count=10,
                ),
                resolved_review_service.session_factory,
                controller,
            )
            stop = asyncio.Event()

            async def notification_loop() -> None:
                cursor = "0-0"
                while not stop.is_set():
                    try:
                        cursor, _ = await worker.recover_once(min_idle_ms=60_000, start_id=cursor)
                        if not stop.is_set():
                            await worker.run_once()
                        if not stop.is_set():
                            await worker.recover_durable_once()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logging.getLogger(__name__).warning(
                            "Telegram review notifier unavailable; durable work retained"
                        )
                        with suppress(TimeoutError):
                            await asyncio.wait_for(stop.wait(), timeout=5)

            @application.on_event("startup")
            async def start_telegram_notifier() -> None:
                await worker.ensure_ready()
                application.state.telegram_notifier_task = asyncio.create_task(
                    notification_loop(), name="telegram-review-notifier"
                )

            @application.on_event("shutdown")
            async def stop_telegram_notifier() -> None:
                stop.set()
                task = getattr(application.state, "telegram_notifier_task", None)
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await redis_client.aclose()

        if resolved_settings.telegram_auto_publish_on_approval:
            publication_stop = asyncio.Event()

            async def publication_recovery_loop() -> None:
                while not publication_stop.is_set():
                    try:
                        await asyncio.to_thread(controller.recover_approved_publications)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logging.getLogger(__name__).warning(
                            "Approved publication recovery unavailable; durable work retained"
                        )
                    with suppress(TimeoutError):
                        await asyncio.wait_for(publication_stop.wait(), timeout=5)

            @application.on_event("startup")
            async def start_approved_publication_recovery() -> None:
                application.state.telegram_publication_recovery_task = asyncio.create_task(
                    publication_recovery_loop(), name="telegram-publication-recovery"
                )

            @application.on_event("shutdown")
            async def stop_approved_publication_recovery() -> None:
                publication_stop.set()
                task = getattr(application.state, "telegram_publication_recovery_task", None)
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    elif telegram_review_configured:
        raise ReviewConfigurationError("Telegram review database configuration is unavailable")
    if publication_service is not None and review_api_configured:
        application.include_router(
            create_publication_router(
                publication_service,
                reviewer_authenticator or ReviewerTokenAuthenticator(resolved_settings),
            )
        )

    return application


app = create_app()
