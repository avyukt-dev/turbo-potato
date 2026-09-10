"""HTTP API entrypoint.

Only liveness/readiness are implemented in the bootstrap batches. Business endpoints are added as
persistence and event-backed application services become available.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from news_ai_common.config import AppSettings
from news_ai_common.runtime import RuntimeDetector

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
        config_ready = resolved_settings.config_dir.is_dir()
        runtime = RuntimeDetector(override=resolved_settings.service_manager).inspect()
        dependencies = await readiness_probe(resolved_settings)
        checks: dict[str, object] = {
            "configuration": config_ready,
            "postgres": dependencies.get("postgres", False),
            "redis": dependencies.get("redis", False),
            "runtime_service_manager": runtime.service_manager,
        }
        ready_state = config_ready and bool(checks["postgres"]) and bool(checks["redis"])
        payload: dict[str, object] = {
            "status": "ready" if ready_state else "not_ready",
            "checks": checks,
        }
        return JSONResponse(status_code=200 if ready_state else 503, content=payload)

    return application


app = create_app()
