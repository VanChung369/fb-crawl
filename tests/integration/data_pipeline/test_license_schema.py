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


TEST_DATABASE_NAME = _safe_test_database_name(TEST_DATABASE_URL)
pytestmark = pytest.mark.skipif(
    TEST_DATABASE_NAME is None,
    reason="TEST_DATABASE_URL must target a PostgreSQL database ending in _test",
)


def test_license_schema_applies_and_protects_default_plan() -> None:
    MigrationRunner(TEST_DATABASE_URL).apply()

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT monthly_contact_limit, max_devices,
                       allow_group_crawl, allow_comment_crawl, is_system
                FROM plans WHERE code = 'default'
                """
            )
            assert cursor.fetchone() == (100, 1, False, False, True)
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN (
                    'license_keys', 'account_subscriptions', 'usage_monthly',
                    'account_contact_reveals', 'admin_audit_events'
                  )
                ORDER BY table_name
                """
            )
            assert len(cursor.fetchall()) == 5
