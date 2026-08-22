from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation, UniqueViolation

from fb_data_pipeline.repositories.migrations import MigrationRunner


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not configured",
)


def _assert_dedicated_test_database() -> None:
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        if not connection.info.dbname.endswith("_test"):
            pytest.fail(
                "TEST_DATABASE_URL must target a database ending in _test"
            )


@pytest.fixture(autouse=True)
def clean_job_tables() -> None:
    _assert_dedicated_test_database()
    MigrationRunner(TEST_DATABASE_URL).apply()
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM crawl_jobs")
            cursor.execute(
                """
                UPDATE crawler_account_state
                SET
                    status = 'ready',
                    cooldown_until = NULL,
                    last_job_id = NULL,
                    last_started_at = NULL,
                    last_finished_at = NULL,
                    rate_limit_count_24h = 0,
                    last_rate_limit_at = NULL,
                    last_warning_code = '',
                    last_warning_at = NULL,
                    block_reason = '',
                    acknowledged_at = NULL
                WHERE account_key = 'default'
                """
            )


def _insert_job(
    cursor: psycopg.Cursor[tuple[object, ...]],
    *,
    status: str,
    requested_targets: int = 0,
) -> None:
    cursor.execute(
        """
        INSERT INTO crawl_jobs (
            id, mode, action, status, request_fingerprint, requested_targets
        )
        VALUES (%s, 'authenticated', 'members', %s, %s, %s)
        """,
        (uuid4(), status, str(uuid4()), requested_targets),
    )


def test_job_orchestration_tables_and_default_account_are_migrated() -> None:
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
            table_names = {row[0] for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT account_key, status
                FROM crawler_account_state
                WHERE account_key = 'default'
                """
            )
            account_row = cursor.fetchone()

    assert table_names >= {
        "crawl_jobs",
        "crawl_targets",
        "crawl_job_events",
        "crawler_account_state",
    }
    assert account_row == ("default", "ready")


def test_job_schema_rejects_invalid_statuses_and_counters() -> None:
    with pytest.raises(CheckViolation):
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                _insert_job(cursor, status="invalid")

    with pytest.raises(CheckViolation):
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                _insert_job(cursor, status="queued", requested_targets=-1)


def test_one_active_job_per_default_account_but_queued_jobs_can_coexist() -> None:
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            _insert_job(cursor, status="running")

            with pytest.raises(UniqueViolation):
                with connection.transaction():
                    _insert_job(cursor, status="cancelling")

            _insert_job(cursor, status="queued")
            _insert_job(cursor, status="queued")
            cursor.execute(
                """
                SELECT count(*)
                FROM crawl_jobs
                WHERE status = 'queued' AND account_key = 'default'
                """
            )
            queued_count = cursor.fetchone()

    assert queued_count == (2,)
