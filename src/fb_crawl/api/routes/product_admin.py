from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response

from fb_crawl.accounts.models import Account, AccountRole
from fb_crawl.accounts.repository import AccountNotFound, AccountRepository
from fb_crawl.api.dependencies import CurrentAccount, ProductAccountAuth
from fb_crawl.api.product_schemas import (
    AccountAdminListResponse,
    AccountAdminResponse,
    AdminAuditEventListResponse,
    AdminAuditEventResponse,
    CreatedLicenseKeyResponse,
    RevealedLicenseKeyResponse,
    DeviceRevokedResponse,
    DeviceListResponse,
    DeviceResponse,
    LicenseDurationRequest,
    LicenseGrantRequest,
    LicenseKeyListResponse,
    LicenseKeyResponse,
    SessionsRevokedResponse,
    SubscriptionListResponse,
)
from fb_crawl.api.routes.licenses import _subscription_response
from fb_crawl.licenses.models import LicenseDuration, LicenseGrant, LicenseKey
from fb_crawl.licenses.service import LicenseService
from fb_crawl.licenses.repository import InvalidLicenseKey, LicenseKeyRevealUnavailable
from fb_crawl.auth.rate_limit import RateLimitService


def create_product_admin_router(
    accounts: AccountRepository,
    licenses: LicenseService,
    current_auth: ProductAccountAuth,
    rate_limiter: RateLimitService,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["product-admin"])

    def require_admin(
        current: CurrentAccount = Depends(current_auth),
    ) -> CurrentAccount:
        if current.account.role is not AccountRole.ADMIN:
            raise HTTPException(status_code=403, detail="Administrator access required.")
        return current

    def require_recent_admin(
        current: CurrentAccount = Depends(require_admin),
    ) -> CurrentAccount:
        if clock() - current.session.authenticated_at > timedelta(minutes=15):
            raise HTTPException(status_code=403, detail="Recent authentication required.")
        return current

    @router.post("/license-keys", response_model=CreatedLicenseKeyResponse)
    def create_license_key(
        payload: LicenseGrantRequest,
        request: Request,
        response: Response,
        current: CurrentAccount = Depends(require_admin),
    ) -> CreatedLicenseKeyResponse:
        now = clock()
        _limit_admin(rate_limiter, "admin_license_create", current, request, now)
        grant = LicenseGrant(
            LicenseDuration(payload.duration.unit, payload.duration.value),
            payload.monthly_contact_limit,
            payload.max_devices,
            payload.allow_group_crawl,
            payload.allow_comment_crawl,
            payload.features,
        )
        key, plaintext = licenses.create_key(grant, current.account.id, now)
        response.headers["Cache-Control"] = "no-store"
        return CreatedLicenseKeyResponse(
            **_license_response(key).model_dump(), key=plaintext
        )

    @router.post("/license-keys/{key_id}/reveal", response_model=RevealedLicenseKeyResponse)
    def reveal_license_key(
        request: Request,
        response: Response,
        key_id: int = Path(gt=0),
        current: CurrentAccount = Depends(require_admin),
    ) -> RevealedLicenseKeyResponse:
        now = clock()
        _limit_admin(rate_limiter, "admin_license_reveal", current, request, now)
        response.headers["Cache-Control"] = "no-store"
        try:
            plaintext = licenses.reveal_key(key_id, current.account.id, now)
        except InvalidLicenseKey:
            raise HTTPException(status_code=404, detail="License key not found.") from None
        except LicenseKeyRevealUnavailable:
            raise HTTPException(
                status_code=409,
                detail="Không thể xem lại key này. Key cũ không lưu bản mã hóa hoặc khóa giải mã không còn khả dụng.",
            ) from None
        return RevealedLicenseKeyResponse(key=plaintext)

    @router.get("/license-keys", response_model=LicenseKeyListResponse)
    def list_license_keys(
        limit: int = Query(default=100, ge=1, le=100),
        cursor: int | None = Query(default=None, gt=0),
        _current: CurrentAccount = Depends(require_admin),
    ) -> LicenseKeyListResponse:
        keys = licenses.list_keys(limit=limit + 1, cursor=cursor)
        has_more = len(keys) > limit
        visible = keys[:limit]
        return LicenseKeyListResponse(
            items=[_license_response(key) for key in visible],
            next_cursor=visible[-1].id if has_more else None,
        )

    @router.delete(
        "/license-keys/{key_id}", response_model=LicenseKeyResponse
    )
    def revoke_license_key(
        request: Request,
        key_id: int = Path(gt=0),
        current: CurrentAccount = Depends(require_admin),
    ) -> LicenseKeyResponse:
        now = clock()
        _limit_admin(rate_limiter, "admin_license_revoke", current, request, now)
        return _license_response(
            licenses.revoke_key(key_id, current.account.id, now)
        )

    @router.get("/accounts", response_model=AccountAdminListResponse)
    def list_accounts(
        limit: int = Query(default=100, ge=1, le=100),
        cursor: int | None = Query(default=None, gt=0),
        _current: CurrentAccount = Depends(require_admin),
    ) -> AccountAdminListResponse:
        rows = accounts.list_accounts(limit=limit + 1, cursor=cursor)
        has_more = len(rows) > limit
        visible = rows[:limit]
        return AccountAdminListResponse(
            items=[_account_response(account) for account in visible],
            next_cursor=visible[-1].id if has_more else None,
        )

    @router.get(
        "/accounts/{account_id}/devices", response_model=DeviceListResponse
    )
    def list_account_devices(
        account_id: int = Path(gt=0),
        current: CurrentAccount = Depends(require_admin),
    ) -> DeviceListResponse:
        if accounts.get_account(account_id) is None:
            raise AccountNotFound("Account was not found.")
        return DeviceListResponse(
            items=[
                DeviceResponse(
                    id=device.id,
                    installation_id=device.installation_id,
                    display_name=device.display_name,
                    status=device.status,
                    first_seen_at=device.first_seen_at,
                    last_seen_at=device.last_seen_at,
                    current=(
                        account_id == current.account.id
                        and device.id == current.device.id
                    ),
                )
                for device in accounts.list_devices(account_id)
            ]
        )

    @router.get("/audit-events", response_model=AdminAuditEventListResponse)
    def list_audit_events(
        limit: int = Query(default=100, ge=1, le=100),
        cursor: int | None = Query(default=None, gt=0),
        _current: CurrentAccount = Depends(require_admin),
    ) -> AdminAuditEventListResponse:
        rows = licenses.list_audit_events(limit=limit + 1, cursor=cursor)
        has_more = len(rows) > limit
        visible = rows[:limit]
        return AdminAuditEventListResponse(
            items=[
                AdminAuditEventResponse(
                    id=event.id,
                    actor_account_id=event.actor_account_id,
                    action=event.action,
                    target_type=event.target_type,
                    target_id=event.target_id,
                    details=event.details,
                    created_at=event.created_at,
                )
                for event in visible
            ],
            next_cursor=visible[-1].id if has_more else None,
        )

    @router.post(
        "/accounts/{account_id}/suspend", response_model=AccountAdminResponse
    )
    def suspend_account(
        request: Request,
        account_id: int = Path(gt=0),
        current: CurrentAccount = Depends(require_recent_admin),
    ) -> AccountAdminResponse:
        now = clock()
        _limit_admin(rate_limiter, "admin_account_suspend", current, request, now)
        if account_id == current.account.id:
            raise HTTPException(status_code=403, detail="Administrator accounts cannot be suspended here.")
        account = accounts.suspend_account_as_admin(
            account_id, current.account.id, now
        )
        return _account_response(account)

    @router.delete(
        "/accounts/{account_id}/devices/{device_id}",
        response_model=DeviceRevokedResponse,
    )
    def revoke_account_device(
        request: Request,
        account_id: int = Path(gt=0),
        device_id: int = Path(gt=0),
        current: CurrentAccount = Depends(require_recent_admin),
    ) -> DeviceRevokedResponse:
        now = clock()
        _limit_admin(rate_limiter, "admin_device_revoke", current, request, now)
        accounts.revoke_device_as_admin(
            account_id, device_id, current.account.id, now
        )
        return DeviceRevokedResponse(device_id=device_id)

    @router.delete(
        "/accounts/{account_id}/sessions",
        response_model=SessionsRevokedResponse,
    )
    def revoke_account_sessions(
        request: Request,
        account_id: int = Path(gt=0),
        current: CurrentAccount = Depends(require_recent_admin),
    ) -> SessionsRevokedResponse:
        now = clock()
        _limit_admin(rate_limiter, "admin_sessions_revoke", current, request, now)
        accounts.revoke_account_sessions_as_admin(
            account_id, current.account.id, now
        )
        return SessionsRevokedResponse(account_id=account_id)

    @router.get(
        "/accounts/{account_id}/subscriptions",
        response_model=SubscriptionListResponse,
    )
    def list_subscriptions(
        account_id: int = Path(gt=0),
        _current: CurrentAccount = Depends(require_admin),
    ) -> SubscriptionListResponse:
        return SubscriptionListResponse(
            items=[
                _subscription_response(subscription)
                for subscription in licenses.list_subscriptions(account_id)
            ]
        )

    @router.post(
        "/accounts/{account_id}/subscriptions/{subscription_id}/start-now",
        response_model=SubscriptionListResponse,
    )
    def start_subscription_now(
        request: Request,
        account_id: int = Path(gt=0),
        subscription_id: int = Path(gt=0),
        current: CurrentAccount = Depends(require_admin),
    ) -> SubscriptionListResponse:
        now = clock()
        _limit_admin(
            rate_limiter, "admin_subscription_start_now", current, request, now
        )
        shifted = licenses.start_subscription_now(
            account_id, subscription_id, current.account.id, now
        )
        return SubscriptionListResponse(
            items=[_subscription_response(subscription) for subscription in shifted]
        )

    return router


