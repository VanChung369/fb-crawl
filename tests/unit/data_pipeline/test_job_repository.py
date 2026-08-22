from __future__ import annotations

import base64
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import inspect
import json
from uuid import uuid4

import psycopg
import pytest

from fb_crawl.core.jobs import (
    CrawlJob,
    JobCreateCommand,
    JobStatus,
    SafeJobOptions,
    TargetStatus,
    canonical_job_target,
)
from fb_crawl.core.models import AuthenticatedAction, ScrapeMode
from fb_crawl.core.exceptions import ValidationError
from fb_data_pipeline.repositories.errors import DatabaseError
from fb_data_pipeline.repositories.jobs import JobRepository


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


def job_row(job_id: object | None = None) -> tuple[object, ...]:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    return (
        job_id or uuid4(), "authenticated", "members", "default", "queued",
        {"steps": 10, "max_duration_seconds": 300.0,
         "navigation_delay_seconds": 8.0, "max_retries": 1, "depth": 1,
         "max_users": 1000, "enrich_profiles": False, "profile_fields": [],
         "profile_limit": 20, "phone_post_steps": 0,
         "phone_post_duration_seconds": None},
        "fingerprint", None, None, 0, 0, None, None, None, None, None, None, now, now,
        1, 0, 0, 0, 0, 0, None, "", "",
    )


