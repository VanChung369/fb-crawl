from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from fastapi import APIRouter, Depends, Path

from fb_crawl.accounts.repository import AccountRepository
from fb_crawl.api.dependencies import CurrentAccount, ProductAccountAuth
from fb_crawl.api.product_schemas import (
    AccountDeletionResponse,
    AccountMeResponse,
    DeviceListResponse,
    DeviceResponse,
    DeviceRevokedResponse,
)


def create_product_account_router(
    repository: AccountRepository,
    current_auth: ProductAccountAuth,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(tags=["product-account"])

    @router.get("/api/v1/account/me", response_model=AccountMeResponse)
    def account_me(
        current: CurrentAccount = Depends(current_auth),
    ) -> AccountMeResponse:
        return AccountMeResponse(
            id=current.account.id,
            email=current.account.display_email,
            role=current.account.role,
            status=current.account.status,
            email_verified_at=current.account.email_verified_at,
        )

    @router.delete("/api/v1/account", response_model=AccountDeletionResponse)
    def delete_account(
        current: CurrentAccount = Depends(current_auth),
    ) -> AccountDeletionResponse:
        repository.request_account_deletion(current.account.id, clock())
        return AccountDeletionResponse()

    @router.get("/api/v1/devices", response_model=DeviceListResponse)
    def list_devices(
        current: CurrentAccount = Depends(current_auth),
    ) -> DeviceListResponse:
        return DeviceListResponse(
            items=[
                DeviceResponse(
                    id=device.id,
                    installation_id=device.installation_id,
                    display_name=device.display_name,
                    status=device.status,
                    first_seen_at=device.first_seen_at,
                    last_seen_at=device.last_seen_at,
                    current=device.id == current.device.id,
                )
                for device in repository.list_devices(current.account.id)
            ]
        )

    @router.delete(
        "/api/v1/devices/{device_id}",
        response_model=DeviceRevokedResponse,
    )
    def revoke_device(
        device_id: int = Path(gt=0, le=9223372036854775807),
        current: CurrentAccount = Depends(current_auth),
    ) -> DeviceRevokedResponse:
        repository.revoke_device(current.account.id, device_id, clock())
        return DeviceRevokedResponse(device_id=device_id)

    return router
