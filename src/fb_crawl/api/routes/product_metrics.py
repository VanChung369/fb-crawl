from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from fb_crawl.accounts.models import AccountRole
from fb_crawl.api.dependencies import CurrentAccount, ProductAccountAuth
from fb_crawl.api.product_schemas import ProductMetricsResponse
from fb_crawl.exports.metrics import ProductMetricsRepository


def create_product_metrics_router(
    repository: ProductMetricsRepository,
    current_auth: ProductAccountAuth,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["product-admin"])

    def require_admin(
        current: CurrentAccount = Depends(current_auth),
    ) -> CurrentAccount:
        if current.account.role is not AccountRole.ADMIN:
            raise HTTPException(
                status_code=403, detail="Administrator access required."
            )
        return current

    @router.get("/product-metrics", response_model=ProductMetricsResponse)
    def product_metrics(
        _current: CurrentAccount = Depends(require_admin),
    ) -> ProductMetricsResponse:
        return ProductMetricsResponse(**asdict(repository.get()))

    return router
