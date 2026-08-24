from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from fb_crawl.core.exceptions import ValidationError
from fb_data_pipeline.repositories.errors import DatabaseError
from fb_data_pipeline.repositories.users import (
    EnrichmentAttemptView,
    PhoneEvidenceView,
    UserQuery,
    UserQueryRepository,
    UserSummary,
)


class RecordingCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.rows = rows or []
        self.commands: list[tuple[str, tuple[object, ...] | None]] = []

    def __enter__(self) -> RecordingCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.commands.append((sql, params))

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor) -> None:
        self.recording_cursor = cursor

    def __enter__(self) -> RecordingConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> RecordingCursor:
        return self.recording_cursor


def connection_factory(connection: RecordingConnection):
    def connect(_database_url: str) -> RecordingConnection:
        return connection

    return connect


def user_row(user_id: int = 7) -> tuple[object, ...]:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    return (
        user_id,
        "100",
        "sample.user",
        "Sample User",
        "https://www.facebook.com/sample.user",
        "+84901111111",
        "+84902222222",
        "Hanoi",
        "1990-01-02",
        "Male",
        now,
        now,
    )


def evidence_row(evidence_id: int = 4) -> tuple[object, ...]:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    return (
        evidence_id,
        "+84901111111",
        "0901 111 111",
        "fbnumber",
        "provider_lookup",
        "https://example.test/source",
        "fbnumber",
        "provider_result",
        now,
        now,
        2,
        now,
        now,
    )


def attempt_row(attempt_id: int = 8) -> tuple[object, ...]:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    return (attempt_id, "fbnumber", "found", now, "", 1, now)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            UserQuery(q="  S\u1ea2Mple  ", username=" Sample.User ", phone="0901 111 111"),
            ("s\u1ea3mple", "sample.user", "+84901111111"),
        ),
        (UserQuery(phone="0084 901 111 111"), (None, None, "+84901111111")),
    ],
)
def test_user_query_normalizes_search_and_phone_filters(query: UserQuery, expected: tuple[str | None, str | None, str | None]) -> None:
    """Break caught: equivalent input spellings produce different persisted-user filters."""
    assert (query.q, query.username, query.phone) == expected


@pytest.mark.parametrize(
    "kwargs",
    [
        {"q": "   "},
        {"username": "   "},
        {"phone": "not a phone"},
        {"phone_origin": "provider"},
        {"has_phone": "true"},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
    ],
)
def test_user_query_rejects_invalid_or_ambiguous_filters(kwargs: dict[str, object]) -> None:
    """Break caught: malformed filters reach SQL or silently widen a PII search."""
    with pytest.raises(ValidationError):
        UserQuery(**kwargs)


def test_list_users_maps_slot_view_rows_and_uses_parameterized_bounded_keyset_sql() -> None:
    """Break caught: list rows lose documented profile fields or untrusted filters reach SQL text."""
    cursor = RecordingCursor([user_row()])
    repository = UserQueryRepository(
        "postgresql://hidden",
        statement_timeout_seconds=7.5,
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    result = repository.list_users(
        UserQuery(
            q="sample",
            uid="100",
            username="sample.",
            phone="0901111111",
            phone_origin="fbnumber",
            has_phone=True,
            limit=3,
        )
    )

    assert result.items == (
        UserSummary(
            id=7,
            facebook_uid="100",
            username="sample.user",
            name="Sample User",
            profile_url="https://www.facebook.com/sample.user",
            phone_1="+84901111111",
            phone_2="+84902222222",
            address="Hanoi",
            birth_date="1990-01-02",
            gender="Male",
            created_at=datetime(2026, 8, 21, tzinfo=UTC),
            updated_at=datetime(2026, 8, 21, tzinfo=UTC),
        ),
    )
    assert result.next_cursor is None
    sql, params = cursor.commands[-1]
    assert "facebook_user_phone_slots AS slots" in sql
    assert "EXISTS" in sql
    assert "OFFSET" not in sql
    assert "LIMIT %s" in sql
    assert "sample" not in sql
    assert params is not None
    assert params[-1] == 4
    assert "set_config('statement_timeout'" in cursor.commands[0][0]
    assert cursor.commands[0][1] == ("7500ms",)


def test_detail_pages_map_only_documented_stored_fields() -> None:
    """Break caught: evidence or attempts leak correlation data or provider response payloads."""
    evidence_cursor = RecordingCursor([evidence_row()])
    repository = UserQueryRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(evidence_cursor)),
    )
    evidence = repository.list_phone_evidence(7, limit=5)
    assert evidence.items == (
        PhoneEvidenceView(
            id=4,
            normalized_phone="+84901111111",
            display_phone="0901 111 111",
            origin="fbnumber",
            source="provider_lookup",
            source_url="https://example.test/source",
            provider="fbnumber",
            confidence="provider_result",
            first_captured_at=datetime(2026, 8, 21, tzinfo=UTC),
            last_captured_at=datetime(2026, 8, 21, tzinfo=UTC),
            evidence_count=2,
            created_at=datetime(2026, 8, 21, tzinfo=UTC),
            updated_at=datetime(2026, 8, 21, tzinfo=UTC),
        ),
    )
    evidence_sql, evidence_params = evidence_cursor.commands[-1]
    assert "correlation_id" not in evidence_sql
    assert "ORDER BY evidence.last_captured_at DESC, evidence.id DESC" in evidence_sql
    assert "LIMIT %s" in evidence_sql
    assert "OFFSET" not in evidence_sql
    assert evidence_params is not None and evidence_params[-1] == 6

    attempt_cursor = RecordingCursor([attempt_row()])
    repository = UserQueryRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(attempt_cursor)),
    )
    attempts = repository.list_enrichment_attempts(7, limit=5)
    assert attempts.items == (
        EnrichmentAttemptView(
            id=8,
            provider="fbnumber",
            status="found",
            checked_at=datetime(2026, 8, 21, tzinfo=UTC),
            error_code="",
            values_found=1,
            created_at=datetime(2026, 8, 21, tzinfo=UTC),
        ),
    )
    attempt_sql, attempt_params = attempt_cursor.commands[-1]
    assert "correlation_id" not in attempt_sql
    assert "ORDER BY attempts.checked_at DESC, attempts.id DESC" in attempt_sql
    assert "LIMIT %s" in attempt_sql
    assert "OFFSET" not in attempt_sql
    assert attempt_params is not None and attempt_params[-1] == 6


