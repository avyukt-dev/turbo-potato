"""HTTP API entrypoint.

Only liveness/readiness are implemented in the bootstrap batches. Business endpoints are added as
persistence and event-backed application services become available.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from news_ai_common.config import AppSettings

from .readiness import ReadinessProbe, run_dependency_checks


def create_app(
    settings: AppSettings | None = None,
    *,
    readiness_probe: ReadinessProbe = run_dependency_checks,
) -> FastAPI:
    resolved_settings = settings or AppSettings()
    application = FastAPI(title="News AI Social Media Manager", version="0.1.0")

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

    return application


app = create_app()
