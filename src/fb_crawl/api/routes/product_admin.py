from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from fb_crawl.accounts.models import Account, AccountRole
from fb_crawl.accounts.repository import AccountRepository
from fb_crawl.api.dependencies import CurrentAccount, ProductAccountAuth
from fb_crawl.api.product_schemas import (
    AccountAdminListResponse,
    AccountAdminResponse,
    CreatedLicenseKeyResponse,
    DeviceRevokedResponse,
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


def create_product_admin_router(
    accounts: AccountRepository,
    licenses: LicenseService,
    current_auth: ProductAccountAuth,
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
        if clock() - current.claims.issued_at > timedelta(minutes=15):
            raise HTTPException(status_code=403, detail="Recent authentication required.")
        return current

    @router.post("/license-keys", response_model=CreatedLicenseKeyResponse)
    def create_license_key(
        payload: LicenseGrantRequest,
        current: CurrentAccount = Depends(require_admin),
    ) -> CreatedLicenseKeyResponse:
        grant = LicenseGrant(
            LicenseDuration(payload.duration.unit, payload.duration.value),
            payload.monthly_contact_limit,
            payload.max_devices,
            payload.allow_group_crawl,
            payload.allow_comment_crawl,
        )
        key, plaintext = licenses.create_key(grant, current.account.id, clock())
        return CreatedLicenseKeyResponse(
            **_license_response(key).model_dump(), key=plaintext
        )

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
        key_id: int = Path(gt=0),
        current: CurrentAccount = Depends(require_admin),
    ) -> LicenseKeyResponse:
        return _license_response(
            licenses.revoke_key(key_id, current.account.id, clock())
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

    @router.post(
        "/accounts/{account_id}/suspend", response_model=AccountAdminResponse
    )
    def suspend_account(
        account_id: int = Path(gt=0),
        _current: CurrentAccount = Depends(require_recent_admin),
    ) -> AccountAdminResponse:
        now = clock()
        account = accounts.suspend_account(account_id, now)
        licenses.write_audit(
            actor_account_id=_current.account.id,
            action="account_suspended",
            target_type="account",
            target_id=str(account_id),
            details={},
            now=now,
        )
        return _account_response(account)

    @router.delete(
        "/accounts/{account_id}/devices/{device_id}",
        response_model=DeviceRevokedResponse,
    )
    def revoke_account_device(
        account_id: int = Path(gt=0),
        device_id: int = Path(gt=0),
        _current: CurrentAccount = Depends(require_recent_admin),
    ) -> DeviceRevokedResponse:
        now = clock()
        accounts.revoke_device(account_id, device_id, now)
        licenses.write_audit(
            actor_account_id=_current.account.id,
            action="account_device_revoked",
            target_type="device",
            target_id=str(device_id),
            details={"account_id": account_id},
            now=now,
        )
        return DeviceRevokedResponse(device_id=device_id)

    @router.delete(
        "/accounts/{account_id}/sessions",
        response_model=SessionsRevokedResponse,
    )
    def revoke_account_sessions(
        account_id: int = Path(gt=0),
        _current: CurrentAccount = Depends(require_recent_admin),
    ) -> SessionsRevokedResponse:
        now = clock()
        accounts.revoke_account_sessions(account_id, now)
        licenses.write_audit(
            actor_account_id=_current.account.id,
            action="account_sessions_revoked",
            target_type="account",
            target_id=str(account_id),
            details={},
            now=now,
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
        account_id: int = Path(gt=0),
        subscription_id: int = Path(gt=0),
        current: CurrentAccount = Depends(require_admin),
    ) -> SubscriptionListResponse:
        shifted = licenses.start_subscription_now(
            account_id, subscription_id, current.account.id, clock()
        )
        return SubscriptionListResponse(
            items=[_subscription_response(subscription) for subscription in shifted]
        )

    return router


def _license_response(value: LicenseKey) -> LicenseKeyResponse:
    return LicenseKeyResponse(
        id=value.id,
        masked_key=value.masked_key,
        key_version=value.key_version,
        duration=LicenseDurationRequest(
            unit=value.grant.duration.unit, value=value.grant.duration.value
        ),
        monthly_contact_limit=value.grant.monthly_contact_limit,
        max_devices=value.grant.max_devices,
        allow_group_crawl=value.grant.allow_group_crawl,
        allow_comment_crawl=value.grant.allow_comment_crawl,
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
