from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Callable
from uuid import UUID

from fastapi import Cookie, Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from fb_crawl.accounts.models import Account, AuthSession, Device, AccountStatus, DeviceStatus
from fb_crawl.accounts.repository import AccountRepository
from fb_crawl.auth.tokens import AccessClaims, InvalidAccessToken, TokenService
from fb_crawl.core.exceptions import FbCrawlError


AUTH_ERROR_BODY = {
    "code": "api_unauthorized",
    "message": "API authentication failed.",
}


class ApiAuthenticationError(FbCrawlError):
    code = "api_unauthorized"

    def __init__(self) -> None:
        super().__init__("API authentication failed.")


class ApiKeyAuth:
    """FastAPI dependency backed by a constant-time shared-key comparison."""

    __slots__ = ("_expected_key_bytes",)

    def __init__(self, expected_key: str) -> None:
        self._expected_key_bytes = expected_key.encode("utf-8")

    def verify(self, provided_key: str | None) -> None:
        candidate = provided_key if isinstance(provided_key, str) else ""
        candidate_bytes = candidate.encode("utf-8")
        if not hmac.compare_digest(candidate_bytes, self._expected_key_bytes):
            raise ApiAuthenticationError

    async def __call__(
        self,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> None:
        self.verify(x_api_key)


class ProductAuthenticationError(FbCrawlError):
    code = "product_unauthorized"

    def __init__(self) -> None:
        super().__init__("Product authentication failed.")


class CsrfValidationError(FbCrawlError):
    code = "csrf_validation_failed"

    def __init__(self) -> None:
        super().__init__("CSRF validation failed.")


@dataclass(frozen=True, slots=True)
class CurrentAccount:
    account: Account
    device: Device
    session: AuthSession
    claims: AccessClaims
    cookie_authenticated: bool
    device_allowed: bool = True


_BEARER = HTTPBearer(auto_error=False)
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class ProductAccountAuth:
    def __init__(
        self,
        repository: AccountRepository,
        token_service: TokenService,
        *,
        allowed_origins: tuple[str, ...],
        entitlement_service: Any | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._token_service = token_service
        self._allowed_origins = frozenset(allowed_origins)
        self._entitlement_service = entitlement_service
        self._clock = clock

    async def __call__(
        self,
        request: Request,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(_BEARER),
        ] = None,
        access_cookie: Annotated[
            str | None,
            Cookie(alias="lead_finder_access"),
        ] = None,
        installation_header: Annotated[
            str | None,
            Header(alias="X-Installation-ID"),
        ] = None,
    ) -> CurrentAccount:
        cookie_authenticated = credentials is None and access_cookie is not None
        raw_access = credentials.credentials if credentials is not None else access_cookie
        if credentials is not None and credentials.scheme.casefold() != "bearer":
            raise ProductAuthenticationError()
        try:
            if raw_access is None or installation_header is None:
                raise ValueError("missing credentials")
            installation_id = UUID(installation_header)
            now = self._clock()
            claims = self._token_service.decode_access(raw_access, now)
            account = self._repository.get_account(claims.account_id)
            device = self._repository.get_device(claims.account_id, claims.device_id)
            session = self._repository.get_session(claims.session_id)
            if (
                account is None
                or account.status is not AccountStatus.ACTIVE
                or device is None
                or device.status is not DeviceStatus.ACTIVE
                or device.installation_id != installation_id
                or session is None
                or session.account_id != account.id
                or session.device_id != device.id
                or session.revoked_at is not None
                or session.expires_at <= now
            ):
                raise ValueError("inactive product identity")
        except (InvalidAccessToken, TypeError, ValueError):
            raise ProductAuthenticationError() from None

        if cookie_authenticated and request.method in _UNSAFE_METHODS:
            validate_cookie_csrf(request, self._allowed_origins)
        device_allowed = True
        if self._entitlement_service is not None:
            entitlements = self._entitlement_service.for_account(account.id, now)
            active_devices = sorted(
                (
                    item
                    for item in self._repository.list_devices(account.id)
                    if item.status is DeviceStatus.ACTIVE
                ),
                key=lambda item: (item.first_seen_at, item.id),
            )
            allowed_ids = {
                item.id for item in active_devices[: entitlements.max_devices]
            }
            device_allowed = device.id in allowed_ids
        return CurrentAccount(
            account=account,
            device=device,
            session=session,
            claims=claims,
            cookie_authenticated=cookie_authenticated,
            device_allowed=device_allowed,
        )


def validate_cookie_csrf(request: Request, allowed_origins: frozenset[str]) -> None:
    cookie_value = request.cookies.get("lead_finder_csrf", "")
    header_value = request.headers.get("X-CSRF-Token", "")
    origin = request.headers.get("Origin", "")
    if (
        not cookie_value
        or not header_value
        or not hmac.compare_digest(cookie_value, header_value)
        or origin not in allowed_origins
    ):
        raise CsrfValidationError()
