from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from fb_crawl.api.dependencies import CurrentAccount, ProductAccountAuth, ProductAuthenticationError
from fb_crawl.api.product_schemas import (
    HistoryPageResponse,
    ProductCrawlJobCreateRequest,
    ProductCrawlJobResponse,
)
from fb_crawl.api.routes.history import history_item_response
from fb_crawl.history.models import AccountHistoryQuery
from fb_crawl.product_jobs.models import ProductCrawlJob, ProductCrawlScope
from fb_crawl.product_jobs.service import CrawlUpgradeRequired, ProductCrawlJobService


def create_product_crawl_router(
    service: ProductCrawlJobService,
    current_auth: ProductAccountAuth,
    history_repository=None,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(tags=["product-crawl-jobs"])

    async def bearer(current: CurrentAccount = Depends(current_auth)) -> CurrentAccount:
        if current.cookie_authenticated:
            raise ProductAuthenticationError()
        return current

    @router.post("/api/v1/crawl-jobs", response_model=ProductCrawlJobResponse)
    def create_job(
        payload: ProductCrawlJobCreateRequest,
        current: CurrentAccount = Depends(bearer),
    ):
        if not current.device_allowed:
            return _error(403, "crawl_device_not_allowed")
        try:
            return _response(service.create(
                current.account.id,
                ProductCrawlScope(payload.scan_scope),
                payload.target_url,
                payload.max_identities,
                clock(),
            ))
        except CrawlUpgradeRequired:
            return _error(403, "crawl_upgrade_required")

    @router.get("/api/v1/crawl-jobs/{job_id}", response_model=ProductCrawlJobResponse)
    def get_job(job_id: UUID, current: CurrentAccount = Depends(bearer)):
        value = service.get(current.account.id, job_id, clock())
        return _error(404, "crawl_job_not_found") if value is None else _response(value)

    @router.get(
        "/api/v1/crawl-jobs/{job_id}/results",
        response_model=HistoryPageResponse,
    )
    def get_job_results(
        job_id: UUID,
        cursor: str | None = Query(default=None, max_length=1024),
        limit: int = Query(default=20, ge=1, le=100),
        current: CurrentAccount = Depends(bearer),
    ):
        if not current.device_allowed:
            return _error(403, "crawl_device_not_allowed")
        if service.get(current.account.id, job_id, clock()) is None:
            return _error(404, "crawl_job_not_found")
        if history_repository is None:
            return _error(503, "crawl_results_unavailable")
        page = history_repository.list(AccountHistoryQuery(
            account_id=current.account.id,
            product_crawl_job_id=job_id,
            cursor=cursor,
            limit=limit,
        ))
        return HistoryPageResponse(
            items=[history_item_response(item) for item in page.items],
            next_cursor=page.next_cursor,
        )

    @router.post("/api/v1/crawl-jobs/{job_id}/cancel", response_model=ProductCrawlJobResponse)
    def cancel_job(job_id: UUID, current: CurrentAccount = Depends(bearer)):
        value = service.cancel(current.account.id, job_id, clock())
        return _error(404, "crawl_job_not_found") if value is None else _response(value)

    return router


def _response(value: ProductCrawlJob) -> ProductCrawlJobResponse:
    return ProductCrawlJobResponse(
        id=value.id,
        scan_scope=value.scope,
        target_url=value.target_url,
        max_identities=value.max_identities,
        status=value.status,
        discovered_count=value.discovered_count,
        processed_count=value.processed_count,
        found_count=value.found_count,
        not_found_count=value.not_found_count,
        quota_exceeded_count=value.quota_exceeded_count,
        safe_error_code=value.safe_error_code,
        created_at=value.created_at,
        updated_at=value.updated_at,
        completed_at=value.completed_at,
    )


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "code": code, "message": "Automatic crawl request failed."
    })
