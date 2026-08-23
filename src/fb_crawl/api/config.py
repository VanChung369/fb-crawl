from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from fb_crawl.core.exceptions import ConfigurationError


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class ApiSettings:
    api_key: str = field(repr=False)
    cors_origins: tuple[str, ...] = ()
    docs_enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self) -> None:
        _validate_api_key(self.api_key)
        _validate_origins(self.cors_origins)
        if not isinstance(self.docs_enabled, bool):
            raise ConfigurationError("FB_CRAWL_API_DOCS must be a boolean.")
        if not isinstance(self.host, str) or not self.host.strip():
            raise ConfigurationError("FB_CRAWL_API_HOST must not be empty.")
        _validate_port(self.port)


def load_api_settings(env: Mapping[str, str]) -> ApiSettings:
    """Build API settings from an explicitly supplied mapping only."""

    if not isinstance(env, Mapping):
        raise ConfigurationError("API settings require an injected mapping.")

    api_key = env.get("FB_CRAWL_API_KEY", "")
    raw_origins = env.get("FB_CRAWL_API_CORS_ORIGINS", "")
    cors_origins = tuple(
        origin.strip()
        for origin in raw_origins.split(",")
        if origin.strip()
    )
    docs_enabled = _boolean(
        "FB_CRAWL_API_DOCS",
        env.get("FB_CRAWL_API_DOCS", "false"),
    )
    host = env.get("FB_CRAWL_API_HOST", "127.0.0.1")
    port = _port(env.get("FB_CRAWL_API_PORT", "8000"))

    return ApiSettings(
        api_key=api_key,
        cors_origins=cors_origins,
        docs_enabled=docs_enabled,
        host=host,
        port=port,
    )


def _validate_api_key(value: object) -> None:
    if not isinstance(value, str) or len(value.strip()) < 32:
        raise ConfigurationError(
            "FB_CRAWL_API_KEY must contain at least 32 nonblank characters."
        )


def _validate_origins(origins: object) -> None:
    if not isinstance(origins, tuple) or any(
        not isinstance(origin, str) or not origin.strip() or origin == "*"
        for origin in origins
    ):
        raise ConfigurationError(
            "FB_CRAWL_API_CORS_ORIGINS must contain exact origins, not wildcards."
        )


def _boolean(name: str, value: object) -> bool:
    if not isinstance(value, str):
        raise ConfigurationError(f"{name} must be a documented true/false value.")
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ConfigurationError(f"{name} must be a documented true/false value.")


def _port(value: object) -> int:
    if not isinstance(value, str):
        raise ConfigurationError("FB_CRAWL_API_PORT must be an integer.")
    try:
        port = int(value)
    except ValueError as error:
        raise ConfigurationError("FB_CRAWL_API_PORT must be an integer.") from error
    return _validate_port(port)


def _validate_port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError("FB_CRAWL_API_PORT must be an integer.")
    if not 1 <= value <= 65535:
        raise ConfigurationError("FB_CRAWL_API_PORT must be 1 through 65535.")
    return value
