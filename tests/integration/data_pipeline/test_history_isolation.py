from __future__ import annotations

import os
from datetime import UTC, date, datetime
from urllib.parse import unquote, urlparse

import psycopg
import pytest

from fb_crawl.history.models import AccountHistoryQuery
from fb_crawl.history.postgres import PostgresHistoryRepository
from fb_data_pipeline.repositories.migrations import MigrationRunner


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
NOW = datetime(2026, 8, 31, 3, tzinfo=UTC)


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


def test_history_is_tenant_private_and_deletion_preserves_quota() -> None:
    MigrationRunner(TEST_DATABASE_URL).apply()
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE TABLE accounts, facebook_users RESTART IDENTITY CASCADE"
            )
            cursor.execute(
                """
                INSERT INTO facebook_users (
                    facebook_uid, facebook_username, normalized_username,
                    display_name, profile_url
                ) VALUES ('100123', 'sample.user', 'sample.user',
                          'Sample User', 'https://www.facebook.com/sample.user')
                RETURNING id
                """
            )
            user_id = int(cursor.fetchone()[0])
            cursor.execute(
                """
                INSERT INTO phone_numbers (normalized_phone, display_phone)
                VALUES ('+84981234567', '0981234567') RETURNING id
                """
            )
            phone_id = int(cursor.fetchone()[0])
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
                        f"history-{index}@example.test",
                        f"history-{index}@example.test",
                        NOW,
                    ),
                )
                account_ids.append(int(cursor.fetchone()[0]))
            cursor.execute(
                """
                INSERT INTO lookup_events (
                    account_id, facebook_user_id, requested_uid, outcome,
                    result_source, quota_charged, created_at, completed_at,
                    revealed_phone_number_id, revealed_observed_at
                ) VALUES (%s, %s, '100123', 'found', 'cache', true,
                          %s, %s, %s, %s)
                RETURNING id
                """,
                (account_ids[0], user_id, NOW, NOW, phone_id, NOW),
            )
            event_id = int(cursor.fetchone()[0])
            cursor.execute(
                """
                INSERT INTO usage_monthly (
                    account_id, period_start, used_contact_count
                ) VALUES (%s, %s, 1)
                """,
                (account_ids[0], date(2026, 8, 1)),
            )
            cursor.execute(
                """
                INSERT INTO account_contact_reveals (
                    account_id, facebook_user_id, phone_number_id,
                    lookup_event_id, period_start, revealed_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    account_ids[0], user_id, phone_id, event_id,
                    date(2026, 8, 1), NOW,
                ),
            )

    repository = PostgresHistoryRepository(TEST_DATABASE_URL)
    assert len(repository.list(AccountHistoryQuery(account_ids[0])).items) == 1
    assert repository.list(AccountHistoryQuery(account_ids[1])).items == ()
    assert repository.delete_one(account_ids[1], event_id) is False
    assert repository.delete_one(account_ids[0], event_id) is True

    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT reveals.lookup_event_id, usage.used_contact_count
                FROM account_contact_reveals AS reveals
                JOIN usage_monthly AS usage
                  ON usage.account_id = reveals.account_id
                 AND usage.period_start = reveals.period_start
                WHERE reveals.account_id = %s
                """,
                (account_ids[0],),
            )
            assert cursor.fetchone() == (None, 1)
