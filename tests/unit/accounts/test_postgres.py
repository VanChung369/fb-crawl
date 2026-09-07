from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import psycopg

from fb_crawl.accounts.models import AccountRole, AccountStatus, DeviceStatus
from fb_crawl.accounts.repository import (
    AdminAccountProtected,
    AdminAlreadyExists,
    SessionReuseDetected,
)
from fb_crawl.accounts.postgres import PostgresAccountRepository
from fb_data_pipeline.repositories.errors import DatabaseError


NOW = datetime(2026, 8, 30, 8, tzinfo=UTC)
SESSION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
NEXT_SESSION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
INSTALLATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def account_row() -> tuple[object, ...]:
    return (
        7,
        "person@example.com",
        "Person@example.com",
        "$argon2id$private",
        "user",
        "pending",
        None,
        NOW,
        NOW,
        None,
    )


def replace_account_status(row: tuple[object, ...], status: str) -> tuple[object, ...]:
    values = list(row)
    values[5] = status
    return tuple(values)


def device_row() -> tuple[object, ...]:
    return (9, 7, INSTALLATION_ID, "Chrome", "active", NOW, NOW)


def session_row(
    session_id: UUID = SESSION_ID,
    *,
    rotated_from_id: UUID | None = None,
    revoked_at: datetime | None = None,
    authenticated_at: datetime = NOW,
) -> tuple[object, ...]:
    return (
        session_id,
        7,
        9,
        NOW + timedelta(days=30),
        rotated_from_id,
        revoked_at,
        NOW,
        NOW,
        authenticated_at,
    )


