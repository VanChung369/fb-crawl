from __future__ import annotations

import logging

from fb_crawl.api.safe_logging import (
    log_readiness_failure,
    log_unexpected_api_error,
)


def _raise_secret_error() -> None:
    try:
        raise RuntimeError("provider_token=provider-private-value")
    except RuntimeError as cause:
        raise RuntimeError(
            "api_key=api-private-value "
            "dsn=postgresql://secret-user:secret-pass@private/db"
        ) from cause


def test_generic_api_logger_never_records_exception_or_cause(caplog) -> None:
    logger = logging.getLogger("fb_crawl.api.test.generic")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        try:
            _raise_secret_error()
        except RuntimeError:
            log_unexpected_api_error(logger)

    combined = "\n".join(record.getMessage() for record in caplog.records)
    assert combined == "Unhandled API error."
    assert "api-private-value" not in combined
    assert "postgresql" not in combined
    assert "provider-private-value" not in combined
    assert all(record.exc_info is None for record in caplog.records)


def test_readiness_logger_never_records_exception_or_cause(caplog) -> None:
    logger = logging.getLogger("fb_crawl.api.test.readiness")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        try:
            _raise_secret_error()
        except RuntimeError:
            log_readiness_failure(logger)

    combined = "\n".join(record.getMessage() for record in caplog.records)
    assert combined == "API readiness check failed."
    assert "api-private-value" not in combined
    assert "postgresql" not in combined
    assert "provider-private-value" not in combined
    assert all(record.exc_info is None for record in caplog.records)
