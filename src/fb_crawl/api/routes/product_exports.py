from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import FileResponse, JSONResponse

from fb_crawl.api.dependencies import (
    CurrentAccount,
    ProductAccountAuth,
    ProductAuthenticationError,
)
from fb_crawl.api.product_schemas import (
    ExportCreateRequest,
    ExportDeleteResponse,
    ExportJobResponse,
)
from fb_crawl.exports.models import ExportJob, ExportStatus
from fb_crawl.exports.postgres import ExportQueueFull
from fb_crawl.exports.service import ExportService
from fb_crawl.auth.rate_limit import RateLimitService


def create_product_export_router(
    service: ExportService,
    current_auth: ProductAccountAuth,
    rate_limiter: RateLimitService,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/exports", tags=["product-exports"])

    async def require_bearer_account(
        current: CurrentAccount = Depends(current_auth),
    ) -> CurrentAccount:
        if current.cookie_authenticated:
            raise ProductAuthenticationError()
        return current

    @router.post("", response_model=ExportJobResponse, status_code=202)
    def create_export(
        payload: ExportCreateRequest,
        request: Request,
        current: CurrentAccount = Depends(require_bearer_account),
    ):
        if not current.device_allowed:
            return _error(403, "export_device_not_allowed")
        now = clock()
        rate_limiter.check(
            "export_create",
            str(current.account.id),
            str(current.device.id),
            _client_ip(request),
            now,
        )
        try:
            job = service.create(
                current.account.id,
                payload.format,
                payload.filters.model_dump(exclude_none=True),
                now,
            )
        except ExportQueueFull:
            return _error(429, "export_queue_full")
        return _response(job)

    @router.get("/{export_id}", response_model=ExportJobResponse)
    def get_export(
        export_id: UUID = Path(),
        current: CurrentAccount = Depends(require_bearer_account),
    ):
        if not current.device_allowed:
            return _error(403, "export_device_not_allowed")
        job = service.get(current.account.id, export_id, clock())
        if job is None or job.account_id != current.account.id:
            return _error(404, "export_not_found")
        return _response(job)

    @router.get("/{export_id}/download")
    def download_export(
        export_id: UUID = Path(),
        current: CurrentAccount = Depends(require_bearer_account),
    ):
        if not current.device_allowed:
            return _error(403, "export_device_not_allowed")
        job = service.get(current.account.id, export_id, clock())
        if job is None or job.account_id != current.account.id:
            return _error(404, "export_not_found")
        if job.status is ExportStatus.EXPIRED:
            return _error(410, "export_expired")
        if job.status is not ExportStatus.COMPLETED:
            return _error(409, "export_not_ready")
        artifact = service.artifact(job)
        if artifact is None:
            return _error(410, "export_artifact_unavailable")
        media_type = (
            "text/csv; charset=utf-8"
            if job.format.value == "csv"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response = FileResponse(
            artifact,
            media_type=media_type,
            filename=f"lead-finder-history-{job.id}.{job.format.value}",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    @router.delete("/{export_id}", response_model=ExportDeleteResponse)
    def delete_export(
        export_id: UUID = Path(),
        current: CurrentAccount = Depends(require_bearer_account),
    ):
        if not current.device_allowed:
            return _error(403, "export_device_not_allowed")
        if not service.delete(current.account.id, export_id):
            return _error(404, "export_not_found")
        return ExportDeleteResponse(deleted=True)

    return router


def _response(job: ExportJob) -> ExportJobResponse:
    return ExportJobResponse(
        id=job.id,
        format=job.format,
        filters=dict(job.filter_snapshot),
        status=job.status,
        attempt_count=job.attempt_count,
        safe_error_code=job.safe_error_code,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
        expires_at=job.expires_at,
        download_url=(
            f"/api/v1/exports/{job.id}/download"
            if job.status is ExportStatus.COMPLETED
            else None
        ),
    )


def _error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": "Export request failed."},
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"
