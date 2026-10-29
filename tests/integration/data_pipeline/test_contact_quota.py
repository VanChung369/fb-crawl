from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

import psycopg
import pytest

from fb_crawl.accounts.postgres import PostgresAccountRepository
from fb_crawl.entitlements.quota import ContactQuotaService, PostgresContactQuotaRepository
from fb_crawl.licenses.models import LicenseDuration, LicenseGrant
from fb_crawl.licenses.postgres import PostgresLicenseRepository
from fb_data_pipeline.repositories.migrations import MigrationRunner
from fb_data_pipeline.migrations import load_migrations


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


def _redeem(account_id: int, digest: str, now: datetime):
    repository = PostgresLicenseRepository(TEST_DATABASE_URL)
    repository.create_key(
        key_digest=digest, key_version=1, masked_key=f"LF-{digest}",
        grant=LicenseGrant(LicenseDuration("day", 1), 1000, 1, False, False),
        created_by_account_id=None, now=now,
    )
    return repository.redeem(account_id, digest, now)


def _paid_account_with_479_used():
    account = PostgresAccountRepository(TEST_DATABASE_URL).create_account(
        "paid-quota@example.com", "paid-quota@example.com", "hash"
    )
    subscription = _redeem(account.id, "old-key", NOW)
    contact = _seed_contact("100000000004", "+84900000004")
    _quota().reserve(account.id, *contact, None, NOW)
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute(
            "UPDATE usage_monthly SET used_contact_count = 479 WHERE account_id = %s",
            (account.id,),
        )
    return account, subscription, contact


def test_expiry_does_not_charge_paid_usage_against_default_allowance() -> None:
    account, subscription, _ = _paid_account_with_479_used()
    quota = _quota()
    assert quota.current_usage(account.id, subscription.ends_at) == 0
    user_id, phone_id = _seed_contact("100000000005", "+84900000005")
    decision = quota.reserve(account.id, user_id, phone_id, None, subscription.ends_at)
    assert decision.allowed and decision.charged
    assert (decision.used, decision.limit) == (1, 100)


def test_new_key_starts_with_all_1000_contacts_after_479_used_on_old_key() -> None:
    account, subscription, contact = _paid_account_with_479_used()
    new = _redeem(account.id, "new-key", subscription.ends_at)
    quota = _quota()
    assert quota.current_usage(account.id, new.starts_at) == 0
    # The same contact is charged once in the new subscription, then deduplicated.
    first = quota.reserve(account.id, *contact, None, new.starts_at)
    again = quota.reserve(account.id, *contact, None, new.starts_at)
    assert (first.used, first.limit, first.charged) == (1, 1000, True)
    assert (again.used, again.charged) == (1, False)


def test_free_and_paid_usage_stay_separate_when_returning_to_default_plan() -> None:
    account = PostgresAccountRepository(TEST_DATABASE_URL).create_account(
        "free-quota@example.com", "free-quota@example.com", "hash"
    )
    contact = _seed_contact("100000000006", "+84900000006")
    quota = _quota()
    assert quota.reserve(account.id, *contact, None, NOW).used == 1
    paid = _redeem(account.id, "paid-key", NOW + timedelta(hours=1))
    assert quota.current_usage(account.id, paid.starts_at) == 0
    assert quota.reserve(account.id, *contact, None, paid.starts_at).charged
    assert quota.current_usage(account.id, paid.ends_at) == 1
    again = quota.reserve(account.id, *contact, None, paid.ends_at)
    assert (again.used, again.limit, again.charged) == (1, 100, False)


def test_queued_key_has_fresh_usage_only_when_its_subscription_starts() -> None:
    account, subscription, _ = _paid_account_with_479_used()
    queued = _redeem(account.id, "queued-key", NOW + timedelta(hours=1))
    assert queued.starts_at == subscription.ends_at
    assert _quota().current_usage(account.id, NOW + timedelta(hours=2)) == 479
    assert _quota().current_usage(account.id, queued.starts_at) == 0


@pytest.mark.parametrize("deleted_paid_contacts", [0, 100])
def test_migration_splits_existing_usage_without_erasing_active_key_usage(
    deleted_paid_contacts: int,
) -> None:
    # An isolated schema lets the real migration consume pre-fix data. Rollback
    # removes the fixture; the configured test database's public schema is intact.
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE SCHEMA quota_migration_fixture")
            cursor.execute("SET LOCAL search_path TO quota_migration_fixture")
            migrations = load_migrations()
            for migration in migrations:
                if migration.version < "024_subscription_quota_scopes":
                    cursor.execute(migration.sql)
            cursor.execute("""
                INSERT INTO accounts (normalized_email, display_email, password_hash)
                VALUES ('migration@example.test', 'migration@example.test', 'hash');
                INSERT INTO license_keys (
                    key_digest, key_version, masked_key, duration_unit, duration_value,
                    monthly_contact_limit, max_devices
                ) VALUES ('old', 1, 'old', 'month', 1, 1000, 1),
                         ('new', 1, 'new', 'month', 1, 1000, 1);
                INSERT INTO account_subscriptions (
                    account_id, license_key_id, duration_unit, duration_value,
                    monthly_contact_limit, max_devices, allow_group_crawl,
                    allow_comment_crawl, starts_at, ends_at
                ) VALUES (1, 1, 'month', 1, 1000, 1, false, false,
                          '2026-08-01T00:00:00Z', '2026-08-20T00:00:00Z'),
                         (1, 2, 'month', 1, 1000, 1, false, false,
                          '2026-08-25T00:00:00Z', '2026-09-25T00:00:00Z');
                INSERT INTO facebook_users (facebook_uid)
                SELECT (100000000000 + item)::text FROM generate_series(1, 482) item;
                INSERT INTO phone_numbers (normalized_phone, display_phone)
                VALUES ('+84900000001', '+84900000001');
                INSERT INTO account_contact_reveals (
                    account_id, facebook_user_id, phone_number_id, period_start, revealed_at
                ) SELECT 1, item, 1, DATE '2026-08-01',
                         CASE WHEN item <= 400 THEN TIMESTAMPTZ '2026-08-10T00:00:00Z'
                              WHEN item <= 479 THEN TIMESTAMPTZ '2026-08-26T00:00:00Z'
                              ELSE TIMESTAMPTZ '2026-08-22T00:00:00Z' END
                  FROM generate_series(1, 482) item;
                INSERT INTO usage_monthly (account_id, period_start, used_contact_count)
                VALUES (1, DATE '2026-08-01', 482);
            """)
            migration = next(m for m in migrations if m.version == "024_subscription_quota_scopes")
            cursor.execute(
                "DELETE FROM account_contact_reveals WHERE facebook_user_id <= %s",
                (deleted_paid_contacts,),
            )
            cursor.execute(migration.sql)
            cursor.execute("SELECT quota_scope, used_contact_count FROM usage_monthly ORDER BY quota_scope")
            assert cursor.fetchall() == [(0, 3), (1, 400 - deleted_paid_contacts), (2, 79)]
            cursor.execute("SELECT quota_scope, count(*) FROM account_contact_reveals GROUP BY quota_scope ORDER BY quota_scope")
            assert cursor.fetchall() == [(0, 3), (1, 400 - deleted_paid_contacts), (2, 79)]
            cursor.execute("SELECT used_contact_count FROM legacy_unattributed_contact_usage")
            assert cursor.fetchall() == ([(100,)] if deleted_paid_contacts else [])
        connection.rollback()
