"""HTTP API entrypoint.

Only liveness/readiness are implemented in the bootstrap batch. Business endpoints are added after
persistence and event infrastructure exist.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from news_ai_common.config import AppSettings
from news_ai_common.runtime import RuntimeDetector


def create_app(settings: AppSettings | None = None) -> FastAPI:
    resolved_settings = settings or AppSettings()
    application = FastAPI(title="News AI Social Media Manager", version="0.1.0")

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/ready", response_model=None)
    async def ready() -> JSONResponse:
        config_ready = resolved_settings.config_dir.is_dir()
        runtime = RuntimeDetector().inspect()
        payload: dict[str, object] = {
            "status": "ready" if config_ready else "not_ready",
            "checks": {
                "configuration": config_ready,
                "runtime_service_manager": runtime.service_manager,
            },
        }
        return JSONResponse(status_code=200 if config_ready else 503, content=payload)

    return application


app = create_app()
