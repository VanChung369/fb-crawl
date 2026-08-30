from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import secrets
from uuid import UUID

import jwt

from fb_crawl.core.exceptions import ValidationError


ACCESS_ISSUER = "lead-finder"
ACCESS_AUDIENCE = "lead-finder-product"


class InvalidAccessToken(ValidationError):
    code = "access_token_invalid"


class AccessTokenExpired(InvalidAccessToken):
    code = "access_token_expired"


@dataclass(frozen=True, slots=True)
class AccessClaims:
    account_id: int
    session_id: UUID
    device_id: int
    issued_at: datetime
    expires_at: datetime


class TokenService:
    def __init__(
        self,
        *,
        jwt_secret: str,
        token_hmac_secret: str,
        access_ttl_seconds: int = 900,
        issuer: str = ACCESS_ISSUER,
        audience: str = ACCESS_AUDIENCE,
    ) -> None:
        self._jwt_secret = _valid_secret("jwt_secret", jwt_secret)
        self._token_hmac_secret = _valid_secret(
            "token_hmac_secret", token_hmac_secret
        ).encode("utf-8")
        if isinstance(access_ttl_seconds, bool) or access_ttl_seconds <= 0:
            raise ValueError("access_ttl_seconds must be positive")
        self._access_ttl = timedelta(seconds=access_ttl_seconds)
        self._issuer = issuer
        self._audience = audience

    @property
    def access_ttl_seconds(self) -> int:
        return int(self._access_ttl.total_seconds())

    def issue_access(
        self,
        account_id: int,
        session_id: UUID,
        device_id: int,
        now: datetime,
    ) -> str:
        _aware(now)
        if account_id <= 0 or device_id <= 0:
            raise ValueError("account_id and device_id must be positive")
        if not isinstance(session_id, UUID):
            raise ValueError("session_id must be a UUID")
        expires_at = now + self._access_ttl
        return jwt.encode(
            {
                "sub": str(account_id),
                "sid": str(session_id),
                "did": device_id,
                "iat": int(now.timestamp()),
                "exp": int(expires_at.timestamp()),
                "iss": self._issuer,
                "aud": self._audience,
            },
            self._jwt_secret,
            algorithm="HS256",
        )

    def decode_access(self, token: str, now: datetime) -> AccessClaims:
        _aware(now)
        if not isinstance(token, str) or not token:
            raise InvalidAccessToken("Access token is invalid.")
        try:
            payload = jwt.decode(
                token,
                self._jwt_secret,
                algorithms=["HS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["sub", "sid", "did", "iat", "exp", "iss", "aud"],
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
            account_id = _positive_int(payload["sub"])
            device_id = _positive_int(payload["did"])
            session_id = UUID(payload["sid"])
            issued_at = _claim_time(payload["iat"])
            expires_at = _claim_time(payload["exp"])
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as error:
            raise InvalidAccessToken("Access token is invalid.") from error
        if expires_at <= now:
            raise AccessTokenExpired("Access token has expired.")
        if issued_at > now or expires_at <= issued_at:
            raise InvalidAccessToken("Access token is invalid.")
        return AccessClaims(
            account_id=account_id,
            session_id=session_id,
            device_id=device_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )

    def new_opaque_token(self) -> str:
        return secrets.token_urlsafe(32)

    def digest_opaque(self, token: str) -> str:
        if not isinstance(token, str) or not token:
            raise ValueError("opaque token must not be empty")
        return hmac.new(
            self._token_hmac_secret,
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def matches_opaque(self, token: str, expected_digest: str) -> bool:
        if not isinstance(expected_digest, str):
            return False
        try:
            actual_digest = self.digest_opaque(token)
        except ValueError:
            return False
        return hmac.compare_digest(actual_digest, expected_digest)


def _valid_secret(name: str, value: object) -> str:
    if not isinstance(value, str) or len(value.strip()) < 32:
        raise ValueError(f"{name} must contain at least 32 nonblank characters")
    return value


def _aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("time must be timezone-aware")


def _positive_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("identity claim must be positive")
    parsed = int(value)
    if parsed <= 0 or str(parsed) != str(value):
        raise ValueError("identity claim must be positive")
    return parsed


def _claim_time(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timestamp claim must be numeric")
    return datetime.fromtimestamp(value, tz=UTC)
