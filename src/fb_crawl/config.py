from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from fb_crawl.core.exceptions import ConfigurationError

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class Settings:
    timeout_seconds: float = 20.0
    max_retries: int = 2
    output_dir: Path = Path("runtime/output")
    user_agent: str = DEFAULT_USER_AGENT


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class BrowserSettings:
    headless: bool = False
    proxy: str | None = None
    session_path: Path = Path("runtime/session.json")
    browser_timeout_seconds: float = 30.0
    verification_timeout_seconds: float = 300.0


def _number(
    name: str,
    value: str,
    cast: type[int] | type[float],
) -> int | float:
    try:
        parsed = cast(value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be numeric.") from error

    if parsed < 0:
        raise ConfigurationError(f"{name} must be 0 or greater.")

    return parsed


def _boolean(name: str, value: str) -> bool:
    normalized = value.strip().lower()

    if normalized in TRUE_VALUES:
        return True

    if normalized in FALSE_VALUES:
        return False

    raise ConfigurationError(f"{name} must be a documented true/false value.")


def _positive_float(
    name: str,
    value: str | float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ConfigurationError(f"{name} must be numeric.") from error

    if parsed <= 0:
        raise ConfigurationError(f"{name} must be greater than 0.")

    return parsed


def validate_session_path(
    path: Path,
    *,
    repository_root: Path,
) -> Path:
    resolved = Path(path)

    if not resolved.is_absolute():
        resolved = repository_root / resolved

    resolved = resolved.resolve()
    repository = repository_root.resolve()
    runtime = (repository / "runtime").resolve()

    try:
        resolved.relative_to(repository)
    except ValueError:
        return resolved

    try:
        resolved.relative_to(runtime)
    except ValueError as error:
        raise ConfigurationError(
            "A repository-local session path must stay under runtime/."
        ) from error

    return resolved


def load_settings(
    env: Mapping[str, str] | None = None,
    *,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    output_dir: Path | None = None,
) -> Settings:
    values = os.environ if env is None else env

    env_timeout = values.get(
        "FB_CRAWL_TIMEOUT_SECONDS",
        "20",
    )
    env_retries = values.get(
        "FB_CRAWL_MAX_RETRIES",
        "2",
    )
    env_output = values.get(
        "FB_CRAWL_OUTPUT_DIR",
        "runtime/output",
    )

    resolved_timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else float(
            _number(
                "FB_CRAWL_TIMEOUT_SECONDS",
                env_timeout,
                float,
            )
        )
    )

    resolved_retries = (
        max_retries
        if max_retries is not None
        else int(
            _number(
                "FB_CRAWL_MAX_RETRIES",
                env_retries,
                int,
            )
        )
    )

    if resolved_timeout <= 0:
        raise ConfigurationError("timeout_seconds must be greater than 0.")

    if resolved_retries < 0:
        raise ConfigurationError("max_retries must be 0 or greater.")

    return Settings(
        timeout_seconds=resolved_timeout,
        max_retries=resolved_retries,
        output_dir=output_dir or Path(env_output),
    )


def load_browser_settings(
    env: Mapping[str, str] | None = None,
    *,
    headless: bool | None = None,
    proxy: str | None = None,
    session_path: Path | None = None,
    browser_timeout_seconds: float | None = None,
    verification_timeout_seconds: float | None = None,
    repository_root: Path | None = None,
) -> BrowserSettings:
    values = os.environ if env is None else env
    root = Path.cwd() if repository_root is None else repository_root

    resolved_headless = (
        headless
        if headless is not None
        else _boolean(
            "FB_CRAWL_HEADLESS",
            values.get("FB_CRAWL_HEADLESS", "false"),
        )
    )

    resolved_proxy = proxy if proxy is not None else values.get("FB_CRAWL_PROXY")

    raw_session = session_path or Path(
        values.get(
            "FB_CRAWL_SESSION_PATH",
            "runtime/session.json",
        )
    )

    browser_timeout = _positive_float(
        "browser_timeout_seconds",
        (
            browser_timeout_seconds
            if browser_timeout_seconds is not None
            else values.get(
                "FB_CRAWL_BROWSER_TIMEOUT_SECONDS",
                "30",
            )
        ),
    )

    verification_timeout = _positive_float(
        "verification_timeout_seconds",
        (
            verification_timeout_seconds
            if verification_timeout_seconds is not None
            else values.get(
                "FB_CRAWL_VERIFICATION_TIMEOUT_SECONDS",
                "300",
            )
        ),
    )

    return BrowserSettings(
        headless=resolved_headless,
        proxy=resolved_proxy or None,
        session_path=validate_session_path(
            raw_session,
            repository_root=root,
        ),
        browser_timeout_seconds=browser_timeout,
        verification_timeout_seconds=verification_timeout,
    )
