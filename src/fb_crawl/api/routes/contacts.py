from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse

from fb_crawl.api.dependencies import (
    CurrentAccount,
    ProductAccountAuth,
    ProductAuthenticationError,
)
from fb_crawl.api.product_schemas import (
    BatchContactLookupRequest,
    BatchContactLookupResponse,
    BatchContactResultResponse,
    ContactDataResponse,
    ContactLookupMetaResponse,
    ContactLookupRequest,
    ContactLookupResponse,
    ContactUserResponse,
)
from fb_crawl.contacts.batch import BatchContactItem, BatchContactLookupService
from fb_crawl.contacts.models import LookupOutcome
from fb_crawl.contacts.service import ContactLookupResult, ContactLookupService
from fb_crawl.auth.rate_limit import RateLimitService
from fb_data_pipeline.repositories.errors import DatabaseIdentityConflict


def create_contact_router(
    service: ContactLookupService,
    current_auth: ProductAccountAuth,
    rate_limiter: RateLimitService,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(tags=["product-contacts"])
    batch_service = BatchContactLookupService(service)

    async def require_bearer_account(
        current: CurrentAccount = Depends(current_auth),
    ) -> CurrentAccount:
        if current.cookie_authenticated:
            raise ProductAuthenticationError()
        return current

    @router.post(
        "/api/v1/contacts/lookup",
        response_model=ContactLookupResponse,
    )
    def lookup_contact(
        payload: ContactLookupRequest,
        request: Request,
        current: CurrentAccount = Depends(require_bearer_account),
    ) -> JSONResponse:
        if not current.device_allowed:
            return _safe_error(403, "contact_device_not_allowed")
        try:
            now = clock()
            rate_limiter.check(
                "contact_lookup",
                str(current.account.id),
                str(current.device.id),
                _client_ip(request),
                now,
            )
            result = service.lookup(
                current.account,
                current.device,
                payload.to_domain(),
                now,
                force_refresh=payload.force_refresh,
            )
        except DatabaseIdentityConflict:
            return _safe_error(409, "provider_identity_conflict")
        except PermissionError:
            return _safe_error(403, "contact_access_denied")
        return _lookup_response(result)

    @router.post(
        "/api/v1/contacts/batch-lookup",
        response_model=BatchContactLookupResponse,
    )
    def batch_lookup_contacts(
        payload: BatchContactLookupRequest,
        request: Request,
        current: CurrentAccount = Depends(require_bearer_account),
    ) -> JSONResponse:
        if not current.device_allowed:
            return _safe_error(403, "contact_device_not_allowed")
        try:
            now = clock()
            rate_limiter.check(
                "contact_batch_lookup",
                str(current.account.id),
                str(current.device.id),
                _client_ip(request),
                now,
            )
            result = batch_service.lookup(
                current.account,
                current.device,
                tuple(
                    BatchContactItem(
                        request=item.to_domain(),
                        source_type=item.source_type,
                        source_url=item.source_url,
                    )
                    for item in payload.items
                ),
                now,
            )
        except DatabaseIdentityConflict:
            return _safe_error(409, "provider_identity_conflict")
        except PermissionError:
            return _safe_error(403, "contact_access_denied")
        response = BatchContactLookupResponse(
            items=[
                BatchContactResultResponse(
                    index=item.index,
                    duplicate_of=item.duplicate_of,
                    **_lookup_model(item.result).model_dump(),
                )
                for item in result.items
            ],
            detected_count=result.detected_count,
            unique_count=result.unique_count,
            processed_count=result.processed_count,
            found_count=result.found_count,
            quota_exceeded_count=result.quota_exceeded_count,
        )
        return JSONResponse(content=response.model_dump(mode="json"))

    @router.get(
        "/api/v1/contacts/lookups/{event_id}",
        response_model=ContactLookupResponse,
    )
    def poll_contact_lookup(
        request: Request,
        event_id: int = Path(gt=0, le=9223372036854775807),
        current: CurrentAccount = Depends(require_bearer_account),
    ) -> JSONResponse:
        if not current.device_allowed:
            return _safe_error(403, "contact_device_not_allowed")
        rate_limiter.check(
            "contact_poll",
            str(current.account.id),
            str(current.device.id),
            _client_ip(request),
            clock(),
        )
        result = service.get_event(current.account.id, event_id)
        if result is None:
            return _safe_error(404, "contact_lookup_not_found")
        return _lookup_response(result)

    return router


def _lookup_response(result: ContactLookupResult) -> JSONResponse:
    response = _lookup_model(result)
    return JSONResponse(
        status_code=_status_code(result),
        content=response.model_dump(mode="json"),
    )


def _lookup_model(result: ContactLookupResult) -> ContactLookupResponse:
    return ContactLookupResponse(
        user=ContactUserResponse(
            facebook_uid=result.user.uid,
            username=result.user.username,
            name=result.user.name,
            profile_url=result.user.profile_url,
        ),
        contact=ContactDataResponse(phone=result.phone),
        meta=ContactLookupMetaResponse(
            event_id=result.event_id,
            state=result.state,
            source=result.source,
            observed_at=result.observed_at,
            provider_called=result.provider_called,
            quota_charged=result.quota_charged,
            monthly_used=result.monthly_used,
            monthly_limit=result.monthly_limit,
            poll_url=(
                f"/api/v1/contacts/lookups/{result.event_id}"
                if result.state is LookupOutcome.PROCESSING
                else None
            ),
            safe_error_code=result.safe_error_code,
        ),
    )


def _status_code(result: ContactLookupResult) -> int:
    if result.state is LookupOutcome.PROCESSING:
        return 202
    if result.state is LookupOutcome.QUOTA_EXCEEDED:
        return 429
    if result.state is LookupOutcome.FAILED:
        if result.safe_error_code == "provider_identity_conflict":
            return 409
        if result.safe_error_code == "provider_rate_limited":
            return 429
        if result.safe_error_code == "provider_authentication_failed":
            return 503
        return 502
    return 200


def _safe_error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": "Contact lookup failed."},
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"