def test_get_job_maps_a_driver_row_to_the_typed_contract_and_sets_timeout() -> None:
    """Break caught: repository rows leak driver values or omit timeout protection."""
    job_id = uuid4()
    cursor = RecordingCursor([job_row(job_id)])
    repository = JobRepository(
        "postgresql://hidden", statement_timeout_seconds=7.5,
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    result = repository.get_job(job_id)

    assert result == CrawlJob(
        id=job_id, mode=ScrapeMode.AUTHENTICATED,
        action=AuthenticatedAction.MEMBERS, status=JobStatus.QUEUED,
        account_key="default", request_options=SafeJobOptions(),
        request_fingerprint="fingerprint", created_at=datetime(2026, 8, 21, tzinfo=UTC),
        updated_at=datetime(2026, 8, 21, tzinfo=UTC), requested_targets=1,
    )
    assert "set_config('statement_timeout'" in cursor.commands[0][0]
    assert cursor.commands[0][1] == ("7500ms",)


@pytest.mark.parametrize("error", [psycopg.OperationalError("secret dsn"), OSError("secret dsn")])
def test_database_connection_errors_are_mapped_to_a_safe_database_error(error: BaseException) -> None:
    """Break caught: driver errors disclose the connection URL or query values."""
    def unavailable(_database_url: str):
        raise error

    repository = JobRepository("postgresql://user:password@host/private", connect_factory=unavailable)

    with pytest.raises(DatabaseError) as captured:
        repository.get_job(uuid4())

    assert captured.value.safe_message == "Database operation failed."
    assert "password" not in captured.value.safe_message
    assert "secret" not in captured.value.safe_message


@pytest.mark.parametrize("key", ["", " ", "x\n", "x" * 129])
def test_create_job_rejects_invalid_idempotency_keys_before_connecting(key: str) -> None:
    """Break caught: malformed idempotency input reaches the database connection."""
    def unexpected_connection(_database_url: str):
        raise AssertionError("database connection should not be opened")

    repository = JobRepository("postgresql://hidden", connect_factory=unexpected_connection)
    target = canonical_job_target(
        AuthenticatedAction.MEMBERS, "https://www.facebook.com/groups/123"
    )
    command = JobCreateCommand(
        AuthenticatedAction.MEMBERS, (target,), SafeJobOptions()
    )

    with pytest.raises(ValidationError, match="idempotency key"):
        repository.create_job(command, idempotency_key=key, request_fingerprint="f")


def test_create_job_revalidates_forged_target_before_connecting() -> None:
    """Break caught: a caller forges canonical target metadata after request parsing."""
    def unexpected_connection(_database_url: str):
        raise AssertionError("database connection should not be opened")

    canonical = canonical_job_target(
        AuthenticatedAction.MEMBERS, "https://www.facebook.com/groups/123"
    )
    forged = replace(canonical, target_key="members:https://www.facebook.com/groups/other/members")
    command = JobCreateCommand(AuthenticatedAction.MEMBERS, (forged,), SafeJobOptions())

    with pytest.raises(ValidationError, match="Invalid authenticated job target"):
        JobRepository("postgresql://hidden", connect_factory=unexpected_connection).create_job(
            command, idempotency_key="forged-target", request_fingerprint="fingerprint"
        )


def test_execute_time_database_errors_hide_connection_and_sql_parameter_details() -> None:
    """Break caught: a SQL execution error leaks a supplied parameter in its safe message."""
    class FailingCursor(RecordingCursor):
        def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
            super().execute(sql, params)
            if "INSERT INTO crawl_jobs" in sql:
                raise psycopg.OperationalError("failure for sql-parameter-secret")

    target = canonical_job_target(
        AuthenticatedAction.MEMBERS, "https://www.facebook.com/groups/123"
    )
    repository = JobRepository(
        "postgresql://user:password@host/private",
        connect_factory=connection_factory(RecordingConnection(FailingCursor())),
    )

    with pytest.raises(DatabaseError) as captured:
        repository.create_job(
            JobCreateCommand(AuthenticatedAction.MEMBERS, (target,), SafeJobOptions()),
            idempotency_key="safe-error", request_fingerprint="sql-parameter-secret",
        )

    assert captured.value.safe_message == "Database operation failed."
    assert "password" not in captured.value.safe_message
    assert "sql-parameter-secret" not in captured.value.safe_message


def test_list_jobs_rejects_a_naive_keyset_cursor_before_connecting() -> None:
    """Break caught: a timezone-less cursor changes database ordering by server locale."""
    def unexpected_connection(_database_url: str):
        raise AssertionError("database connection should not be opened")

    raw_cursor = base64.urlsafe_b64encode(json.dumps({
        "sort_at": "2026-08-21T00:00:00", "row_id": str(uuid4()),
    }).encode()).decode().rstrip("=")

    with pytest.raises(ValidationError, match="Invalid job pagination cursor"):
        JobRepository("postgresql://hidden", connect_factory=unexpected_connection).list_jobs(cursor=raw_cursor)


@pytest.mark.parametrize("counters", [{"steps_completed": True}, {"unknown": 1}, {"steps_completed": -1}])
def test_append_event_rejects_unsafe_counter_values_before_connecting(counters: object) -> None:
    """Break caught: an event JSON payload accepts nonnumeric or unapproved counters."""
    def unexpected_connection(_database_url: str):
        raise AssertionError("database connection should not be opened")

    with pytest.raises(ValidationError, match="Invalid crawl event counters"):
        JobRepository("postgresql://hidden", connect_factory=unexpected_connection).append_event(
            uuid4(), "target_progress", "info", counters=counters  # type: ignore[arg-type]
        )


def test_heartbeat_renews_an_owned_cancelling_job_without_treating_cancel_as_lease_loss() -> None:
    """Break caught: a cancellation makes the heartbeat thread report a lost lease."""
    cursor = RecordingCursor()
    repository = JobRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    assert repository.heartbeat(uuid4(), "worker", lease_duration=timedelta(seconds=60)) is False

    heartbeat_sql = cursor.commands[-1][0]
    assert "status IN ('running', 'cancelling')" in heartbeat_sql


def test_target_progress_recomputes_job_aggregate_counters_in_the_same_transaction() -> None:
    """Break caught: target progress is durable but the job summary remains stale."""
    job_id = uuid4()
    target_id = uuid4()
    now = datetime(2026, 8, 21, tzinfo=UTC)

    class SequencedCursor(RecordingCursor):
        def __init__(self) -> None:
            super().__init__()
            self.responses = [
                (job_id,),
                (4, 5, 3),
                (1, job_id, "target_progress", "info", now, target_id, "", {}),
            ]

        def fetchone(self):
            return self.responses.pop(0) if self.responses else None

    cursor = SequencedCursor()
    repository = JobRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    assert repository.update_target_progress(
        job_id,
        target_id,
        "worker",
        steps_completed=4,
        items_discovered=5,
        users_persisted=3,
        provider_retries_required=2,
    ) is True

    aggregate_sql = next(sql for sql, _ in cursor.commands if "UPDATE crawl_jobs" in sql)
    assert "SUM(items_discovered)" in aggregate_sql
    assert "SUM(users_persisted)" in aggregate_sql
    assert "SUM(provider_retries_required)" in aggregate_sql


def test_lease_sql_uses_wall_clock_not_transaction_stable_time() -> None:
    """Break caught: a lease remains valid after a transaction waits past expiry."""
    cursor = RecordingCursor()
    repository = JobRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    repository.heartbeat(uuid4(), "worker", lease_duration=timedelta(seconds=60))
    JobRepository._lock_owned_job(cursor, uuid4(), "worker", statuses=("running",))

    lease_sql = "\n".join(sql for sql, _ in cursor.commands)
    assert "lease_expires_at = clock_timestamp() + %s::interval" in lease_sql
    assert "lease_expires_at > clock_timestamp()" in lease_sql
    source = inspect.getsource(JobRepository)
    assert "lease_expires_at = now() +" not in source
    assert "lease_expires_at > now()" not in source
    assert "lease_expires_at <= now()" not in source


@pytest.mark.parametrize("operation", ["start", "progress", "finish"])
def test_ordinary_target_writes_reject_a_stored_cancelling_job(operation: str) -> None:
    """Break caught: an ordinary target write remains legal after request_cancel."""
    job_id = uuid4()
    target_id = uuid4()
    cursor = RecordingCursor()
    repository = JobRepository(
        "postgresql://hidden",
        connect_factory=connection_factory(RecordingConnection(cursor)),
    )

    if operation == "start":
        result = repository.start_target(job_id, target_id, "worker")
    elif operation == "progress":
        result = repository.update_target_progress(job_id, target_id, "worker")
    else:
        result = repository.finish_target(
            job_id,
            target_id,
            "worker",
            status=TargetStatus.SUCCEEDED,
        )

    assert result is False
    ownership_query = next(
        params for sql, params in cursor.commands
        if "SELECT id FROM crawl_jobs" in sql
    )
    assert ownership_query == (job_id, "worker", ["running"])
