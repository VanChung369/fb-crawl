from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from urllib.parse import unquote, urlparse

import psycopg
import pytest

from fb_crawl.core.jobs import KeysetCursor, decode_cursor, encode_cursor
from fb_data_pipeline.repositories.migrations import MigrationRunner
from fb_data_pipeline.repositories.users import (
    EnrichmentAttemptView,
    PhoneEvidenceView,
    UserQuery,
    UserQueryRepository,
    UserSummary,
)


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
    database_name = unquote(parsed.path.removeprefix("/"))
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not hostname
        or parsed.query
        or parsed.fragment
        or not database_name.endswith("_test")
        or "/" in database_name
        or "\\" in database_name
    ):
        return None
    return database_name


TEST_DATABASE_NAME = _safe_test_database_name(TEST_DATABASE_URL)

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_NAME is None,
    reason="TEST_DATABASE_URL must target a PostgreSQL database ending in _test",
)


def _assert_dedicated_test_database() -> None:
    assert TEST_DATABASE_NAME is not None
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        assert connection.info.dbname == TEST_DATABASE_NAME
        assert connection.info.dbname.endswith("_test")


@pytest.fixture(autouse=True)
def clean_database() -> None:
    _assert_dedicated_test_database()
    MigrationRunner(TEST_DATABASE_URL).apply()
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
                    enrichment_attempts,
                    user_phone_evidence,
                    facebook_user_profiles,
                    phone_numbers,
                    facebook_users
                RESTART IDENTITY
                """
            )


def seed_user(
    *,
    uid: str,
    username: str,
    name: str,
    created_at: datetime,
    updated_at: datetime,
    address: str = "",
    birth_date: str = "",
    gender: str = "",
) -> int:
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO facebook_users (
                    facebook_uid, facebook_username, normalized_username,
                    display_name, profile_url, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    uid,
                    username,
                    username.casefold(),
                    name,
                    f"https://www.facebook.com/{username}",
                    created_at,
                    updated_at,
                ),
            )
            row = cursor.fetchone()
            assert row is not None
            user_id = int(row[0])
            if address or birth_date or gender:
                cursor.execute(
                    """
                    INSERT INTO facebook_user_profiles (
                        facebook_user_id, address, birth_date, gender
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (user_id, address or None, birth_date or None, gender or None),
                )
    return user_id


def seed_evidence(
    user_id: int,
    *,
    phone: str,
    origin: str,
    captured_at: datetime,
    source: str = "seed",
    provider: str = "",
    confidence: str = "profile_field",
) -> int:
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO phone_numbers (normalized_phone, display_phone)
                VALUES (%s, %s)
                ON CONFLICT (normalized_phone) DO UPDATE
                SET display_phone = EXCLUDED.display_phone
                RETURNING id
                """,
                (phone, phone),
            )
            phone_row = cursor.fetchone()
            assert phone_row is not None
            cursor.execute(
                """
                INSERT INTO user_phone_evidence (
                    facebook_user_id, phone_number_id, origin, source, source_url,
                    provider, confidence, first_captured_at, last_captured_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    user_id,
                    phone_row[0],
                    origin,
                    source,
                    f"https://example.test/{source}",
                    provider,
                    confidence,
                    captured_at,
                    captured_at,
                ),
            )
            row = cursor.fetchone()
            assert row is not None
            return int(row[0])


def seed_attempt(user_id: int, *, checked_at: datetime, status: str, error_code: str = "") -> int:
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO enrichment_attempts (
                    facebook_user_id, provider, status, checked_at, error_code, values_found
                ) VALUES (%s, 'fbnumber', %s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, status, checked_at, error_code, 1 if status == "found" else 0),
            )
            row = cursor.fetchone()
            assert row is not None
            return int(row[0])


