"""Proxy pool management routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from fb_crawl.api.dependencies import ApiKeyAuth
from fb_crawl.api.schemas import (
    ApiErrorResponse,
    ProxyAddRequest,
    ProxyDeleteRequest,
    ProxyItemResponse,
    ProxyListResponse,
    ProxyUpdateRequest,
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

    @router.patch("", response_model=ProxyItemResponse, responses=ERROR_RESPONSES)
    def update_proxy(request: ProxyUpdateRequest) -> ProxyItemResponse:
        from fastapi import HTTPException
        entry = proxy_pool.update_proxy(request.raw_url, new_url=request.new_url, status=request.status)
        if not entry:
            raise HTTPException(status_code=404, detail="Không tìm thấy proxy tương ứng.")
        return ProxyItemResponse(
            raw_url=entry.raw_url,
            scheme=entry.scheme,
            host=entry.host,
            port=entry.port,
            status=entry.status.value,
            success_count=entry.success_count,
            failure_count=entry.failure_count,
            is_available=entry.is_available,
        )

    @router.delete("", responses=ERROR_RESPONSES)
    def delete_proxy(request: ProxyDeleteRequest):
        from fastapi import HTTPException
        removed = proxy_pool.remove_proxy(request.raw_url)
        if not removed:
            raise HTTPException(status_code=404, detail="Không tìm thấy proxy để xóa.")
        return {"status": "success", "message": "Proxy đã được xóa khỏi Pool thành công."}

    return router
