from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import os
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

import psycopg
import pytest

from fb_crawl.accounts.postgres import PostgresAccountRepository
from fb_crawl.entitlements.quota import ContactQuotaService, PostgresContactQuotaRepository
from fb_data_pipeline.repositories.migrations import MigrationRunner


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
NOW = datetime(2026, 8, 30, 8, tzinfo=UTC)


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
def clean_quota_data() -> None:
    MigrationRunner(TEST_DATABASE_URL).apply()
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE admin_audit_events, account_contact_reveals,
                    usage_monthly, account_subscriptions, license_keys, accounts,
                    phone_numbers, facebook_users
                RESTART IDENTITY CASCADE
                """
            )


def _seed_contact(facebook_uid: str, phone: str) -> tuple[int, int]:
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO facebook_users (facebook_uid) VALUES (%s) RETURNING id",
                (facebook_uid,),
            )
            user_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO phone_numbers (normalized_phone, display_phone)
                VALUES (%s, %s) RETURNING id
                """,
                (phone, phone),
            )
            phone_id = cursor.fetchone()[0]
    return user_id, phone_id


def _quota() -> ContactQuotaService:
    return ContactQuotaService(
        PostgresContactQuotaRepository(TEST_DATABASE_URL),
        ZoneInfo("Asia/Ho_Chi_Minh"),
    )


def test_same_user_is_charged_once_in_calendar_month() -> None:
    account = PostgresAccountRepository(TEST_DATABASE_URL).create_account(
        "quota@example.com", "quota@example.com", "hash"
    )
    user_id, phone_id = _seed_contact("100000000001", "+84900000001")

    first = _quota().reserve(account.id, user_id, phone_id, None, NOW)
    second = _quota().reserve(account.id, user_id, phone_id, None, NOW)

    assert first.allowed and first.charged
    assert second.allowed and not second.charged
    assert first.used == second.used == 1


def test_two_connections_competing_for_final_slot_have_one_winner() -> None:
    account = PostgresAccountRepository(TEST_DATABASE_URL).create_account(
        "race-quota@example.com", "race-quota@example.com", "hash"
    )
    contacts = (
        _seed_contact("100000000002", "+84900000002"),
        _seed_contact("100000000003", "+84900000003"),
    )
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO usage_monthly (account_id, period_start, used_contact_count)
                VALUES (%s, DATE '2026-08-01', 99)
                """,
                (account.id,),
            )

    def reserve(contact: tuple[int, int]):
        return _quota().reserve(account.id, contact[0], contact[1], None, NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(reserve, contacts))

    assert sum(result.allowed for result in results) == 1
    assert sum(result.charged for result in results) == 1
    assert sorted(result.used for result in results) == [100, 100]
