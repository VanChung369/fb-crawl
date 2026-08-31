"""Stable API failure logging that never serializes exception details."""

from __future__ import annotations

import logging

from fb_crawl.api.correlation import current_request_id


def log_unexpected_api_error(
    logger: logging.Logger,
    *,
    request_id: str | None = None,
) -> None:
    logger.error(
        "Unhandled API error.",
        extra={"request_id": request_id or current_request_id() or "unavailable"},
    )


def log_readiness_failure(logger: logging.Logger) -> None:
    logger.warning(
        "API readiness check failed.",
        extra={"request_id": current_request_id() or "unavailable"},
    )
