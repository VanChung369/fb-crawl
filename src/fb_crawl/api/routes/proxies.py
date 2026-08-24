"""Proxy pool management routes."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

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


def _proxy_display_url(raw_url: str) -> str:
    parsed = urlsplit(raw_url)
    if not parsed.hostname or parsed.port is None:
        return raw_url
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return urlunsplit((parsed.scheme, f"{host}:{parsed.port}", "", "", ""))


def _resolve_proxy_key(proxy_pool: ProxyPool, value: str) -> str:
    for entry in proxy_pool._entries:
        if entry.raw_url == value or entry.formatted_url == value:
            return entry.raw_url
        if _proxy_display_url(entry.raw_url) == value:
            return entry.raw_url
    return value


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
                display_url=_proxy_display_url(p.raw_url),
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
        proxy_key = _resolve_proxy_key(proxy_pool, request.raw_url)
        entry = proxy_pool.update_proxy(proxy_key, new_url=request.new_url, status=request.status)
        if not entry:
            raise HTTPException(status_code=404, detail="Không tìm thấy proxy tương ứng.")
        return ProxyItemResponse(
            display_url=_proxy_display_url(entry.raw_url),
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
        proxy_key = _resolve_proxy_key(proxy_pool, request.raw_url)
        removed = proxy_pool.remove_proxy(proxy_key)
        if not removed:
            raise HTTPException(status_code=404, detail="Không tìm thấy proxy để xóa.")
        return {"status": "success", "message": "Proxy đã được xóa khỏi Pool thành công."}

    return router
