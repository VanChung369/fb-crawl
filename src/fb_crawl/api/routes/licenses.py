from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from fastapi import APIRouter, Depends

from fb_crawl.api.dependencies import CurrentAccount, ProductAccountAuth
from fb_crawl.api.product_schemas import (
    EntitlementsResponse,
    LicenseDurationRequest,
    RedeemLicenseRequest,
    SubscriptionResponse,
)
from fb_crawl.entitlements.models import Entitlements
from fb_crawl.entitlements.service import EntitlementService
from fb_crawl.licenses.models import Subscription
from fb_crawl.licenses.service import LicenseService


def create_license_router(
    licenses: LicenseService,
    entitlements: EntitlementService,
    current_auth: ProductAccountAuth,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(tags=["product-licenses"])

    @router.post(
        "/api/v1/licenses/redeem", response_model=SubscriptionResponse
    )
    def redeem_license(
        payload: RedeemLicenseRequest,
        current: CurrentAccount = Depends(current_auth),
    ) -> SubscriptionResponse:
        return _subscription_response(
            licenses.redeem(current.account.id, payload.key, clock())
        )

    @router.get(
        "/api/v1/account/entitlements", response_model=EntitlementsResponse
    )
    def account_entitlements(
        current: CurrentAccount = Depends(current_auth),
    ) -> EntitlementsResponse:
        return _entitlements_response(
            entitlements.for_account(current.account.id, clock())
        )

    return router


def _entitlements_response(value: Entitlements) -> EntitlementsResponse:
    return EntitlementsResponse(
        monthly_contact_limit=value.monthly_contact_limit,
        max_devices=value.max_devices,
        allow_group_crawl=value.allow_group_crawl,
        allow_comment_crawl=value.allow_comment_crawl,
        subscription_id=value.subscription_id,
        starts_at=value.starts_at,
        ends_at=value.ends_at,
    )


def _subscription_response(value: Subscription) -> SubscriptionResponse:
    return SubscriptionResponse(
        subscription_id=value.id,
        account_id=value.account_id,
        license_key_id=value.license_key_id,
        duration=LicenseDurationRequest(
            unit=value.grant.duration.unit, value=value.grant.duration.value
        ),
        monthly_contact_limit=value.grant.monthly_contact_limit,
        max_devices=value.grant.max_devices,
        allow_group_crawl=value.grant.allow_group_crawl,
        allow_comment_crawl=value.grant.allow_comment_crawl,
        starts_at=value.starts_at,
        ends_at=value.ends_at,
        status=value.status,
    )