def test_search_filters_return_documented_user_summaries_without_duplicates() -> None:
    """Break caught: filters lose profile data, mismatch normalized values, or duplicate evidence-backed users."""
    created_at = datetime(2026, 8, 20, tzinfo=UTC)
    updated_at = datetime(2026, 8, 21, tzinfo=UTC)
    sample_id = seed_user(
        uid="100",
        username="sample.user",
        name="Sample User",
        created_at=created_at,
        updated_at=updated_at,
        address="Hanoi",
        birth_date="1990-01-02",
        gender="Male",
    )
    other_id = seed_user(
        uid="200",
        username="other.user",
        name="Sample Other",
        created_at=created_at,
        updated_at=updated_at - timedelta(seconds=1),
    )
    no_phone_id = seed_user(
        uid="300",
        username="quiet.user",
        name="No Phone",
        created_at=created_at,
        updated_at=updated_at - timedelta(seconds=2),
    )
    seed_evidence(sample_id, phone="+84901111111", origin="fbnumber", captured_at=updated_at, provider="fbnumber")
    seed_evidence(sample_id, phone="+84902222222", origin="fb_crawl", captured_at=updated_at)
    seed_evidence(other_id, phone="+84903333333", origin="fbnumber", captured_at=updated_at, provider="fbnumber")
    repository = UserQueryRepository(TEST_DATABASE_URL)
    expected = UserSummary(
        id=sample_id,
        facebook_uid="100",
        username="sample.user",
        name="Sample User",
        profile_url="https://www.facebook.com/sample.user",
        phone_1="+84901111111",
        phone_2="+84902222222",
        address="Hanoi",
        birth_date="1990-01-02",
        gender="Male",
        created_at=created_at,
        updated_at=updated_at,
    )

    assert repository.list_users(UserQuery(uid="100")).items == (expected,)
    assert repository.list_users(UserQuery(phone="0901 111 111")).items == (expected,)
    assert repository.list_users(UserQuery(username=" SAMPLE. ")).items == (expected,)
    assert repository.list_users(UserQuery(q="sample")).items == (expected, UserSummary(
        id=other_id, facebook_uid="200", username="other.user", name="Sample Other",
        profile_url="https://www.facebook.com/other.user", phone_1="+84903333333",
        phone_2=None, address=None, birth_date=None, gender=None,
        created_at=created_at, updated_at=updated_at - timedelta(seconds=1),
    ))
    assert repository.list_users(UserQuery(phone_origin="fb_crawl")).items == (expected,)
    assert repository.list_users(UserQuery(phone="0901 111 111", phone_origin="fbnumber")).items == (expected,)
    assert repository.list_users(UserQuery(phone="0901 111 111", phone_origin="fb_crawl")).items == ()
    assert [item.id for item in repository.list_users(UserQuery(has_phone=True)).items] == [sample_id, other_id]
    assert repository.list_users(UserQuery(has_phone=False)).items == (UserSummary(
        id=no_phone_id, facebook_uid="300", username="quiet.user", name="No Phone",
        profile_url="https://www.facebook.com/quiet.user", phone_1=None, phone_2=None,
        address=None, birth_date=None, gender=None, created_at=created_at,
        updated_at=updated_at - timedelta(seconds=2),
    ),)
    assert repository.list_users(UserQuery(username="sample", phone_origin="fb_crawl", has_phone=True)).items == (expected,)
    assert repository.list_users(UserQuery(uid="missing")).items == ()


def test_display_name_search_uses_consistent_unicode_lower_normalization() -> None:
    """Break caught: Python and PostgreSQL apply different Unicode lowercasing to name queries.

    PostgreSQL 17 has no Unicode casefold function, so this intentionally does
    not make STRASSE match Straße; the bounded expression-index query uses
    Unicode lower() on both sides instead.
    """
    timestamp = datetime(2026, 8, 21, tzinfo=UTC)
    user_id = seed_user(
        uid="unicode-100",
        username="unicode.user",
        name="Đặng Account",
        created_at=timestamp,
        updated_at=timestamp,
    )

    items = UserQueryRepository(TEST_DATABASE_URL).list_users(UserQuery(q="  ĐẶNG  ")).items

    assert [item.id for item in items] == [user_id]


def test_user_and_detail_pages_are_keyset_ordered_without_response_or_correlation_leaks() -> None:
    """Break caught: equal sort keys repeat rows, detail ordering uses offsets, or provider internals escape."""
    same_time = datetime(2026, 8, 21, tzinfo=UTC)
    ids = [
        seed_user(
            uid=str(400 + index), username=f"page.{index}", name=f"Page {index}",
            created_at=same_time, updated_at=same_time,
        )
        for index in range(3)
    ]
    repository = UserQueryRepository(TEST_DATABASE_URL)
    first = repository.list_users(UserQuery(limit=2))
    second = repository.list_users(UserQuery(limit=2, cursor=first.next_cursor))
    assert [item.id for item in first.items + second.items] == list(reversed(ids))
    assert first.next_cursor is not None
    assert second.next_cursor is None
    assert repository.get_user(ids[0]).id == ids[0]  # type: ignore[union-attr]
    assert repository.get_user(999999) is None

    first_evidence = seed_evidence(ids[0], phone="+84904444444", origin="fbnumber", captured_at=same_time, source="early", provider="fbnumber")
    second_evidence = seed_evidence(ids[0], phone="+84905555555", origin="fb_crawl", captured_at=same_time, source="middle")
    third_evidence = seed_evidence(ids[0], phone="+84906666666", origin="fb_crawl", captured_at=same_time, source="late")
    evidence_page = repository.list_phone_evidence(ids[0], limit=1)
    evidence_next = repository.list_phone_evidence(ids[0], limit=1, cursor=evidence_page.next_cursor)
    evidence_final = repository.list_phone_evidence(ids[0], limit=1, cursor=evidence_next.next_cursor)
    assert [item.id for item in evidence_page.items + evidence_next.items + evidence_final.items] == [third_evidence, second_evidence, first_evidence]
    assert evidence_final.next_cursor is None
    assert evidence_page.items[0] == PhoneEvidenceView(
        id=third_evidence, normalized_phone="+84906666666", display_phone="+84906666666",
        origin="fb_crawl", source="late", source_url="https://example.test/late", provider="",
        confidence="profile_field", first_captured_at=same_time,
        last_captured_at=same_time, evidence_count=1,
        created_at=evidence_page.items[0].created_at, updated_at=evidence_page.items[0].updated_at,
    )
    assert not hasattr(evidence_page.items[0], "correlation_id")

    first_attempt = seed_attempt(ids[0], checked_at=same_time, status="not_found")
    second_attempt = seed_attempt(ids[0], checked_at=same_time, status="failed", error_code="temporary")
    third_attempt = seed_attempt(ids[0], checked_at=same_time, status="found")
    attempt_page = repository.list_enrichment_attempts(ids[0], limit=1)
    attempt_next = repository.list_enrichment_attempts(ids[0], limit=1, cursor=attempt_page.next_cursor)
    attempt_final = repository.list_enrichment_attempts(ids[0], limit=1, cursor=attempt_next.next_cursor)
    assert [item.id for item in attempt_page.items + attempt_next.items + attempt_final.items] == [third_attempt, second_attempt, first_attempt]
    assert attempt_final.next_cursor is None
    assert attempt_page.items[0].provider == "fbnumber"
    assert attempt_page.items[0].status == "found"
    assert not hasattr(attempt_page.items[0], "correlation_id")


