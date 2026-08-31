from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from urllib.parse import unquote, urlparse

import psycopg
import pytest

from fb_crawl.contacts.postgres import PostgresContactRepository
from fb_crawl.contacts.models import (
    ContactIdentity,
    LookupOutcome,
    LookupSource,
)
from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    ProviderResult,
    ProviderStatus,
    UserBundle,
)
from fb_data_pipeline.repositories.migrations import MigrationRunner
from fb_data_pipeline.repositories.postgres import PostgresRepository
from fb_data_pipeline.services.pipeline import EnrichedUser


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
NOW = datetime(2026, 8, 30, 10, tzinfo=UTC)


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


@pytest.fixture(autouse=True)
def clean_contact_data() -> None:
    MigrationRunner(TEST_DATABASE_URL).apply()
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE TABLE accounts, facebook_users RESTART IDENTITY CASCADE"
            )


def test_live_lease_has_one_winner_and_expired_lease_can_be_reclaimed() -> None:
    user_id = PostgresRepository(TEST_DATABASE_URL).upsert_identity(
        FacebookIdentity(uid="100123", username="sample.user")
    )
    claim_now = datetime.now(UTC)
    barrier = Barrier(2)

    def claim(owner: str):
        barrier.wait()
        return PostgresContactRepository(TEST_DATABASE_URL).claim_lease(
            user_id,
            "fbnumber",
            "phone",
            owner,
            claim_now,
            claim_now + timedelta(seconds=30),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=10)
            for future in (
                executor.submit(claim, "owner-a"),
                executor.submit(claim, "owner-b"),
            )
        ]

    assert sorted(item.acquired for item in results) == [False, True]

    reclaimed = PostgresContactRepository(TEST_DATABASE_URL).claim_lease(
        user_id,
        "fbnumber",
        "phone",
        "owner-next",
        claim_now + timedelta(seconds=31),
        claim_now + timedelta(seconds=61),
    )
    assert reclaimed.acquired is True


def test_lookup_event_reads_and_completion_are_account_scoped() -> None:
    identity = FacebookIdentity(uid="100123", username="sample.user")
    user_id = PostgresRepository(TEST_DATABASE_URL).upsert_identity(identity)
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            account_ids = []
            for index in (1, 2):
                cursor.execute(
                    """
                    INSERT INTO accounts (
                        normalized_email, display_email, password_hash,
                        status, email_verified_at
                    ) VALUES (%s, %s, %s, 'active', %s)
                    RETURNING id
                    """,
                    (
                        f"owner-{index}@example.test",
                        f"owner-{index}@example.test",
                        "test-password-hash",
                        NOW,
                    ),
                )
                account_ids.append(int(cursor.fetchone()[0]))

    repository = PostgresContactRepository(TEST_DATABASE_URL)
    event = repository.create_lookup_event(
        account_ids[0],
        None,
        ContactIdentity(user_id, identity),
        identity,
        NOW,
    )

    assert repository.get_lookup_event(account_ids[0], event.id) == event
    assert repository.get_lookup_event(account_ids[1], event.id) is None
    assert repository.complete_lookup_event(
        account_ids[1],
        event.id,
        LookupOutcome.FOUND,
        LookupSource.CACHE,
        provider_called=False,
        quota_charged=False,
        safe_error_code="",
        now=NOW + timedelta(seconds=1),
    ) is None
    completed = repository.complete_lookup_event(
        account_ids[0],
        event.id,
        LookupOutcome.FOUND,
        LookupSource.CACHE,
        provider_called=False,
        quota_charged=False,
        safe_error_code="",
        now=NOW + timedelta(seconds=1),
    )
    assert completed is not None
    assert completed.outcome is LookupOutcome.FOUND
    assert repository.complete_lookup_event(
        account_ids[0],
        event.id,
        LookupOutcome.FAILED,
        LookupSource.NONE,
        provider_called=False,
        quota_charged=False,
        safe_error_code="late_completion",
        now=NOW + timedelta(seconds=2),
    ) is None


def test_reclaimed_lease_fences_the_expired_owner_state_update() -> None:
    user_id = PostgresRepository(TEST_DATABASE_URL).upsert_identity(
        FacebookIdentity(uid="100123")
    )
    repository = PostgresContactRepository(TEST_DATABASE_URL)
    claim_now = datetime.now(UTC)
    assert repository.claim_lease(
        user_id,
        "fbnumber",
        "phone",
        "owner-old",
        claim_now,
        claim_now + timedelta(seconds=1),
    ).acquired
    next_now = claim_now + timedelta(seconds=2)
    assert repository.claim_lease(
        user_id,
        "fbnumber",
        "phone",
        "owner-new",
        next_now,
        next_now + timedelta(seconds=30),
    ).acquired

    def enriched(phone: str, checked_at: datetime) -> EnrichedUser:
        evidence = PhoneEvidence(
            phone_number=phone,
            normalized_phone=phone,
            source="external:fbnumber",
            captured_at=checked_at,
            provider="fbnumber",
        )
        return EnrichedUser(
            bundle=UserBundle(
                identity=FacebookIdentity(uid="100123"),
                evidence=(evidence,),
            ),
            provider_result=ProviderResult(
                provider="fbnumber",
                status=ProviderStatus.FOUND,
                evidence=(evidence,),
                checked_at=checked_at,
            ),
        )

    current = repository.finalize_enrichment(
        user_id,
        "fbnumber",
        "phone",
        "owner-new",
        enriched("+84981111111", next_now),
        next_now + timedelta(days=30),
    )
    stale = repository.finalize_enrichment(
        user_id,
        "fbnumber",
        "phone",
        "owner-old",
        enriched("+84982222222", claim_now),
        claim_now + timedelta(days=7),
    )

    assert current is not None
    assert current.latest_status is ProviderStatus.FOUND
    assert stale is None
    cached = repository.get_cached_contact(user_id)
    assert cached is not None
    assert cached.state == current
    assert cached.phone == "+84981111111"
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM phone_numbers WHERE normalized_phone = %s",
                ("+84982222222",),
            )
            assert cursor.fetchone() == (0,)
