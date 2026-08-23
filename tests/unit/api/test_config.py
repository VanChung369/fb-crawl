import os

import pytest

from fb_crawl.api.config import ApiSettings, load_api_settings
from fb_crawl.core.exceptions import ConfigurationError


API_KEY = "k" * 32


def test_load_api_settings_uses_only_the_injected_mapping(monkeypatch) -> None:
    """Break caught: API configuration silently reads process variables or .env."""

    monkeypatch.setitem(os.environ, "FB_CRAWL_API_KEY", "process-secret-" * 4)

    settings = load_api_settings({"FB_CRAWL_API_KEY": API_KEY})

    assert settings == ApiSettings(api_key=API_KEY)


def test_api_key_must_be_nonblank_and_at_least_32_characters() -> None:
    """Break caught: a weak or whitespace-only shared secret starts the API."""

    for invalid in ("", " " * 32, "short", "x" * 31 + " " * 100):
        with pytest.raises(ConfigurationError, match="at least 32"):
            load_api_settings({"FB_CRAWL_API_KEY": invalid})


def test_api_key_is_excluded_from_settings_repr() -> None:
    """Break caught: diagnostics accidentally disclose the configured API key."""

    settings = load_api_settings({"FB_CRAWL_API_KEY": API_KEY})

    assert API_KEY not in repr(settings)


def test_cors_origins_are_exact_trimmed_comma_separated_values() -> None:
    """Break caught: CORS becomes wildcard or rewrites configured origins."""

    settings = load_api_settings(
        {
            "FB_CRAWL_API_KEY": API_KEY,
            "FB_CRAWL_API_CORS_ORIGINS": (
                " https://ui.example.test,HTTP://LOCALHOST:5173 "
            ),
        }
    )

    assert settings.cors_origins == (
        "https://ui.example.test",
        "HTTP://LOCALHOST:5173",
    )

    with pytest.raises(ConfigurationError, match="exact origins"):
        load_api_settings(
            {
                "FB_CRAWL_API_KEY": API_KEY,
                "FB_CRAWL_API_CORS_ORIGINS": "*",
            }
        )


def test_docs_bind_host_and_port_have_safe_defaults_and_valid_overrides() -> None:
    """Break caught: API defaults expose docs/network or ignore operator values."""

    defaults = load_api_settings({"FB_CRAWL_API_KEY": API_KEY})
    configured = load_api_settings(
        {
            "FB_CRAWL_API_KEY": API_KEY,
            "FB_CRAWL_API_DOCS": "true",
            "FB_CRAWL_API_HOST": "0.0.0.0",
            "FB_CRAWL_API_PORT": "9001",
        }
    )

    assert defaults.docs_enabled is False
    assert defaults.host == "127.0.0.1"
    assert defaults.port == 8000
    assert configured.docs_enabled is True
    assert configured.host == "0.0.0.0"
    assert configured.port == 9001


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("FB_CRAWL_API_DOCS", "sometimes", "true/false"),
        ("FB_CRAWL_API_HOST", "   ", "(?i)host"),
        ("FB_CRAWL_API_PORT", "zero", "integer"),
        ("FB_CRAWL_API_PORT", "0", "1 through 65535"),
        ("FB_CRAWL_API_PORT", "65536", "1 through 65535"),
    ],
)
def test_invalid_api_setting_values_fail_safely(
    name: str,
    value: str,
    message: str,
) -> None:
    """Break caught: malformed process configuration reaches server startup."""

    with pytest.raises(ConfigurationError, match=message):
        load_api_settings({"FB_CRAWL_API_KEY": API_KEY, name: value})