def _index_names_for(table_name: str) -> set[str]:
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = %s",
                (table_name,),
            )
            return {str(row[0]) for row in cursor.fetchall()}


def _plan_index_names(plan: object) -> set[str]:
    if isinstance(plan, dict):
        current = {str(plan["Index Name"])} if "Index Name" in plan else set()
        for value in plan.values():
            current.update(_plan_index_names(value))
        return current
    if isinstance(plan, list):
        return {name for value in plan for name in _plan_index_names(value)}
    return set()


def _plan_conditions(plan: object) -> set[str]:
    if isinstance(plan, dict):
        current = {
            str(plan[key])
            for key in ("Index Cond", "Recheck Cond", "Filter")
            if key in plan
        }
        for value in plan.values():
            current.update(_plan_conditions(value))
        return current
    if isinstance(plan, list):
        return {condition for value in plan for condition in _plan_conditions(value)}
    return set()


def _index_name_for(table_name: str, definition_suffix: str) -> str:
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = %s",
                (table_name,),
            )
            matches = [str(row[0]) for row in cursor.fetchall() if str(row[1]).endswith(definition_suffix)]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize(
    ("query", "expected_indexes", "condition_fragments"),
    [
        (
            UserQuery(uid="900"),
            (("facebook_users", "btree (facebook_uid)"),),
            ("facebook_uid",),
        ),
        (
            UserQuery(phone="0909999999"),
            (
                ("phone_numbers", "btree (normalized_phone)"),
                ("user_phone_evidence", "btree (phone_number_id)"),
            ),
            ("normalized_phone", "phone_number_id"),
        ),
        (
            UserQuery(username="prefix"),
            (("facebook_users", "btree (normalized_username text_pattern_ops)"),),
            ("normalized_username",),
        ),
        (
            UserQuery(
                cursor=encode_cursor(
                    KeysetCursor(datetime(2026, 8, 22, tzinfo=UTC), 999999)
                )
            ),
            (("facebook_users", "btree (updated_at DESC, id DESC)"),),
            ("updated_at", "id"),
        ),
    ],
)
def test_indexed_user_query_predicates_have_an_available_nonsequential_plan(
    query: UserQuery,
    expected_indexes: tuple[tuple[str, str], ...],
    condition_fragments: tuple[str, ...],
) -> None:
    """Break caught: a migration drops an index needed by persisted-user lookup or cursor pagination."""
    timestamp = datetime(2026, 8, 21, tzinfo=UTC)
    user_id = seed_user(
        uid="900", username="prefix.user", name="Selective User",
        created_at=timestamp, updated_at=timestamp,
    )
    seed_evidence(user_id, phone="+84909999999", origin="fbnumber", captured_at=timestamp, provider="fbnumber")
    other_id = seed_user(
        uid="901", username="other.user", name="Other User",
        created_at=timestamp - timedelta(days=1), updated_at=timestamp - timedelta(days=1),
    )
    seed_evidence(other_id, phone="+84908888888", origin="fbnumber", captured_at=timestamp, provider="fbnumber")
    cursor_values = decode_cursor(query.cursor) if query.cursor else None
    where, params = UserQueryRepository._user_filters(query, cursor_values)
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
            cursor.execute(
                f"EXPLAIN (FORMAT JSON) {UserQueryRepository._list_users_sql(where)}",
                (*params, query.limit + 1),
            )
            row = cursor.fetchone()
            assert row is not None
            plan_indexes = _plan_index_names(row[0])
            plan_conditions = " ".join(_plan_conditions(row[0]))
    assert set(_index_name_for(table_name, fragment) for table_name, fragment in expected_indexes) <= plan_indexes
    assert all(fragment in plan_conditions for fragment in condition_fragments)
