"""Proxy pool management routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from fb_crawl.api.dependencies import ApiKeyAuth
from fb_crawl.api.schemas import (
    ApiErrorResponse,
    ProxyAddRequest,
    ProxyItemResponse,
    ProxyListResponse,
)
from fb_crawl.core.proxy_pool import ProxyPool

ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
}


def create_proxies_router(proxy_pool: ProxyPool, auth: ApiKeyAuth) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/proxies",
        tags=["proxies"],
        dependencies=[Depends(auth)],
    )

    @router.get("", response_model=ProxyListResponse, responses=ERROR_RESPONSES)
    def list_proxies() -> ProxyListResponse:
        items = [
            ProxyItemResponse(
                raw_url=p.raw_url,
                scheme=p.scheme,
                host=p.host,
                port=p.port,
                status=p.status.value,
                success_count=p.success_count,
                failure_count=p.failure_count,
                is_available=p.is_available,
            )
            for p in proxy_pool._entries
        ]
        return ProxyListResponse(
            total_count=proxy_pool.total_count,
            active_count=proxy_pool.active_count,
            items=items,
        )

    @router.post("", status_code=status.HTTP_201_CREATED, response_model=ProxyListResponse, responses=ERROR_RESPONSES)
    def add_proxies(request: ProxyAddRequest) -> ProxyListResponse:
        for item in request.proxies:
            proxy_pool.add_proxy(item)
        return list_proxies()

    return router
