"""System overview and dashboard statistics routes."""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends

from fb_crawl.api.dependencies import ApiKeyAuth
from fb_crawl.api.schemas import (
    ApiErrorResponse,
    StatsOverviewResponse,
)
from fb_crawl.core.session_pool import SessionPool
from fb_crawl.core.proxy_pool import ProxyPool

ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
}


def create_stats_router(
    job_repository: Any,
    user_repository: Any,
    session_pool: SessionPool | None,
    proxy_pool: ProxyPool | None,
    auth: ApiKeyAuth,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/stats",
        tags=["stats"],
        dependencies=[Depends(auth)],
    )

    @router.get("/overview", response_model=StatsOverviewResponse, responses=ERROR_RESPONSES)
    def get_overview_stats() -> StatsOverviewResponse:
        total_users = 0
        users_with_phone = 0
        try:
            users_page = user_repository.list_users(limit=1)
            total_users = getattr(users_page, "total_count", len(users_page.items))
            phone_page = user_repository.list_users(has_phone=True, limit=1)
            users_with_phone = getattr(phone_page, "total_count", len(phone_page.items))
        except Exception:
            pass

        total_jobs = 0
        try:
            jobs_page = job_repository.list_jobs(limit=1)
            total_jobs = getattr(jobs_page, "total_count", len(jobs_page.items))
        except Exception:
            pass

        total_proxies = proxy_pool.total_count if proxy_pool else 0
        active_proxies = proxy_pool.active_count if proxy_pool else 0
        total_sessions = session_pool.total_count if session_pool else 0
        available_sessions = len(session_pool.available_sessions) if session_pool else 0

        return StatsOverviewResponse(
            total_users=total_users,
            users_with_phone=users_with_phone,
            total_jobs=total_jobs,
            active_proxies=active_proxies,
            total_proxies=total_proxies,
            available_sessions=available_sessions,
            total_sessions=total_sessions,
        )

    return router