def _limit_admin(
    rate_limiter: RateLimitService,
    action: str,
    current: CurrentAccount,
    request: Request,
    now: datetime,
) -> None:
    ip_address = request.client.host if request.client is not None else "unknown"
    rate_limiter.check(
        action,
        str(current.account.id),
        str(current.device.id),
        ip_address,
        now,
    )


def _license_response(value: LicenseKey) -> LicenseKeyResponse:
    return LicenseKeyResponse(
        id=value.id,
        masked_key=value.masked_key,
        can_reveal=bool(value.encrypted_key),
        key_version=value.key_version,
        duration=LicenseDurationRequest(
            unit=value.grant.duration.unit, value=value.grant.duration.value
        ),
        monthly_contact_limit=value.grant.monthly_contact_limit,
        max_devices=value.grant.max_devices,
        allow_group_crawl=value.grant.allow_group_crawl,
        allow_comment_crawl=value.grant.allow_comment_crawl,
        features=value.grant.features,
        status=value.status,
        created_by_account_id=value.created_by_account_id,
        redeemed_by_account_id=value.redeemed_by_account_id,
        redeemed_at=value.redeemed_at,
        created_at=value.created_at,
        revoked_at=value.revoked_at,
    )


def _account_response(value: Account) -> AccountAdminResponse:
    return AccountAdminResponse(
        id=value.id,
        email=value.display_email,
        role=value.role,
        status=value.status,
        email_verified_at=value.email_verified_at,
        created_at=value.created_at,
    )
