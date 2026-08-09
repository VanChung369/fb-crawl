from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from fb_crawl.core.exceptions import ConfigurationError


DEFAULT_FB_NUMBER_API_URL = "https://api.fbnumber.com/v1/phone/search"


def _positive_float(name: str, raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be numeric.") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than 0.")
    return value


def _non_negative_int(name: str, raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be numeric.") from error
    if value < 0:
        raise ConfigurationError(f"{name} must be 0 or greater.")
    return value


@dataclass(frozen=True, slots=True)
class PipelineSettings:
    default_country_code: str = "84"
    fb_number_api_url: str = DEFAULT_FB_NUMBER_API_URL
    fb_number_api_token: str = ""
    fb_number_auth_header: str = "Authorization"
    fb_number_auth_scheme: str = "Bearer"
    fb_number_timeout_seconds: float = 15.0
    fb_number_max_retries: int = 2

    def require_fb_number(self) -> None:
        if not self.fb_number_api_url:
            raise ConfigurationError("FB_NUMBER_API_URL is required.")
        if not self.fb_number_api_token:
            raise ConfigurationError("FB_NUMBER_API_TOKEN is required.")
        if not self.fb_number_auth_header:
            raise ConfigurationError("FB_NUMBER_AUTH_HEADER is required.")


def load_pipeline_settings(
    env: Mapping[str, str] | None = None,
) -> PipelineSettings:
    values = os.environ if env is None else env
    country_code = "".join(
        character
        for character in values.get("PIPELINE_DEFAULT_COUNTRY_CODE", "84")
        if character.isdigit()
    )
    if not country_code:
        raise ConfigurationError(
            "PIPELINE_DEFAULT_COUNTRY_CODE must contain digits."
        )

    return PipelineSettings(
        default_country_code=country_code,
        fb_number_api_url=values.get(
            "FB_NUMBER_API_URL",
            DEFAULT_FB_NUMBER_API_URL,
        ).strip(),
        fb_number_api_token=values.get("FB_NUMBER_API_TOKEN", "").strip(),
        fb_number_auth_header=values.get(
            "FB_NUMBER_AUTH_HEADER",
            "Authorization",
        ).strip(),
        fb_number_auth_scheme=values.get(
            "FB_NUMBER_AUTH_SCHEME",
            "Bearer",
        ).strip(),
        fb_number_timeout_seconds=_positive_float(
            "FB_NUMBER_TIMEOUT_SECONDS",
            values.get("FB_NUMBER_TIMEOUT_SECONDS", "15"),
        ),
        fb_number_max_retries=_non_negative_int(
            "FB_NUMBER_MAX_RETRIES",
            values.get("FB_NUMBER_MAX_RETRIES", "2"),
        ),
    )