def test_update_user_normalizes_manual_phone_numbers_before_writing_slots() -> None:
    """Break caught: dashboard edit writes raw 091... into E.164-only phone key."""

    cursor = RecordingCursor([user_row()])
    repository = UserQueryRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    updated = repository.update_user(7, phone_1="0912 345 678")

    phone_insert = next(
        params
        for sql, params in cursor.commands
        if "INSERT INTO phone_numbers" in sql
    )
    evidence_insert = next(
        params
        for sql, params in cursor.commands
        if "INSERT INTO user_phone_evidence" in sql
    )
    assert updated is not None
    assert phone_insert == ("+84912345678", "0912 345 678")
    assert evidence_insert is not None
    assert evidence_insert[2] == "fbnumber"


@pytest.mark.parametrize("method", ["list_users", "list_phone_evidence", "list_enrichment_attempts"])
def test_repository_rejects_invalid_keyset_cursors_before_connecting(method: str) -> None:
    """Break caught: malformed cursors reach PostgreSQL and change pagination semantics."""
    def unexpected_connection(_database_url: str):
        raise AssertionError("database connection should not be opened")

    repository = UserQueryRepository("postgresql://hidden", connect_factory=unexpected_connection)
    with pytest.raises(ValidationError, match="Invalid pagination cursor"):
        if method == "list_users":
            repository.list_users(UserQuery(cursor="not-a-cursor"))
        else:
            getattr(repository, method)(7, cursor="not-a-cursor")


@pytest.mark.parametrize("method", ["list_phone_evidence", "list_enrichment_attempts"])
@pytest.mark.parametrize("cursor", ["", False, 0, []])
def test_detail_pages_reject_falsey_non_null_cursors_before_connecting(method: str, cursor: object) -> None:
    """Break caught: falsey malformed detail cursors bypass validation and restart a page."""
    def unexpected_connection(_database_url: str):
        raise AssertionError("database connection should not be opened")

    repository = UserQueryRepository("postgresql://hidden", connect_factory=unexpected_connection)
    with pytest.raises(ValidationError, match="Invalid pagination cursor"):
        getattr(repository, method)(7, cursor=cursor)


@pytest.mark.parametrize("error", [psycopg.OperationalError("secret dsn"), OSError("secret dsn")])
def test_database_errors_are_mapped_to_a_safe_message(error: BaseException) -> None:
    """Break caught: read failures expose connection credentials or user query values."""
    def unavailable(_database_url: str):
        raise error

    with pytest.raises(DatabaseError) as captured:
        UserQueryRepository("postgresql://user:password@host/private", connect_factory=unavailable).get_user(7)

    assert captured.value.safe_message == "Database operation failed."
    assert "password" not in captured.value.safe_message
    assert "secret" not in captured.value.safe_message
