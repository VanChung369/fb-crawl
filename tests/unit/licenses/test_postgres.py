from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fb_crawl.licenses.models import (
    LicenseDuration,
    LicenseGrant,
    LicenseKeyStatus,
)
from fb_crawl.licenses.postgres import PostgresLicenseRepository
from fb_crawl.licenses.repository import LicenseAlreadyRedeemed


NOW = datetime(2026, 8, 30, 8, tzinfo=UTC)
GRANT = LicenseGrant(LicenseDuration("month", 1), 1000, 2, True, False)


def key_row(
    key_id: int,
    digest: str,
    *,
    status: str = "available",
) -> tuple[object, ...]:
    return (
        key_id,
        digest,
        1,
        f"LF-****-****-{key_id:04d}",
        "month",
        1,
        1000,
        2,
        True,
        False,
        status,
        1,
        7 if status == "redeemed" else None,
        NOW if status == "redeemed" else None,
        NOW,
        None,
    )


def subscription_row(
    subscription_id: int,
    key_id: int,
    starts_at: datetime,
) -> tuple[object, ...]:
    return (
        subscription_id,
        7,
        key_id,
        "month",
        1,
        1000,
        2,
        True,
        False,
        starts_at,
        starts_at + timedelta(days=31),
        "valid",
        None,
        NOW,
    )


class ScriptedCursor:
    def __init__(
        self,
        one: list[tuple[object, ...] | None],
        all_rows: list[list[tuple[object, ...]]] | None = None,
    ) -> None:
        self.one = list(one)
        self.all_rows = list(all_rows or [])
        self.commands: list[tuple[str, tuple[object, ...] | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.commands.append((sql, params))

    def fetchone(self):
        return self.one.pop(0) if self.one else None

    def fetchall(self):
        return self.all_rows.pop(0) if self.all_rows else []


class Connection:
    def __init__(self, cursor: ScriptedCursor) -> None:
        self.cursor_value = cursor
        self.exit_errors: list[type[BaseException] | None] = []

    def __enter__(self):
        return self

    def __exit__(self, error_type, *_args: object) -> None:
        self.exit_errors.append(error_type)

    def cursor(self):
        return self.cursor_value


def connect(cursor: ScriptedCursor):
    connection = Connection(cursor)
    return lambda _url: connection


def test_create_key_persists_digest_and_mask_but_never_plaintext() -> None:
    cursor = ScriptedCursor([key_row(11, "digest-one")])
    repository = PostgresLicenseRepository(
        "postgresql://hidden", connect_factory=connect(cursor)
    )

    key = repository.create_key(
        key_digest="digest-one",
        key_version=1,
        masked_key="LF-****-****-0011",
        grant=GRANT,
        created_by_account_id=1,
        now=NOW,
    )

    assert key.id == 11
    assert key.status is LicenseKeyStatus.AVAILABLE
    sql, params = next(
        command
        for command in cursor.commands
        if "INSERT INTO license_keys" in command[0]
    )
    assert "INSERT INTO license_keys" in sql
    assert "LF-REAL-PLAINTEXT" not in repr(cursor.commands)
    assert params is not None and params[0] == "digest-one"
    assert any("INSERT INTO admin_audit_events" in sql for sql, _ in cursor.commands)


def test_list_keys_first_page_avoids_untyped_null_cursor_parameter() -> None:
    cursor = ScriptedCursor([], all_rows=[[]])
    repository = PostgresLicenseRepository(
        "postgresql://hidden", connect_factory=connect(cursor)
    )

    keys = repository.list_keys(limit=25)

    assert keys == ()
    sql, params = cursor.commands[-1]
    assert "FROM license_keys" in sql
    assert "WHERE" not in sql
    assert params == (25,)


def test_list_audit_events_first_page_avoids_untyped_null_cursor_parameter() -> None:
    cursor = ScriptedCursor([], all_rows=[[]])
    repository = PostgresLicenseRepository(
        "postgresql://hidden", connect_factory=connect(cursor)
    )

    events = repository.list_audit_events(limit=50)

    assert events == ()
    sql, params = cursor.commands[-1]
    assert "FROM admin_audit_events" in sql
    assert "WHERE" not in sql
    assert params == (50,)


def test_second_key_starts_after_existing_valid_end() -> None:
    existing_end = datetime(2026, 9, 30, 8, tzinfo=UTC)
    cursor = ScriptedCursor(
        [
            key_row(12, "digest-two"),
            (existing_end,),
            subscription_row(22, 12, existing_end),
        ]
    )
    repository = PostgresLicenseRepository(
        "postgresql://hidden", connect_factory=connect(cursor)
    )

    subscription = repository.redeem(7, "digest-two", NOW)

    assert subscription.starts_at == existing_end
    assert "pg_advisory_xact_lock" in cursor.commands[1][0]
    assert "FOR UPDATE" in cursor.commands[2][0]
    assert any("UPDATE license_keys" in sql for sql, _ in cursor.commands)
    assert any("INSERT INTO admin_audit_events" in sql for sql, _ in cursor.commands)


def test_second_account_cannot_redeem_used_key() -> None:
    cursor = ScriptedCursor([key_row(11, "used-digest", status="redeemed")])
    connection_factory = connect(cursor)
    repository = PostgresLicenseRepository(
        "postgresql://hidden", connect_factory=connection_factory
    )

    with pytest.raises(LicenseAlreadyRedeemed):
        repository.redeem(8, "used-digest", NOW)


def test_effective_entitlements_select_paid_snapshot_or_default_plan() -> None:
    paid_cursor = ScriptedCursor([subscription_row(21, 11, NOW)])
    paid = PostgresLicenseRepository(
        "postgresql://hidden", connect_factory=connect(paid_cursor)
    ).effective_entitlements(7, NOW + timedelta(days=1))

    default_cursor = ScriptedCursor([None, (100, 1, False, False)])
    default = PostgresLicenseRepository(
        "postgresql://hidden", connect_factory=connect(default_cursor)
    ).effective_entitlements(7, NOW)

    assert paid.subscription_id == 21
    assert paid.monthly_contact_limit == 1000
    assert default.subscription_id is None
    assert (
        default.monthly_contact_limit,
        default.max_devices,
        default.allow_group_crawl,
        default.allow_comment_crawl,
    ) == (100, 1, False, False)
