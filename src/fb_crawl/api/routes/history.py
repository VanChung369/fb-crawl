from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse

from fb_crawl.api.dependencies import (
    CurrentAccount,
    ProductAccountAuth,
    ProductAuthenticationError,
)
from fb_crawl.api.product_schemas import (
    ContactUserResponse,
    HistoryDeleteRequest,
    HistoryDeleteResponse,
    HistoryItemResponse,
    HistoryPageResponse,
)
from fb_crawl.contacts.models import LookupOutcome
from fb_crawl.entitlements.quota import ContactQuotaService
from fb_crawl.history.models import AccountHistoryQuery, HistoryItem
from fb_crawl.history.repository import HistoryRepository
from fb_crawl.history.service import HistoryService
from fb_data_pipeline.detectors.vietnamese import infer_profile_attributes


def create_history_router(
    repository: HistoryRepository,
    current_auth: ProductAccountAuth,
    quota: ContactQuotaService,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(tags=["product-history"])
    service = HistoryService(repository, quota, clock=clock)

    async def require_bearer_account(
        current: CurrentAccount = Depends(current_auth),
    ) -> CurrentAccount:
        if current.cookie_authenticated:
            raise ProductAuthenticationError()
        return current

    @router.get(
        "/api/v1/history/lookups",
        response_model=HistoryPageResponse,
    )
    def list_history(
        outcome: LookupOutcome | None = None,
        name: str | None = Query(default=None, max_length=256),
        uid: str | None = Query(default=None, max_length=32),
        username: str | None = Query(default=None, max_length=100),
        phone: str | None = Query(default=None, max_length=64),
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        cursor: str | None = Query(default=None, max_length=1024),
        limit: int = Query(default=20, ge=1, le=100),
        current: CurrentAccount = Depends(require_bearer_account),
    ):
        if not current.device_allowed:
            return _device_not_allowed()
        page = service.list(
            _query(
                current.account.id,
                outcome=outcome,
                name=name,
                uid=uid,
                username=username,
                phone=phone,
                created_from=created_from,
                created_to=created_to,
                cursor=cursor,
                limit=limit,
            )
        )
        return HistoryPageResponse(
            items=[history_item_response(value) for value in page.items],
            next_cursor=page.next_cursor,
        )

    @router.get(
        "/api/v1/history/lookups/{event_id}",
        response_model=HistoryItemResponse,
    )
    def get_history(
        event_id: int = Path(gt=0, le=9223372036854775807),
        current: CurrentAccount = Depends(require_bearer_account),
    ):
        if not current.device_allowed:
            return _device_not_allowed()
        value = service.get(current.account.id, event_id)
        return _not_found() if value is None else history_item_response(value)

    @router.delete(
        "/api/v1/history/lookups/{event_id}",
        response_model=HistoryDeleteResponse,
    )
    def delete_history(
        event_id: int = Path(gt=0, le=9223372036854775807),
        current: CurrentAccount = Depends(require_bearer_account),
    ):
        if not current.device_allowed:
            return _device_not_allowed()
        if not repository.delete_one(current.account.id, event_id):
            return _not_found()
        return HistoryDeleteResponse(deleted_count=1)

    @router.delete(
        "/api/v1/history/people/{facebook_user_id}",
        response_model=HistoryDeleteResponse,
    )
    def delete_person_history(
        facebook_user_id: int = Path(gt=0, le=9223372036854775807),
        current: CurrentAccount = Depends(require_bearer_account),
    ):
        if not current.device_allowed:
            return _device_not_allowed()
        return HistoryDeleteResponse(
            deleted_count=repository.delete_person(
                current.account.id,
                facebook_user_id,
            )
        )

    @router.delete(
        "/api/v1/history/lookups",
        response_model=HistoryDeleteResponse,
    )
    def delete_history_filtered(
        payload: HistoryDeleteRequest,
        outcome: LookupOutcome | None = None,
        name: str | None = Query(default=None, max_length=256),
        uid: str | None = Query(default=None, max_length=32),
        username: str | None = Query(default=None, max_length=100),
        phone: str | None = Query(default=None, max_length=64),
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        current: CurrentAccount = Depends(require_bearer_account),
    ):
        if not current.device_allowed:
            return _device_not_allowed()
        return HistoryDeleteResponse(
            deleted_count=repository.delete_filtered(
                _query(
                    current.account.id,
                    outcome=outcome,
                    name=name,
                    uid=uid,
                    username=username,
                    phone=phone,
                    created_from=created_from,
                    created_to=created_to,
                )
            )
        )

    return router


def _query(account_id: int, **values: object) -> AccountHistoryQuery:
    return AccountHistoryQuery(account_id=account_id, **values)


def history_item_response(value: HistoryItem) -> HistoryItemResponse:
    address, gender = infer_profile_attributes(value.name, value.address, value.gender)
    return HistoryItemResponse(
        id=value.id,
        device_id=value.device_id,
        facebook_user_id=value.facebook_user_id,
        user=ContactUserResponse(
            facebook_uid=value.facebook_uid,
            username=value.username,
            name=value.name,
            profile_url=value.profile_url,
            gender=gender,
            address=address,
            birth_date=value.birth_date,
        ),
        phone=value.phone,
        outcome=value.outcome,
        source=value.source,
        provider_called=value.provider_called,
        quota_charged=value.quota_charged,
        safe_error_code=value.safe_error_code,
        created_at=value.created_at,
        completed_at=value.completed_at,
        scan_mode=value.scan_mode,
        source_type=value.source_type,
        source_url=value.source_url,
        product_crawl_job_id=value.product_crawl_job_id,
        gender=gender,
        address=address,
        birth_date=value.birth_date,
    )


def _not_found() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "code": "history_lookup_not_found",
            "message": "Lookup history was not found.",
        },
    )


def _device_not_allowed() -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "code": "history_device_not_allowed",
            "message": "History access failed.",
        },
    )
