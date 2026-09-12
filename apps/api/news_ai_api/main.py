"""HTTP API entrypoint.

Liveness/readiness remain thin, while authenticated review routes delegate to the Stage-23
application service and PostgreSQL transaction boundary.
"""

from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from news_ai_common.config import AppSettings
from news_ai_publishing import PublicationError, PublicationService
from news_ai_review import (
    ApprovalEligibilityService,
    ReviewConfigurationError,
    ReviewError,
    ReviewService,
)
from news_ai_social.config import load_instagram_config

from .auth import ReviewerTokenAuthenticator
from .dependencies import build_production_review_stack
from .publications import create_publication_router
from .readiness import ReadinessProbe, run_dependency_checks
from .review import create_review_router


def create_app(
    settings: AppSettings | None = None,
    *,
    readiness_probe: ReadinessProbe = run_dependency_checks,
    review_service: ReviewService | None = None,
    reviewer_authenticator: ReviewerTokenAuthenticator | None = None,
    publication_service: PublicationService | None = None,
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

    resolved_review_service = review_service
    review_auth_configured = (
        resolved_settings.review_api_token is not None and resolved_settings.reviewer_id is not None
    )
    if (
        resolved_review_service is None
        and resolved_settings.database_url
        and resolved_settings.environment != "test"
        and not review_auth_configured
    ):
        raise ReviewConfigurationError("production review authentication is not configured")
    if (
        resolved_review_service is None
        and resolved_settings.database_url
        and review_auth_configured
    ):
        review_stack = build_production_review_stack(resolved_settings)
        application.state.review_stack = review_stack
        resolved_review_service = review_stack.service
        reviewer_authenticator = review_stack.authenticator
        if publication_service is None:
            publication_service = PublicationService(
                resolved_review_service.session_factory,
                ApprovalEligibilityService(
                    resolved_review_service.session_factory,
                    resolved_review_service.publishing_policy,
                ),
                platform_config=load_instagram_config(resolved_settings.config_dir),
            )
    if resolved_review_service is not None:
        authenticator = reviewer_authenticator or ReviewerTokenAuthenticator(resolved_settings)
        application.include_router(create_review_router(resolved_review_service, authenticator))
    if publication_service is not None:
        application.include_router(
            create_publication_router(
                publication_service,
                reviewer_authenticator or ReviewerTokenAuthenticator(resolved_settings),
            )
        )

    return application


app = create_app()
