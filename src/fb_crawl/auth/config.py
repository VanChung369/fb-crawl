from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fb_crawl.core.exceptions import ConfigurationError


DEFAULT_PRODUCT_TIMEZONE = "Asia/Ho_Chi_Minh"
DEVELOPMENT_ENVIRONMENT = "development"


@dataclass(frozen=True, slots=True)
class AuthSettings:
    jwt_secret: str = field(repr=False)
    token_hmac_secret: str = field(repr=False)
    public_base_url: str
    product_timezone: ZoneInfo
    access_ttl_seconds: int = 900


def load_auth_settings(env: Mapping[str, str]) -> AuthSettings:
    """Load product authentication settings from an injected environment."""

    if not isinstance(env, Mapping):
        raise ConfigurationError("Auth settings require an injected mapping.")

    jwt_secret = _secret(
        "JWT secret",
        env.get("LEAD_FINDER_JWT_SECRET", ""),
    )
    token_hmac_secret = _secret(
        "token HMAC secret",
        env.get("LEAD_FINDER_TOKEN_HMAC_SECRET", ""),
    )
    environment = env.get("LEAD_FINDER_ENV", "production").strip().lower()
    public_base_url = _public_base_url(
        env.get("LEAD_FINDER_PUBLIC_BASE_URL", ""),
        development=environment == DEVELOPMENT_ENVIRONMENT,
    )
    timezone_name = env.get(
        "LEAD_FINDER_TIMEZONE",
        DEFAULT_PRODUCT_TIMEZONE,
    ).strip()
    try:
        product_timezone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ConfigurationError(
            "LEAD_FINDER_TIMEZONE must name a valid IANA timezone."
        ) from error

    return AuthSettings(
        jwt_secret=jwt_secret,
        token_hmac_secret=token_hmac_secret,
        public_base_url=public_base_url,
        product_timezone=product_timezone,
    )


def _secret(label: str, value: object) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or len(value.strip()) < 32
    ):
        raise ConfigurationError(
            f"Lead Finder {label} must contain at least 32 nonblank characters."
        )
    return value


def _public_base_url(value: object, *, development: bool) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("LEAD_FINDER_PUBLIC_BASE_URL is required.")
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ConfigurationError(
            "LEAD_FINDER_PUBLIC_BASE_URL must be an origin without credentials, path, query, or fragment."
        )
    if parsed.scheme == "https":
        return normalized
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if development and parsed.scheme == "http" and parsed.hostname in local_hosts:
        return normalized
    raise ConfigurationError(
        "LEAD_FINDER_PUBLIC_BASE_URL must use HTTPS outside local development."
    )
