from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fb_crawl.exports.models import ExportFormat, ExportStatus
import pytest

from fb_crawl.exports.postgres import ExportQueueFull, PostgresExportRepository


NOW = datetime(2026, 8, 31, 3, tzinfo=UTC)
JOB_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def row(*, account_id=7, status="queued", owner=None, path=None):
    return (
        JOB_ID,
        account_id,
        "csv",
        {"outcome": "found"},
        status,
        owner,
        NOW + timedelta(minutes=15) if owner else None,
        1,
        None,
        path,
        NOW + timedelta(hours=24) if path else None,
        NOW,
        NOW,
        NOW if status in {"completed", "failed", "expired"} else None,
    )


class Cursor:
    def __init__(self, rows=()) -> None:
        self.rows = list(rows)
        self.commands = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params=None):
        self.commands.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows


class Connection:
    def __init__(self, cursor):
        self.value = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return self.value


def repository(cursor):
    return PostgresExportRepository(
        "postgresql://hidden",
        connect_factory=lambda _url: Connection(cursor),
    )


def command(cursor, marker):
    return next(
        (sql, params)
        for sql, params in cursor.commands
        if marker in sql
    )


def test_create_and_account_reads_bind_tenant_ownership() -> None:
    cursor = Cursor([None, (0,), row(), row()])
    exports = repository(cursor)

    created = exports.create(
        7, ExportFormat.CSV, {"outcome": "found"}, NOW, job_id=JOB_ID
    )
    fetched = exports.get(7, JOB_ID)

    assert created.id == fetched.id == JOB_ID
    insert_sql, insert_params = command(cursor, "INSERT INTO export_jobs")
    assert insert_params[:2] == (JOB_ID, 7)
    select_sql, select_params = command(cursor, "FROM export_jobs WHERE")
    assert "account_id = %s AND id = %s" in select_sql
    assert select_params == (7, JOB_ID)


def test_create_deduplicates_active_filter_before_inserting() -> None:
    cursor = Cursor([row()])
    exports = repository(cursor)

    existing = exports.create(
        7, ExportFormat.CSV, {"outcome": "found"}, NOW, job_id=JOB_ID
    )

    assert existing.id == JOB_ID
    sql = "\n".join(command for command, _params in cursor.commands)
    assert "pg_advisory_xact_lock" in sql
    assert "filter_snapshot = %s::jsonb" in sql
    assert "INSERT INTO export_jobs" not in sql


def test_create_rejects_more_than_five_active_jobs_per_account() -> None:
    cursor = Cursor([None, (5,)])
    exports = repository(cursor)

    with pytest.raises(ExportQueueFull):
        exports.create(
            7, ExportFormat.XLSX, {"uid": "100"}, NOW, job_id=JOB_ID
        )

    assert not any("INSERT INTO export_jobs" in sql for sql, _ in cursor.commands)


def test_claim_recovers_expired_lease_with_skip_locked_and_owner_fencing() -> None:
    cursor = Cursor([row(status="running", owner="worker-1")])
    exports = repository(cursor)

    claimed = exports.claim_next("worker-1", NOW)

    assert claimed is not None and claimed.status is ExportStatus.RUNNING
    sql, params = command(cursor, "FOR UPDATE SKIP LOCKED")
    assert "leased_until <= %s" in sql
    assert "attempt_count = jobs.attempt_count + 1" in sql
    assert "owner_token = %s" in sql
    assert params[0] == NOW
    assert params[1] == "worker-1"


def test_complete_and_fail_require_current_owner_and_live_running_job() -> None:
    cursor = Cursor()
    exports = repository(cursor)

    assert exports.complete(
        JOB_ID, "worker-1", "safe/file.csv", NOW, NOW + timedelta(hours=24)
    ) is True
    assert exports.fail(JOB_ID, "worker-1", "export_generation_failed", NOW) is True

    complete_sql, complete_params = command(cursor, "status = 'completed'")
    assert "owner_token = %s" in complete_sql
    assert "leased_until > %s" in complete_sql
    assert complete_params[-3:] == (JOB_ID, "worker-1", NOW)
    fail_sql, fail_params = command(cursor, "status = 'failed'")
    assert "owner_token = %s" in fail_sql
    assert "leased_until > %s" in fail_sql
    assert fail_params[-3:] == (JOB_ID, "worker-1", NOW)


def test_expiry_returns_only_previously_completed_artifact_paths() -> None:
    cursor = Cursor([("first.csv",), (None,), ("second.xlsx",)])
    exports = repository(cursor)

    paths = exports.expire_completed(NOW)

    assert paths == ("first.csv", "second.xlsx")
    sql, params = command(cursor, "status = 'expired'")
    assert "status = 'completed'" in sql
    assert "expires_at <= %s" in sql
    assert params == (NOW, NOW)

    assert exports.clear_artifact("first.csv", NOW) is True
    clear_sql, clear_params = command(cursor, "artifact_path = NULL")
    assert "status = 'expired'" in clear_sql
    assert clear_params == (NOW, "first.csv")


def test_delete_is_tenant_scoped_and_returns_artifact_for_cleanup() -> None:
    cursor = Cursor([("safe/file.csv",)])
    exports = repository(cursor)

    deleted, path = exports.delete(7, JOB_ID)

    assert deleted is True and path == "safe/file.csv"
    sql, params = command(cursor, "DELETE FROM export_jobs")
    assert "account_id = %s AND id = %s" in sql
    assert params == (7, JOB_ID)
