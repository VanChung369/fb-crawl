from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from urllib.parse import unquote, urlparse
from uuid import UUID

import psycopg
import pytest

from fb_crawl.accounts.repository import SessionReuseDetected
from fb_crawl.accounts.postgres import PostgresAccountRepository
from fb_data_pipeline.repositories.migrations import MigrationRunner


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
NOW = datetime(2026, 8, 30, 8, tzinfo=UTC)
INSTALLATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def _safe_test_database_name(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return None
    name = unquote(parsed.path.removeprefix("/"))
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not hostname
        or parsed.query
        or parsed.fragment
        or not name.endswith("_test")
        or "/" in name
        or "\\" in name
    ):
        return None
    return name


TEST_DATABASE_NAME = _safe_test_database_name(TEST_DATABASE_URL)
pytestmark = pytest.mark.skipif(
    TEST_DATABASE_NAME is None,
    reason="TEST_DATABASE_URL must target a PostgreSQL database ending in _test",
)


@pytest.fixture(autouse=True)
def clean_product_accounts() -> None:
    MigrationRunner(TEST_DATABASE_URL).apply()
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE TABLE accounts, rate_limit_buckets RESTART IDENTITY CASCADE"
            )


def test_account_verification_device_and_refresh_rotation_are_transactional() -> None:
    repository = PostgresAccountRepository(TEST_DATABASE_URL)
    account = repository.create_account(
        "person@example.com",
        "Person@example.com",
        "$argon2id$test-hash",
    )
    repository.create_account_token(
        account.id,
        "email_verify",
        "verify-digest",
        NOW + timedelta(days=1),
        NOW,
    )
    verified = repository.verify_email_token("verify-digest", NOW)
    device = repository.create_device(
        account.id,
        INSTALLATION_ID,
        "Chrome on Windows",
        NOW,
    )
    session = repository.create_session(
        account.id,
        device.id,
        "refresh-digest",
        NOW + timedelta(days=30),
        NOW,
    )
    rotated = repository.rotate_session(
        "refresh-digest",
        "next-digest",
        NOW + timedelta(minutes=1),
        NOW + timedelta(days=30),
    )

    assert verified.email_verified_at == NOW
    assert verified.status.value == "active"
    assert rotated.rotated_from_id == session.id
    with pytest.raises(SessionReuseDetected):
        repository.rotate_session(
            "refresh-digest",
            "reused-digest",
            NOW + timedelta(minutes=2),
            NOW + timedelta(days=30),
        )

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM auth_sessions WHERE account_id = %s AND revoked_at IS NOT NULL",
                (account.id,),
            )
            assert cursor.fetchone() == (2,)