class ScriptedCursor:
    def __init__(self, rows: list[tuple[object, ...] | None]) -> None:
        self.rows = list(rows)
        self.commands: list[tuple[str, tuple[object, ...] | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.commands.append((sql, params))

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        row = self.rows.pop(0) if self.rows else ()
        return list(row) if row else []  # type: ignore[arg-type]


class AuditFailingCursor(ScriptedCursor):
    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        super().execute(sql, params)
        if "INSERT INTO admin_audit_events" in sql:
            raise psycopg.OperationalError("audit unavailable")


class RecordingConnection:
    def __init__(self, cursor: ScriptedCursor) -> None:
        self.recording_cursor = cursor
        self.exit_errors: list[type[BaseException] | None] = []

    def __enter__(self):
        return self

    def __exit__(self, error_type, *_args: object) -> None:
        self.exit_errors.append(error_type)

    def cursor(self) -> ScriptedCursor:
        return self.recording_cursor


class ConnectionSequence:
    def __init__(self, *cursors: ScriptedCursor) -> None:
        self.connections = [RecordingConnection(cursor) for cursor in cursors]
        self._remaining = list(self.connections)

    def __call__(self, _database_url: str) -> RecordingConnection:
        return self._remaining.pop(0)


def test_create_account_uses_parameterized_sql_and_maps_closed_model() -> None:
    cursor = ScriptedCursor([account_row()])
    repository = PostgresAccountRepository(
        "postgresql://hidden",
        connect_factory=ConnectionSequence(cursor),
    )

    account = repository.create_account(
        "person@example.com",
        "Person@example.com",
        "$argon2id$private",
    )

    assert account.id == 7
    assert account.role is AccountRole.USER
    assert account.status is AccountStatus.PENDING
    assert "$argon2id$private" not in repr(account)
    sql, params = cursor.commands[-1]
    assert "INSERT INTO accounts" in sql
    assert "person@example.com" not in sql
    assert params == (
        "person@example.com",
        "Person@example.com",
        "$argon2id$private",
    )


def test_list_accounts_first_page_avoids_untyped_null_cursor_parameter() -> None:
    cursor = ScriptedCursor([()])
    repository = PostgresAccountRepository(
        "postgresql://hidden",
        connect_factory=ConnectionSequence(cursor),
    )

    accounts = repository.list_accounts(limit=25)

    assert accounts == ()
    sql, params = cursor.commands[-1]
    assert "FROM accounts" in sql
    assert "WHERE" not in sql
    assert params == (25,)


def test_rotate_refresh_token_is_single_use_and_reuse_revokes_family() -> None:
    original_authentication = NOW - timedelta(hours=1)
    first_cursor = ScriptedCursor(
        [
            (*session_row(authenticated_at=original_authentication), None),
            session_row(
                NEXT_SESSION_ID,
                rotated_from_id=SESSION_ID,
                authenticated_at=original_authentication,
            ),
        ]
    )
    reused_cursor = ScriptedCursor(
        [(*session_row(revoked_at=NOW), NEXT_SESSION_ID)]
    )
    connections = ConnectionSequence(first_cursor, reused_cursor)
    repository = PostgresAccountRepository(
        "postgresql://hidden",
        connect_factory=connections,
        uuid_factory=lambda: NEXT_SESSION_ID,
    )

    rotated = repository.rotate_session(
        "old-digest",
        "new-digest",
        NOW,
        NOW + timedelta(days=30),
    )
    with pytest.raises(SessionReuseDetected):
        repository.rotate_session(
            "old-digest",
            "another-digest",
            NOW + timedelta(seconds=1),
            NOW + timedelta(days=30),
        )

    assert rotated.id == NEXT_SESSION_ID
    assert rotated.rotated_from_id == SESSION_ID
    assert rotated.authenticated_at == original_authentication
    assert "FOR UPDATE" in first_cursor.commands[1][0]
    assert any("WITH RECURSIVE" in sql for sql, _ in reused_cursor.commands)
    assert connections.connections[1].exit_errors == [None]


def test_revoke_device_locks_device_and_revokes_its_sessions() -> None:
    cursor = ScriptedCursor([device_row()])
    repository = PostgresAccountRepository(
        "postgresql://hidden",
        connect_factory=ConnectionSequence(cursor),
    )

    device = repository.revoke_device(7, 9, NOW)

    assert device.status is DeviceStatus.REVOKED
    assert "FOR UPDATE" in cursor.commands[1][0]
    assert any("UPDATE auth_sessions" in sql for sql, _ in cursor.commands)
    assert all(params is None or "$argon" not in repr(params) for _, params in cursor.commands)


def test_admin_suspend_rejects_admin_targets_before_mutation() -> None:
    admin = (*account_row()[:4], "admin", "active", *account_row()[6:])
    cursor = ScriptedCursor([admin])
    repository = PostgresAccountRepository(
        "postgresql://hidden", connect_factory=ConnectionSequence(cursor)
    )

    with pytest.raises(AdminAccountProtected):
        repository.suspend_account_as_admin(7, 9, NOW)

    assert not any("UPDATE accounts" in sql for sql, _ in cursor.commands)


def test_admin_suspend_and_audit_share_transaction_and_roll_back_on_audit_error() -> None:
    cursor = AuditFailingCursor([replace_account_status(account_row(), "active")])
    connections = ConnectionSequence(cursor)
    repository = PostgresAccountRepository(
        "postgresql://hidden", connect_factory=connections
    )

    with pytest.raises(DatabaseError):
        repository.suspend_account_as_admin(7, 9, NOW)

    assert any("UPDATE accounts" in sql for sql, _ in cursor.commands)
    assert any("INSERT INTO admin_audit_events" in sql for sql, _ in cursor.commands)
    assert connections.connections[0].exit_errors == [psycopg.OperationalError]


def test_rate_limit_hit_is_one_atomic_upsert() -> None:
    cursor = ScriptedCursor([(3,)])
    repository = PostgresAccountRepository(
        "postgresql://hidden",
        connect_factory=ConnectionSequence(cursor),
    )

    count = repository.record_rate_limit_hit(
        "bucket-digest",
        "login",
        NOW,
        NOW + timedelta(minutes=5),
    )

    assert count == 3
    sql, params = cursor.commands[-1]
    assert "ON CONFLICT" in sql
    assert "request_count = rate_limit_buckets.request_count + 1" in sql
    assert params == (
        "bucket-digest",
        "login",
        NOW,
        NOW + timedelta(minutes=5),
    )


def test_database_driver_failures_are_mapped_without_dsn() -> None:
    def fail(_database_url: str):
        raise OSError("postgresql://user:secret@private-host/database")

    repository = PostgresAccountRepository(
        "postgresql://user:secret@private-host/database",
        connect_factory=fail,
    )

    with pytest.raises(DatabaseError) as captured:
        repository.find_account_by_email("person@example.com")
    assert "secret" not in str(captured.value)


def test_bootstrap_admin_serializes_first_admin_creation() -> None:
    admin_row = (
        1,
        "admin@example.com",
        "Admin@example.com",
        "$argon2id$private",
        "admin",
        "active",
        NOW,
        NOW,
        NOW,
        None,
    )
    cursor = ScriptedCursor([(False,), admin_row])
    repository = PostgresAccountRepository(
        "postgresql://hidden",
        connect_factory=ConnectionSequence(cursor),
    )

    admin = repository.bootstrap_admin(
        "admin@example.com",
        "Admin@example.com",
        "$argon2id$private",
        NOW,
    )

    assert admin.role is AccountRole.ADMIN
    assert "pg_advisory_xact_lock" in cursor.commands[1][0]
    assert "WHERE role = 'admin'" in cursor.commands[2][0]


def test_bootstrap_admin_rejects_when_any_admin_exists() -> None:
    cursor = ScriptedCursor([(True,)])
    repository = PostgresAccountRepository(
        "postgresql://hidden",
        connect_factory=ConnectionSequence(cursor),
    )

    with pytest.raises(AdminAlreadyExists):
        repository.bootstrap_admin(
            "other@example.com",
            "other@example.com",
            "$argon2id$private",
            NOW,
        )
