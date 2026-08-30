from __future__ import annotations

import os
from urllib.parse import unquote, urlparse

import psycopg
import pytest

from fb_data_pipeline.repositories.migrations import MigrationRunner


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


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


pytestmark = pytest.mark.skipif(
    _safe_test_database_name(TEST_DATABASE_URL) is None,
    reason="TEST_DATABASE_URL must target a PostgreSQL database ending in _test",
)


def test_contact_lookup_schema_applies_with_tenant_and_claim_indexes() -> None:
    MigrationRunner(TEST_DATABASE_URL).apply()

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN (
                    'provider_lookup_state', 'enrichment_leases',
                    'lookup_events', 'export_jobs'
                  )
                ORDER BY table_name
                """
            )
            assert len(cursor.fetchall()) == 4
            cursor.execute(
                """
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = 'public'
                  AND table_name = 'account_contact_reveals'
                  AND constraint_name = 'account_contact_reveals_lookup_event_fk'
                """
            )
            assert cursor.fetchone() is not None
