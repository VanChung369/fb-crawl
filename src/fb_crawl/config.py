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
