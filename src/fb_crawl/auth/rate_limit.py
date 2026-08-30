from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
from typing import Protocol

from fb_crawl.core.exceptions import ValidationError


class AuthRateLimited(ValidationError):
    code = "auth_rate_limited"


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    requests: int
    window_seconds: int


DEFAULT_AUTH_POLICIES = {
    "register": RateLimitPolicy(5, 15 * 60),
    "verify_email": RateLimitPolicy(10, 15 * 60),
    "resend_verification": RateLimitPolicy(3, 60 * 60),
    "login": RateLimitPolicy(10, 15 * 60),
    "refresh": RateLimitPolicy(30, 5 * 60),
    "forgot_password": RateLimitPolicy(5, 60 * 60),
    "reset_password": RateLimitPolicy(5, 60 * 60),
    "reauthenticate": RateLimitPolicy(5, 15 * 60),
    "license_redeem": RateLimitPolicy(10, 15 * 60),
    "admin_license_create": RateLimitPolicy(10, 60 * 60),
    "admin_license_revoke": RateLimitPolicy(30, 60 * 60),
    "admin_account_suspend": RateLimitPolicy(20, 60 * 60),
    "admin_device_revoke": RateLimitPolicy(30, 60 * 60),
    "admin_sessions_revoke": RateLimitPolicy(30, 60 * 60),
    "admin_subscription_start_now": RateLimitPolicy(20, 60 * 60),
}


class RateLimitRepository(Protocol):
    def record_rate_limit_hit(
        self,
        bucket_hash: str,
        action: str,
        window_start: datetime,
        expires_at: datetime,
    ) -> int: ...


class RateLimitService:
    def __init__(
        self,
        repository: RateLimitRepository,
        hmac_secret: str,
        *,
        policies: dict[str, RateLimitPolicy] | None = None,
    ) -> None:
        if len(hmac_secret.strip()) < 32:
            raise ValueError("rate-limit HMAC secret must contain at least 32 characters")
        self._repository = repository
        self._secret = hmac_secret.encode("utf-8")
        self._policies = dict(DEFAULT_AUTH_POLICIES if policies is None else policies)

    def check(
        self,
        action: str,
        account_key: str | None,
        device_key: str | None,
        ip_address: str,
        now: datetime,
    ) -> None:
        policy = self._policies.get(action)
        if policy is None:
            raise ValueError("unknown rate-limit action")
        if now.tzinfo is None:
            raise ValueError("rate-limit time must be timezone-aware")
        window_start = datetime.fromtimestamp(
            int(now.timestamp()) // policy.window_seconds * policy.window_seconds,
            tz=UTC,
        )
        expires_at = window_start + timedelta(seconds=policy.window_seconds * 2)
        rotation = now.astimezone(UTC).date().isoformat()
        identities = [("ip", ip_address)]
        if account_key:
            identities.append(("account", account_key))
        if device_key:
            identities.append(("device", device_key))

        limited = False
        for kind, value in identities:
            digest = hmac.new(
                self._secret,
                f"{rotation}:{kind}:{value}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            count = self._repository.record_rate_limit_hit(
                digest,
                action,
                window_start,
                expires_at,
            )
            limited = limited or count > policy.requests
        if limited:
            raise AuthRateLimited("Too many authentication requests. Try again later.")
