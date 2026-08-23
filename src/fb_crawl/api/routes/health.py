from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from fb_crawl.api.safe_logging import log_readiness_failure


REQUIRED_MIGRATION = "003_job_orchestration"
NOT_READY_BODY = {
    "code": "api_not_ready",
    "message": "API is not ready.",
}

logger = logging.getLogger(__name__)

ReadinessCheck = Callable[[str], bool]


def create_health_router(readiness: ReadinessCheck) -> APIRouter:
    router = APIRouter()

    @router.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health/ready", response_model=None)
    def ready() -> dict[str, str] | JSONResponse:
        try:
            is_ready = readiness(REQUIRED_MIGRATION)
        except Exception:
            log_readiness_failure(logger)
            is_ready = False
        if not is_ready:
            return JSONResponse(status_code=503, content=NOT_READY_BODY)
        return {"status": "ready"}

    return router
