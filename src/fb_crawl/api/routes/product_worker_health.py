from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from fb_crawl.api.dependencies import (
    CurrentAccount,
    ProductAccountAuth,
    ProductAuthenticationError,
)
from fb_crawl.api.product_schemas import (
    ExportWorkerHealthResponse,
    ProductWorkerHealthResponse,
    ProductWorkersHealthResponse,
)
from fb_crawl.exports.service import ExportService


def create_product_worker_health_router(
    export_service: ExportService,
    current_auth: ProductAccountAuth,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(tags=["product-worker-health"])

    async def require_bearer_account(
        current: CurrentAccount = Depends(current_auth),
    ) -> CurrentAccount:
        if current.cookie_authenticated:
            raise ProductAuthenticationError()
        return current

    @router.get(
        "/api/v1/worker-health",
        response_model=ProductWorkerHealthResponse,
    )
    def worker_health(
        current: CurrentAccount = Depends(require_bearer_account),
    ):
        if not current.device_allowed:
            return JSONResponse(
                status_code=403,
                content={
                    "code": "worker_health_device_not_allowed",
                    "message": "Worker health request failed.",
                },
            )
        health = export_service.worker_health(clock())
        return ProductWorkerHealthResponse(
            workers=ProductWorkersHealthResponse(
                export=ExportWorkerHealthResponse(
                    available=health.available,
                    last_seen_at=health.last_seen_at,
                )
            )
        )

    return router
