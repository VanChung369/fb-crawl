from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from urllib.parse import unquote, urlparse

import psycopg
import pytest

from fb_crawl.contacts.postgres import PostgresContactRepository
from fb_data_pipeline.core.models import FacebookIdentity
from fb_data_pipeline.repositories.migrations import MigrationRunner
from fb_data_pipeline.repositories.postgres import PostgresRepository


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
    barrier = Barrier(2)

    def claim(owner: str):
        barrier.wait()
        return PostgresContactRepository(TEST_DATABASE_URL).claim_lease(
            user_id,
            "fbnumber",
            "phone",
            owner,
            NOW,
            NOW + timedelta(seconds=30),
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
        NOW + timedelta(seconds=31),
        NOW + timedelta(seconds=61),
    )
    assert reclaimed.acquired is True
