"""Stable API failure logging that never serializes exception details."""

from __future__ import annotations

import logging


def log_unexpected_api_error(logger: logging.Logger) -> None:
    logger.error("Unhandled API error.")


def log_readiness_failure(logger: logging.Logger) -> None:
    logger.warning("API readiness check failed.")
