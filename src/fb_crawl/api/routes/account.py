"""Safe crawler-account status and manual acknowledgement routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from fb_crawl.api.dependencies import ApiKeyAuth
from fb_crawl.api.schemas import (
    AccountAcknowledgeRequest,
    AccountResponse,
    ApiErrorResponse,
)
from fb_crawl.core.jobs import CrawlerAccountState
from fb_crawl.services.jobs import JobService


ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
    409: {"model": ApiErrorResponse},
}


def create_account_router(job_service: JobService, auth: ApiKeyAuth) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/account/default",
        tags=["account"],
        dependencies=[Depends(auth)],
    )

    @router.get(
        "",
        response_model=AccountResponse,
        responses=ERROR_RESPONSES,
    )
    def get_account() -> AccountResponse:
        return _account_response(job_service.get_account("default"))

    @router.post(
        "/acknowledge",
        response_model=AccountResponse,
        responses=ERROR_RESPONSES,
    )
    def acknowledge_account(request: AccountAcknowledgeRequest) -> AccountResponse:
        account = job_service.acknowledge_account(
            account_key="default",
            acknowledged=request.acknowledged,
        )
        return _account_response(account)

    return router


def _account_response(account: CrawlerAccountState) -> AccountResponse:
    return AccountResponse(
        account_key="default",
        status=account.status,
        cooldown_until=account.cooldown_until,
        last_started_at=account.last_started_at,
        last_finished_at=account.last_finished_at,
        last_rate_limit_at=account.last_rate_limit_at,
        last_warning_code=account.last_warning_code,
        last_warning_at=account.last_warning_at,
        acknowledged_at=account.acknowledged_at,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )
