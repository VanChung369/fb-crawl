from __future__ import annotations

from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import os
from urllib.parse import unquote, urlparse

import psycopg
import pytest

from fb_crawl.accounts.postgres import PostgresAccountRepository
from fb_crawl.licenses.models import LicenseDuration, LicenseGrant
from fb_crawl.licenses.postgres import PostgresLicenseRepository
from fb_crawl.licenses.repository import LicenseAlreadyRedeemed
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
def clean_license_data() -> None:
    MigrationRunner(TEST_DATABASE_URL).apply()
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE admin_audit_events, account_contact_reveals,
                    usage_monthly, account_subscriptions, license_keys, accounts
                RESTART IDENTITY CASCADE
                """
            )


def test_redeem_is_permanent_and_second_key_queues_after_first() -> None:
    accounts = PostgresAccountRepository(TEST_DATABASE_URL)
    first_account = accounts.create_account("one@example.com", "one@example.com", "hash")
    second_account = accounts.create_account("two@example.com", "two@example.com", "hash")
    licenses = PostgresLicenseRepository(TEST_DATABASE_URL)
    grant = LicenseGrant(LicenseDuration("day", 7), 500, 1, False, False)
    first_key = licenses.create_key(
        key_digest="first-digest",
        key_version=1,
        masked_key="LF-****-****-0001",
        grant=grant,
        created_by_account_id=None,
        now=NOW,
    )
    second_key = licenses.create_key(
        key_digest="second-digest",
        key_version=1,
        masked_key="LF-****-****-0002",
        grant=grant,
        created_by_account_id=None,
        now=NOW,
    )

    first = licenses.redeem(first_account.id, first_key.key_digest, NOW)
    second = licenses.redeem(
        first_account.id, second_key.key_digest, NOW + timedelta(days=1)
    )

    assert second.starts_at == first.ends_at
    with pytest.raises(LicenseAlreadyRedeemed):
        licenses.redeem(second_account.id, first_key.key_digest, NOW)


def test_two_connection_redeem_race_has_exactly_one_winner() -> None:
    accounts = PostgresAccountRepository(TEST_DATABASE_URL)
    account_ids = (
        accounts.create_account("race1@example.com", "race1@example.com", "hash").id,
        accounts.create_account("race2@example.com", "race2@example.com", "hash").id,
    )
    licenses = PostgresLicenseRepository(TEST_DATABASE_URL)
    key = licenses.create_key(
        key_digest="race-digest",
        key_version=1,
        masked_key="LF-****-****-RACE",
        grant=LicenseGrant(LicenseDuration("day", 1), 100, 1, False, False),
        created_by_account_id=None,
        now=NOW,
    )

    def redeem(account_id: int) -> str:
        try:
            licenses.redeem(account_id, key.key_digest, NOW)
        except LicenseAlreadyRedeemed:
            return "already_redeemed"
        return "redeemed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(redeem, account_ids))

    assert sorted(results) == ["already_redeemed", "redeemed"]


def test_encrypted_license_survives_repository_reload_and_can_be_redeemed() -> None:
    from fb_crawl.licenses.config import LicenseKeyRing
    from fb_crawl.licenses.keys import LicenseKeyService
    from fb_crawl.licenses.service import LicenseService

    accounts = PostgresAccountRepository(TEST_DATABASE_URL)
    account = accounts.create_account("reveal@example.com", "reveal@example.com", "hash")
    keys = LicenseKeyService(LicenseKeyRing(1, {1: b"t" * 32}))
    service = LicenseService(PostgresLicenseRepository(TEST_DATABASE_URL), keys)
    key, plaintext = service.create_key(
        LicenseGrant(LicenseDuration("day", 7), 100, 1, False, False), account.id, NOW,
    )
    reloaded = LicenseService(PostgresLicenseRepository(TEST_DATABASE_URL), keys)
    stored = reloaded.repository.get_key(key.id)
    assert stored.encrypted_key and stored.encrypted_key != plaintext
    assert reloaded.reveal_key(key.id, account.id, NOW) == plaintext
    assert reloaded.redeem(account.id, plaintext, NOW).license_key_id == key.id
