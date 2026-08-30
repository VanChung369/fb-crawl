from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from fb_crawl.auth.config import load_auth_settings
from fb_crawl.core.exceptions import ConfigurationError


VALID_ENV = {
    "LEAD_FINDER_JWT_SECRET": "j" * 32,
    "LEAD_FINDER_TOKEN_HMAC_SECRET": "h" * 32,
    "LEAD_FINDER_PUBLIC_BASE_URL": "https://leads.example.com",
}


def test_auth_settings_load_secure_defaults_without_exposing_secrets() -> None:
    settings = load_auth_settings(VALID_ENV)

    assert settings.public_base_url == "https://leads.example.com"
    assert settings.product_timezone == ZoneInfo("Asia/Ho_Chi_Minh")
    assert settings.access_ttl_seconds == 900
    assert VALID_ENV["LEAD_FINDER_JWT_SECRET"] not in repr(settings)
    assert VALID_ENV["LEAD_FINDER_TOKEN_HMAC_SECRET"] not in repr(settings)


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("LEAD_FINDER_JWT_SECRET", "JWT secret"),
        ("LEAD_FINDER_TOKEN_HMAC_SECRET", "token HMAC secret"),
    ],
)
def test_auth_settings_reject_short_or_blank_secrets(name: str, message: str) -> None:
    env = {**VALID_ENV, name: " short secret padded with spaces     "}

    with pytest.raises(ConfigurationError, match=message):
        load_auth_settings(env)


def test_auth_settings_require_https_outside_development() -> None:
    env = {
        **VALID_ENV,
        "LEAD_FINDER_PUBLIC_BASE_URL": "http://leads.example.com",
    }

    with pytest.raises(ConfigurationError, match="HTTPS"):
        load_auth_settings(env)


def test_auth_settings_allow_local_http_in_development() -> None:
    settings = load_auth_settings(
        {
            **VALID_ENV,
            "LEAD_FINDER_ENV": "development",
            "LEAD_FINDER_PUBLIC_BASE_URL": "http://127.0.0.1:8000/",
        }
    )

    assert settings.public_base_url == "http://127.0.0.1:8000"


def test_auth_settings_reject_unknown_timezone() -> None:
    with pytest.raises(ConfigurationError, match="timezone"):
        load_auth_settings(
            {**VALID_ENV, "LEAD_FINDER_TIMEZONE": "Mars/Olympus_Mons"}
        )


def test_auth_settings_require_an_injected_mapping() -> None:
    with pytest.raises(ConfigurationError, match="mapping"):
        load_auth_settings(None)  # type: ignore[arg-type]
