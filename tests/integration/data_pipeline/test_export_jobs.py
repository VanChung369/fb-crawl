from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlparse
from uuid import UUID

import psycopg
import pytest

from fb_crawl.exports.models import ExportFormat
from fb_crawl.exports.postgres import PostgresExportRepository
from fb_data_pipeline.repositories.migrations import MigrationRunner


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
NOW = datetime(2026, 8, 31, 3, tzinfo=UTC)
JOB_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _safe_test_database(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(value)
        parsed.port
    except ValueError:
        return False
    name = unquote(parsed.path.removeprefix("/"))
    return bool(
        parsed.scheme in {"postgres", "postgresql"}
        and parsed.hostname
        and not parsed.query
        and not parsed.fragment
        and name.endswith("_test")
        and "/" not in name
        and "\\" not in name
    )


pytestmark = pytest.mark.skipif(
    not _safe_test_database(TEST_DATABASE_URL),
    reason="TEST_DATABASE_URL must target a PostgreSQL database ending in _test",
)


def test_export_jobs_are_tenant_private_and_expired_leases_are_recoverable() -> None:
    MigrationRunner(TEST_DATABASE_URL).apply()
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE TABLE accounts RESTART IDENTITY CASCADE")
            account_ids = []
            for index in (1, 2):
                cursor.execute(
                    """
                    INSERT INTO accounts (
                        normalized_email, display_email, password_hash,
                        status, email_verified_at
                    ) VALUES (%s, %s, 'test-placeholder', 'active', %s)
                    RETURNING id
                    """,
                    (
                        f"export-{index}@example.test",
                        f"export-{index}@example.test",
                        NOW,
                    ),
                )
                account_ids.append(int(cursor.fetchone()[0]))

    repository = PostgresExportRepository(TEST_DATABASE_URL)
    created = repository.create(
        account_ids[0],
        ExportFormat.CSV,
        {"outcome": "found"},
        NOW,
        job_id=JOB_A,
    )
    assert repository.get(account_ids[1], created.id) is None
    first = repository.claim_next("worker-old", NOW)
    assert first is not None and first.id == JOB_A
    assert repository.claim_next("worker-new", NOW + timedelta(minutes=1)) is None
    recovered = repository.claim_next(
        "worker-new", NOW + timedelta(minutes=16)
    )
    assert recovered is not None and recovered.attempt_count == 2
    assert repository.complete(
        JOB_A,
        "worker-old",
        "old.csv",
        NOW + timedelta(minutes=16),
        NOW + timedelta(hours=24),
    ) is False
    assert repository.complete(
        JOB_A,
        "worker-new",
        "new.csv",
        NOW + timedelta(minutes=17),
        NOW + timedelta(hours=24),
    ) is True
    assert repository.delete(account_ids[1], JOB_A) == (False, "")
    assert repository.delete(account_ids[0], JOB_A) == (True, "new.csv")
