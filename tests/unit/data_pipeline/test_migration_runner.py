from __future__ import annotations

from collections.abc import Callable

import psycopg
import pytest

from fb_data_pipeline.migrations import Migration
from fb_data_pipeline.repositories.errors import DatabaseError, MigrationChecksumError
from fb_data_pipeline.repositories.migrations import MigrationRunner


class RecordingCursor:
    def __init__(
        self,
        existing: tuple[tuple[str, str], ...] = (),
        *,
        fail_sql: str = "",
        one: tuple[object, ...] | None = None,
    ) -> None:
        self.existing = existing
        self.fail_sql = fail_sql
        self.one = one
        self.commands: list[tuple[str, tuple[object, ...] | None]] = []

    def __enter__(self) -> RecordingCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] | None = None,
    ) -> None:
        self.commands.append((sql, params))
        if sql == self.fail_sql:
            raise RuntimeError("migration execution failed")

    def fetchall(self) -> list[tuple[str, str]]:
        return list(self.existing)

    def fetchone(self) -> tuple[object, ...] | None:
        return self.one


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor) -> None:
        self.recording_cursor = cursor
        self.committed = False
        self.rolled_back = False

    def __enter__(self) -> RecordingConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object,
    ) -> None:
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None

    def cursor(self) -> RecordingCursor:
        return self.recording_cursor


def connection_factory(
    connection: RecordingConnection,
) -> Callable[[str], RecordingConnection]:
    def connect(_database_url: str) -> RecordingConnection:
        return connection

    return connect


def migration() -> Migration:
    return Migration(
        version="001_initial",
        sql="CREATE TABLE example (id bigint)",
        checksum="a" * 64,
    )


def test_runner_applies_unseen_migration_and_records_checksum() -> None:
    item = migration()
    cursor = RecordingCursor()
    connection = RecordingConnection(cursor)
    runner = MigrationRunner(
        "postgresql://hidden",
        connect_factory=connection_factory(connection),
        migrations=(item,),
    )

    assert runner.apply() == ("001_initial",)
    assert connection.committed is True
    assert cursor.commands[-2] == (item.sql, None)
    assert "INSERT INTO schema_migrations" in cursor.commands[-1][0]
    assert cursor.commands[-1][1] == (item.version, item.checksum)


def test_runner_skips_migration_with_matching_checksum() -> None:
    item = migration()
    cursor = RecordingCursor(((item.version, item.checksum),))
    runner = MigrationRunner(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
        migrations=(item,),
    )

    assert runner.apply() == ()
    assert all(command[0] != item.sql for command in cursor.commands)


def test_runner_rejects_changed_applied_migration_without_secrets() -> None:
    item = migration()
    cursor = RecordingCursor(((item.version, "b" * 64),))
    runner = MigrationRunner(
        "postgresql://user:secret@localhost/database",
        connect_factory=connection_factory(RecordingConnection(cursor)),
        migrations=(item,),
    )

    with pytest.raises(MigrationChecksumError) as captured:
        runner.apply()

    assert item.version in captured.value.safe_message
    assert "secret" not in captured.value.safe_message
    assert item.sql not in captured.value.safe_message


def test_runner_rolls_back_and_does_not_record_failed_migration() -> None:
    item = migration()
    cursor = RecordingCursor(fail_sql=item.sql)
    connection = RecordingConnection(cursor)
    runner = MigrationRunner(
        "postgresql://hidden",
        connect_factory=connection_factory(connection),
        migrations=(item,),
    )

    with pytest.raises(RuntimeError, match="migration execution failed"):
        runner.apply()

    assert connection.rolled_back is True
    assert not any(
        "INSERT INTO schema_migrations" in command[0]
        for command in cursor.commands
    )


@pytest.mark.parametrize(("row", "expected"), [((True,), True), ((False,), False)])
def test_runner_checks_a_parameterized_migration_version_without_schema_mutation(
    row: tuple[object, ...],
    expected: bool,
) -> None:
    """Break caught: API readiness interpolates SQL or applies migrations."""
    cursor = RecordingCursor(one=row)
    runner = MigrationRunner(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    assert runner.is_applied("003_job_orchestration") is expected

    assert len(cursor.commands) == 1
    sql, params = cursor.commands[0]
    assert "schema_migrations" in sql
    assert "CREATE" not in sql.upper()
    assert "INSERT" not in sql.upper()
    assert params == ("003_job_orchestration",)


def test_runner_readiness_maps_database_failure_without_leaking_url() -> None:
    """Break caught: readiness exposes connection details or raw driver errors."""

    def fail_connect(_database_url: str):
        raise psycopg.OperationalError("postgresql://user:secret@private/database")

    runner = MigrationRunner(
        "postgresql://user:secret@private/database",
        connect_factory=fail_connect,
    )

    with pytest.raises(DatabaseError) as captured:
        runner.is_applied("003_job_orchestration")

    assert captured.value.safe_message == "Database readiness check failed."
    assert "secret" not in captured.value.safe_message
