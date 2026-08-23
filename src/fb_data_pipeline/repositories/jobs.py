from __future__ import annotations

import base64
import hmac
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypeAlias
from uuid import UUID, uuid4

import psycopg

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.jobs import (
    CrawlEvent,
    CrawlJob,
    CrawlTarget,
    CrawlerAccountState,
    AccountStatus,
    IdempotencyConflict,
    JobCreateCommand,
    JobStatus,
    Page,
    SafeJobOptions,
    TargetStatus,
    canonical_job_target,
)
from fb_crawl.core.models import AuthenticatedAction, ScrapeMode
from fb_data_pipeline.repositories.errors import DatabaseError


EventType: TypeAlias = Literal[
    "job_created",
    "job_claimed",
    "browser_started",
    "target_started",
    "target_progress",
    "target_completed",
    "provider_progress",
    "facebook_rate_limited",
    "account_warning",
    "cancel_requested",
    "job_cancelled",
    "job_blocked",
    "job_completed",
]
EventLevel: TypeAlias = Literal["debug", "info", "warning", "error"]

_EVENT_TYPES = frozenset(
    {
        "job_created",
        "job_claimed",
        "browser_started",
        "target_started",
        "target_progress",
        "target_completed",
        "provider_progress",
        "facebook_rate_limited",
        "account_warning",
        "cancel_requested",
        "job_cancelled",
        "job_blocked",
        "job_completed",
    }
)
_EVENT_LEVELS = frozenset({"debug", "info", "warning", "error"})
_COUNTER_KEYS = frozenset(
    {
        "requested_targets",
        "completed_targets",
        "failed_targets",
        "discovered_users",
        "persisted_users",
        "provider_retries_required",
        "steps_completed",
        "items_discovered",
        "users_persisted",
    }
)

_JOB_COLUMNS = """
    id, mode, action, account_key, status, request_options,
    request_fingerprint, idempotency_key, retry_of_job_id, priority, attempt,
    worker_id, lease_expires_at, heartbeat_at, cancel_requested_at, started_at,
    finished_at, created_at, updated_at, requested_targets, completed_targets,
    failed_targets, discovered_users, persisted_users,
    provider_retries_required, current_target_id, error_code, error_message
"""
_TARGET_COLUMNS = """
    id, job_id, target_key, target_url, target_kind, position, status,
    checkpoint_path, created_at, updated_at, attempt, started_at, finished_at,
    steps_completed, items_discovered, users_persisted,
    provider_retries_required, error_code, error_message
"""
_EVENT_COLUMNS = """
    id, job_id, event_type, level, created_at, target_id, safe_message, counters
"""
_ACCOUNT_COLUMNS = """
    account_key, status, cooldown_until, last_job_id, last_started_at,
    last_finished_at, rate_limit_count_24h, last_rate_limit_at,
    last_warning_code, last_warning_at, block_reason, acknowledged_at,
    created_at, updated_at
"""


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job: CrawlJob
    targets: tuple[CrawlTarget, ...]


class JobRepository:
    """Short, transaction-scoped PostgreSQL operations for crawl jobs."""

    def __init__(
        self,
        database_url: str,
        *,
        statement_timeout_seconds: float = 5.0,
        connect_factory=psycopg.connect,
    ) -> None:
        self.database_url = database_url
        self.statement_timeout_ms = max(1, round(statement_timeout_seconds * 1000))
        self.connect_factory = connect_factory

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{self.statement_timeout_ms}ms",),
                    )
                    yield cursor
        except DatabaseError:
            raise
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error

    def create_job(
        self,
        command: JobCreateCommand,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[CrawlJob, bool]:
        self._validate_idempotency_key(idempotency_key)
        if not isinstance(request_fingerprint, str) or not request_fingerprint:
            raise ValidationError("A request fingerprint is required.")
        targets = tuple(
            canonical_job_target(command.action, target.target_url)
            for target in command.targets
        )
        if targets != command.targets:
            raise ValidationError("Invalid authenticated job target.")

        job_id = uuid4()
        target_ids = tuple(uuid4() for _ in targets)
        try:
            with self._connect() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO crawl_jobs ({_JOB_COLUMNS})
                    SELECT
                        %s, 'authenticated', %s, %s, 'queued', %s::jsonb,
                        %s, %s, NULL, %s, 0, NULL, NULL, NULL, NULL, NULL,
                        NULL, now(), now(), %s, 0, 0, 0, 0, 0, NULL, '', ''
                    ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL
                    DO NOTHING
                    RETURNING {_JOB_COLUMNS}
                    """,
                    (
                        job_id,
                        command.action.value,
                        command.account_key,
                        json.dumps(command.options.to_canonical_dict(), sort_keys=True),
                        request_fingerprint,
                        idempotency_key,
                        command.priority,
                        len(targets),
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        f"""
                        SELECT {_JOB_COLUMNS}
                        FROM crawl_jobs
                        WHERE idempotency_key = %s
                        FOR UPDATE
                        """,
                        (idempotency_key,),
                    )
                    existing = cursor.fetchone()
                    if existing is None:
                        raise DatabaseError("Database idempotency lookup failed.")
                    job = self._job_from_row(existing)
                    if not hmac.compare_digest(job.request_fingerprint, request_fingerprint):
                        raise IdempotencyConflict(
                            "The idempotency key was used for a different request."
                        )
                    return job, False

                job = self._job_from_row(row)
                for position, (target, target_id) in enumerate(zip(targets, target_ids, strict=True)):
                    checkpoint_path = f"runtime/checkpoints/jobs/{job_id}/{target_id}.json"
                    cursor.execute(
                        """
                        INSERT INTO crawl_targets (
                            id, job_id, target_key, target_url, target_kind, position,
                            status, checkpoint_path
                        ) VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)
                        """,
                        (
                            target_id, job_id, target.target_key, target.target_url,
                            target.target_kind, position, checkpoint_path,
                        ),
                    )
                self._insert_event(cursor, job_id, "job_created", "info")
                return job, True
        except (IdempotencyConflict, ValidationError, DatabaseError):
            raise
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error

    def get_job(self, job_id: UUID) -> CrawlJob | None:
        with self._connect() as cursor:
            cursor.execute(
                f"SELECT {_JOB_COLUMNS} FROM crawl_jobs WHERE id = %s",
                (job_id,),
            )
            row = cursor.fetchone()
        return None if row is None else self._job_from_row(row)

    def list_jobs(self, *, limit: int = 50, cursor: str | None = None) -> Page[CrawlJob]:
        limit = self._bounded_limit(limit)
        cursor_values = self._decode_uuid_cursor(cursor, name="job") if cursor else None
        where = ""
        params: tuple[object, ...] = ()
        if cursor_values is not None:
            sort_at, row_id = cursor_values
            where = "WHERE (created_at < %s OR (created_at = %s AND id < %s))"
            params = (sort_at, sort_at, row_id)
        with self._connect() as db_cursor:
            db_cursor.execute(
                f"""
                SELECT {_JOB_COLUMNS}
                FROM crawl_jobs
                {where}
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (*params, limit + 1),
            )
            rows = db_cursor.fetchall()
        items = tuple(self._job_from_row(row) for row in rows[:limit])
        next_cursor = (
            self._encode_uuid_cursor(items[-1].created_at, items[-1].id)
            if len(rows) > limit
            else None
        )
        return Page(items, next_cursor)

    def list_targets(
        self,
        job_id: UUID,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> Page[CrawlTarget]:
        limit = self._bounded_limit(limit)
        cursor_values = self._decode_target_cursor(cursor) if cursor else None
        where = "WHERE job_id = %s"
        params: tuple[object, ...] = (job_id,)
        if cursor_values is not None:
            position, row_id = cursor_values
            where += " AND (position > %s OR (position = %s AND id > %s))"
            params += (position, position, row_id)
        with self._connect() as db_cursor:
            db_cursor.execute(
                f"""
                SELECT {_TARGET_COLUMNS}
                FROM crawl_targets
                {where}
                ORDER BY position ASC, id ASC
                LIMIT %s
                """,
                (*params, limit + 1),
            )
            rows = db_cursor.fetchall()
        items = tuple(self._target_from_row(row) for row in rows[:limit])
        next_cursor = (
            self._encode_target_cursor(items[-1].position, items[-1].id)
            if len(rows) > limit
            else None
        )
        return Page(items, next_cursor)

    def list_events(
        self,
        job_id: UUID,
        *,
        after_id: int = 0,
        limit: int = 100,
    ) -> Page[CrawlEvent]:
        if isinstance(after_id, bool) or not isinstance(after_id, int) or after_id < 0:
            raise ValidationError("Invalid event cursor.")
        limit = self._bounded_limit(limit)
        with self._connect() as cursor:
            cursor.execute(
                f"""
                SELECT {_EVENT_COLUMNS}
                FROM crawl_job_events
                WHERE job_id = %s AND id > %s
                ORDER BY id ASC
                LIMIT %s
                """,
                (job_id, after_id, limit + 1),
            )
            rows = cursor.fetchall()
        items = tuple(self._event_from_row(row) for row in rows[:limit])
        return Page(items, str(items[-1].id) if len(rows) > limit else None)

    def append_event(
        self,
        job_id: UUID,
        event_type: EventType,
        level: EventLevel,
        *,
        target_id: UUID | None = None,
        safe_message: str = "",
        counters: Mapping[str, int] | None = None,
    ) -> CrawlEvent:
        self._validate_event(event_type, level, safe_message, counters)
        with self._connect() as cursor:
            return self._insert_event(
                cursor, job_id, event_type, level, target_id=target_id,
                safe_message=self._sanitize_message(safe_message), counters=counters,
            )

    def request_cancel(self, job_id: UUID) -> CrawlJob | None:
        with self._connect() as cursor:
            cursor.execute(
                f"SELECT {_JOB_COLUMNS} FROM crawl_jobs WHERE id = %s FOR UPDATE",
                (job_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            job = self._job_from_row(row)
            if job.status is JobStatus.QUEUED:
                cursor.execute(
                    f"""
                    UPDATE crawl_jobs
                    SET status = 'cancelled',
                        cancel_requested_at = COALESCE(cancel_requested_at, now()),
                        finished_at = now(), updated_at = now()
                    WHERE id = %s AND status = 'queued'
                    RETURNING {_JOB_COLUMNS}
                    """,
                    (job_id,),
                )
                changed = cursor.fetchone()
                if changed is not None:
                    cursor.execute(
                        """
                        UPDATE crawl_targets
                        SET status = 'cancelled', finished_at = now(), updated_at = now()
                        WHERE job_id = %s
                        """,
                        (job_id,),
                    )
                    self._insert_event(cursor, job_id, "cancel_requested", "info")
                    self._insert_event(cursor, job_id, "job_cancelled", "info")
                    return self._job_from_row(changed)
            elif job.status is JobStatus.RUNNING:
                cursor.execute(
                    f"""
                    UPDATE crawl_jobs
                    SET status = 'cancelling',
                        cancel_requested_at = COALESCE(cancel_requested_at, now()),
                        updated_at = now()
                    WHERE id = %s AND status = 'running'
                    RETURNING {_JOB_COLUMNS}
                    """,
                    (job_id,),
                )
                changed = cursor.fetchone()
                if changed is not None:
                    self._insert_event(cursor, job_id, "cancel_requested", "info")
                    return self._job_from_row(changed)
            return job

    def claim_next(self, worker_id: str, *, lease_duration: timedelta) -> ClaimedJob | None:
        """Claim one job while serializing the sole supported account."""
        if not isinstance(worker_id, str) or not worker_id.strip() or lease_duration <= timedelta():
            raise ValidationError("Invalid job lease.")
        with self._connect() as cursor:
            cursor.execute(
                f"SELECT {_ACCOUNT_COLUMNS} FROM crawler_account_state WHERE account_key = 'default' FOR UPDATE"
            )
            account_row = cursor.fetchone()
            if account_row is None:
                raise DatabaseError("Default crawler account is unavailable.")
            account = self._account_from_row(account_row)
            account_status = account.status
            if account_status is AccountStatus.COOLDOWN and account.cooldown_until is not None:
                cursor.execute(
                    """
                    UPDATE crawler_account_state
                    SET status = 'ready', cooldown_until = NULL, updated_at = now()
                    WHERE account_key = 'default' AND status = 'cooldown' AND cooldown_until <= now()
                    RETURNING status
                    """
                )
                if cursor.fetchone() is not None:
                    account_status = AccountStatus.READY
            if account_status is not AccountStatus.READY:
                return None
            cursor.execute(
                """
                SELECT id FROM crawl_jobs
                WHERE account_key = 'default' AND mode = 'authenticated'
                  AND status IN ('running', 'cancelling')
                LIMIT 1
                """
            )
            if cursor.fetchone() is not None:
                return None
            cursor.execute(
                """
                SELECT id
                FROM crawl_jobs
                WHERE status = 'queued'
                ORDER BY priority DESC, created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            selected = cursor.fetchone()
            if selected is None:
                return None
            job_id = selected[0]
            cursor.execute(
                f"""
                UPDATE crawl_jobs
                SET status = 'running', worker_id = %s,
                    attempt = attempt + 1, started_at = COALESCE(started_at, now()),
                    heartbeat_at = now(), lease_expires_at = clock_timestamp() + %s::interval,
                    updated_at = now()
                WHERE id = %s AND status = 'queued'
                RETURNING {_JOB_COLUMNS}
                """,
                (worker_id.strip(), self._interval(lease_duration), job_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            job = self._job_from_row(row)
            cursor.execute(
                """
                UPDATE crawler_account_state
                SET last_job_id = %s, last_started_at = now(), updated_at = now()
                WHERE account_key = 'default'
                """,
                (job.id,),
            )
            self._insert_event(cursor, job.id, "job_claimed", "info")
            cursor.execute(
                f"SELECT {_TARGET_COLUMNS} FROM crawl_targets WHERE job_id = %s ORDER BY position, id",
                (job.id,),
            )
            return ClaimedJob(job, tuple(self._target_from_row(item) for item in cursor.fetchall()))

    def heartbeat(self, job_id: UUID, worker_id: str, *, lease_duration: timedelta) -> bool:
        if lease_duration <= timedelta():
            raise ValidationError("Invalid job lease.")
        with self._connect() as cursor:
            cursor.execute(
                """
                UPDATE crawl_jobs
                SET heartbeat_at = now(), lease_expires_at = clock_timestamp() + %s::interval, updated_at = now()
                WHERE id = %s AND status IN ('running', 'cancelling') AND worker_id = %s
                  AND lease_expires_at > clock_timestamp()
                RETURNING id
                """,
                (self._interval(lease_duration), job_id, worker_id),
            )
            return cursor.fetchone() is not None

    def start_target(self, job_id: UUID, target_id: UUID, worker_id: str) -> bool:
        with self._connect() as cursor:
            if not self._lock_owned_job(cursor, job_id, worker_id, statuses=("running",)):
                return False
            cursor.execute(
                """
                UPDATE crawl_targets AS target
                SET status = 'running', attempt = target.attempt + 1, started_at = COALESCE(target.started_at, now()),
                    updated_at = now()
                WHERE target.id = %s AND target.job_id = %s AND target.status = 'pending'
                RETURNING target.id
                """,
                (target_id, job_id),
            )
            changed = cursor.fetchone() is not None
            if changed:
                cursor.execute("UPDATE crawl_jobs SET current_target_id = %s, updated_at = now() WHERE id = %s", (target_id, job_id))
                self._insert_event(cursor, job_id, "target_started", "info", target_id=target_id)
            return changed

    def update_target_progress(
        self, job_id: UUID, target_id: UUID, worker_id: str, *, steps_completed: int = 0,
        items_discovered: int = 0, users_persisted: int = 0, provider_retries_required: int = 0,
    ) -> bool:
        values = (steps_completed, items_discovered, users_persisted, provider_retries_required)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValidationError("Invalid target progress.")
        with self._connect() as cursor:
            if not self._lock_owned_job(cursor, job_id, worker_id, statuses=("running",)):
                return False
            cursor.execute(
                """
                UPDATE crawl_targets AS target
                SET steps_completed = GREATEST(target.steps_completed, %s),
                    items_discovered = GREATEST(target.items_discovered, %s),
                    users_persisted = GREATEST(target.users_persisted, %s),
                    provider_retries_required = GREATEST(target.provider_retries_required, %s),
                    updated_at = now()
                WHERE target.id = %s AND target.job_id = %s AND target.status = 'running'
                RETURNING target.steps_completed, target.items_discovered, target.users_persisted
                """,
                (*values, target_id, job_id),
            )
            changed = cursor.fetchone()
            if changed is not None:
                cursor.execute(
                    """
                    UPDATE crawl_jobs
                    SET discovered_users = (
                            SELECT COALESCE(SUM(items_discovered), 0)
                            FROM crawl_targets WHERE job_id = %s
                        ),
                        persisted_users = (
                            SELECT COALESCE(SUM(users_persisted), 0)
                            FROM crawl_targets WHERE job_id = %s
                        ),
                        provider_retries_required = (
                            SELECT COALESCE(SUM(provider_retries_required), 0)
                            FROM crawl_targets WHERE job_id = %s
                        ),
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (job_id, job_id, job_id, job_id),
                )
                self._insert_event(cursor, job_id, "target_progress", "info", target_id=target_id, counters={
                    "steps_completed": changed[0], "items_discovered": changed[1], "users_persisted": changed[2],
                })
            return changed is not None

    def finish_target(
        self, job_id: UUID, target_id: UUID, worker_id: str, *, status: TargetStatus,
        error_code: str = "", error_message: str = "",
    ) -> bool:
        if status not in {TargetStatus.SUCCEEDED, TargetStatus.PARTIAL, TargetStatus.FAILED, TargetStatus.CANCELLED, TargetStatus.BLOCKED}:
            raise ValidationError("Invalid target terminal status.")
        with self._connect() as cursor:
            if not self._lock_owned_job(cursor, job_id, worker_id, statuses=("running",)):
                return False
            cursor.execute(
                """
                UPDATE crawl_targets AS target
                SET status = %s, finished_at = now(), error_code = %s, error_message = %s, updated_at = now()
                WHERE target.id = %s AND target.job_id = %s AND target.status = 'running'
                RETURNING target.id
                """,
                (status.value, error_code, self._sanitize_message(error_message), target_id, job_id),
            )
            changed = cursor.fetchone() is not None
            if changed:
                cursor.execute("UPDATE crawl_jobs SET current_target_id = NULL, updated_at = now() WHERE id = %s", (job_id,))
                self._insert_event(cursor, job_id, "target_completed", "info", target_id=target_id)
            return changed

    def finish_job(
        self, job_id: UUID, worker_id: str, *, status: JobStatus,
        error_code: str = "", error_message: str = "",
        active_target_id: UUID | None = None,
        active_target_status: TargetStatus | None = None,
        account_signal: str | None = None,
        normal_cooldown: timedelta = timedelta(hours=1),
        rate_limit_cooldown: timedelta = timedelta(hours=6),
        now: datetime | None = None,
    ) -> bool:
        """Atomically finalize an owned lease and its account policy.

        Lock order is account, claimed job, then all job targets.  This keeps a
        stale worker from applying an account hold after it no longer owns the
        job, and prevents a cancellation from being mistaken for lease loss.
        """
        if status not in {JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.BLOCKED}:
            raise ValidationError("Invalid job terminal status.")
        if (active_target_id is None) is not (active_target_status is None):
            raise ValidationError("Active target finalization is incomplete.")
        if active_target_status is not None and active_target_status not in {
            TargetStatus.SUCCEEDED,
            TargetStatus.PARTIAL,
            TargetStatus.FAILED,
            TargetStatus.CANCELLED,
            TargetStatus.BLOCKED,
        }:
            raise ValidationError("Invalid active target terminal status.")
        self._validate_worker_cooldowns(normal_cooldown, rate_limit_cooldown)
        if now is not None and (now.tzinfo is None or now.utcoffset() is None):
            raise ValidationError("Account policy time must be UTC-aware.")
        with self._connect() as cursor:
            cursor.execute(f"SELECT {_ACCOUNT_COLUMNS} FROM crawler_account_state WHERE account_key = 'default' FOR UPDATE")
            account_row = cursor.fetchone()
            if account_row is None:
                raise DatabaseError("Default crawler account is unavailable.")
            account = self._account_from_row(account_row)
            if now is None:
                cursor.execute("SELECT now()")
                clock_row = cursor.fetchone()
                if clock_row is None or not isinstance(clock_row[0], datetime):
                    raise DatabaseError("Database clock is unavailable.")
                policy_now = clock_row[0]
            else:
                policy_now = now
            cursor.execute(
                f"""
                SELECT {_JOB_COLUMNS}
                FROM crawl_jobs
                WHERE id = %s AND worker_id = %s AND status IN ('running', 'cancelling')
                  AND lease_expires_at > clock_timestamp()
                FOR UPDATE
                """,
                (job_id, worker_id),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            job = self._job_from_row(row)
            cancellation_wins = job.status is JobStatus.CANCELLING and status is not JobStatus.BLOCKED
            effective_status = (
                JobStatus.CANCELLED
                if cancellation_wins
                else status
            )
            effective_active_target_status = (
                TargetStatus.CANCELLED
                if cancellation_wins
                else active_target_status
            )
            # A cancellation raced with the worker's terminal decision.  Store
            # one canonical, non-sensitive cancellation result for both the
            # active target and job.  A safety block deliberately wins this
            # rule and keeps its specific safety code.
            effective_error_code = "crawl_cancelled" if cancellation_wins else error_code
            effective_error_message = "" if cancellation_wins else self._sanitize_message(error_message)
            inherited = {
                JobStatus.SUCCEEDED: TargetStatus.SKIPPED,
                JobStatus.PARTIAL: TargetStatus.PARTIAL,
                JobStatus.FAILED: TargetStatus.FAILED,
                JobStatus.CANCELLED: TargetStatus.CANCELLED,
                JobStatus.BLOCKED: TargetStatus.BLOCKED,
            }[effective_status]
            cursor.execute(
                "SELECT id FROM crawl_targets WHERE job_id = %s FOR UPDATE",
                (job_id,),
            )
            cursor.fetchall()
            if active_target_id is not None and effective_active_target_status is not None:
                cursor.execute(
                    """
                    UPDATE crawl_targets
                    SET status = %s, finished_at = now(), error_code = %s,
                        error_message = %s, updated_at = now()
                    WHERE id = %s AND job_id = %s AND status = 'running'
                    """,
                    (
                        effective_active_target_status.value,
                        effective_error_code,
                        effective_error_message,
                        active_target_id,
                        job_id,
                    ),
                )
            cursor.execute(
                """
                UPDATE crawl_targets
                SET status = %s, finished_at = now(), updated_at = now()
                WHERE job_id = %s AND status IN ('pending', 'running')
                """,
                (inherited.value, job_id),
            )
            cursor.execute(
                """
                UPDATE crawl_jobs
                SET status = %s, finished_at = now(), lease_expires_at = NULL,
                    heartbeat_at = now(), current_target_id = NULL, error_code = %s,
                    error_message = %s,
                    completed_targets = (
                        SELECT count(*) FROM crawl_targets
                        WHERE job_id = %s AND status = 'succeeded'
                    ),
                    failed_targets = (
                        SELECT count(*) FROM crawl_targets
                        WHERE job_id = %s
                          AND status IN ('partial', 'failed', 'cancelled', 'blocked')
                    ),
                    discovered_users = (
                        SELECT COALESCE(SUM(items_discovered), 0)
                        FROM crawl_targets WHERE job_id = %s
                    ),
                    persisted_users = (
                        SELECT COALESCE(SUM(users_persisted), 0)
                        FROM crawl_targets WHERE job_id = %s
                    ),
                    provider_retries_required = (
                        SELECT COALESCE(SUM(provider_retries_required), 0)
                        FROM crawl_targets WHERE job_id = %s
                    ),
                    updated_at = now()
                WHERE id = %s
                """,
                (
                    effective_status.value,
                    effective_error_code,
                    effective_error_message,
                    job_id,
                    job_id,
                    job_id,
                    job_id,
                    job_id,
                    job_id,
                ),
            )
            if account_signal is not None:
                self._apply_account_signal_locked(
                    cursor,
                    account,
                    account_signal,
                    now=policy_now,
                    rate_limit_cooldown=rate_limit_cooldown,
                )
                cursor.execute(
                    """UPDATE crawler_account_state
                    SET last_finished_at = %s, updated_at = %s
                    WHERE account_key = 'default'""",
                    (policy_now, policy_now),
                )
                self._insert_event(
                    cursor,
                    job_id,
                    (
                        "facebook_rate_limited"
                        if account_signal in {
                            "facebook_rate_limited",
                            "authenticated_rate_limited",
                        }
                        else "account_warning"
                    ),
                    "warning",
                )
            elif effective_status in {
                JobStatus.SUCCEEDED,
                JobStatus.PARTIAL,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                self._apply_normal_cooldown_locked(
                    cursor,
                    normal_cooldown=normal_cooldown,
                    now=policy_now,
                )
            self._insert_event(
                cursor,
                job_id,
                (
                    "job_blocked"
                    if effective_status is JobStatus.BLOCKED
                    else "job_cancelled"
                    if effective_status is JobStatus.CANCELLED
                    else "job_completed"
                ),
                "info",
            )
            return True

    def recover_stale_jobs(self, *, limit: int = 100) -> tuple[UUID, ...]:
        """Recover bounded stale work one job per short transaction."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValidationError("Recovery limit must be positive.")
        recovered: list[UUID] = []
        for _ in range(min(limit, 100)):
            recovered_job = self._recover_one_stale_job()
            if recovered_job is None:
                break
            recovered.append(recovered_job)
        return tuple(recovered)

    def _recover_one_stale_job(self) -> UUID | None:
        with self._connect() as cursor:
            cursor.execute("SELECT account_key FROM crawler_account_state WHERE account_key = 'default' FOR UPDATE")
            if cursor.fetchone() is None:
                raise DatabaseError("Default crawler account is unavailable.")
            cursor.execute(
                f"""SELECT {_JOB_COLUMNS} FROM crawl_jobs
                WHERE status IN ('running', 'cancelling') AND lease_expires_at <= clock_timestamp()
                ORDER BY lease_expires_at, id FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            row = cursor.fetchone()
            if row is None:
                return None
            job = self._job_from_row(row)
            cursor.execute(
                """
                SELECT
                    EXISTS (
                        SELECT 1 FROM crawl_targets
                        WHERE job_id = %s
                          AND (attempt > 0 OR started_at IS NOT NULL OR status = 'running')
                    ),
                    EXISTS (
                        SELECT 1 FROM crawl_job_events
                        WHERE job_id = %s AND event_type = 'browser_started'
                    )
                """,
                (job.id, job.id),
            )
            target_activity, browser_started = cursor.fetchone()
            uncertain = bool(target_activity or browser_started)
            if not uncertain:
                cursor.execute("UPDATE crawl_jobs SET status = 'queued', worker_id = NULL, lease_expires_at = NULL, heartbeat_at = NULL, current_target_id = NULL, updated_at = now() WHERE id = %s AND status IN ('running', 'cancelling')", (job.id,))
            else:
                cursor.execute("UPDATE crawl_targets SET status = 'blocked', finished_at = now(), updated_at = now() WHERE job_id = %s AND (attempt > 0 OR status IN ('pending', 'running'))", (job.id,))
                cursor.execute(
                    """
                    UPDATE crawl_jobs
                    SET status = 'blocked', finished_at = now(),
                        lease_expires_at = NULL, current_target_id = NULL,
                        error_code = 'lease_recovery',
                        error_message = 'Lease expired during target execution.',
                        completed_targets = (
                            SELECT count(*) FROM crawl_targets
                            WHERE job_id = %s AND status = 'succeeded'
                        ),
                        failed_targets = (
                            SELECT count(*) FROM crawl_targets
                            WHERE job_id = %s
                              AND status IN ('partial', 'failed', 'cancelled', 'blocked')
                        ),
                        updated_at = now()
                    WHERE id = %s AND status IN ('running', 'cancelling')
                    """,
                    (job.id, job.id, job.id),
                )
                cursor.execute(
                    """
                    UPDATE crawler_account_state
                    SET status = CASE
                            WHEN status = 'blocked' THEN 'blocked'
                            ELSE 'manual_review'
                        END,
                        last_finished_at = now(),
                        last_warning_code = 'account_recovery',
                        last_warning_at = now(), block_reason = 'lease_recovery',
                        updated_at = now()
                    WHERE account_key = 'default'
                    """
                )
                self._insert_event(cursor, job.id, "account_warning", "warning", safe_message="Lease expired during target execution.")
                self._insert_event(cursor, job.id, "job_blocked", "info")
            return job.id

    def get_account(self, account_key: str = "default") -> CrawlerAccountState | None:
        with self._connect() as cursor:
            cursor.execute(f"SELECT {_ACCOUNT_COLUMNS} FROM crawler_account_state WHERE account_key = %s", (account_key,))
            row = cursor.fetchone()
        return None if row is None else self._account_from_row(row)

    def refresh_account(self, account_key: str, *, now: datetime) -> CrawlerAccountState | None:
        """Lock and release only an expired ordinary cooldown."""
        with self._connect() as cursor:
            cursor.execute(f"SELECT {_ACCOUNT_COLUMNS} FROM crawler_account_state WHERE account_key = %s FOR UPDATE", (account_key,))
            row = cursor.fetchone()
            if row is None:
                return None
            account = self._account_from_row(row)
            if account.status is AccountStatus.COOLDOWN and account.cooldown_until is not None and account.cooldown_until <= now:
                cursor.execute(f"UPDATE crawler_account_state SET status = 'ready', cooldown_until = NULL, updated_at = %s WHERE account_key = %s AND status = 'cooldown' RETURNING {_ACCOUNT_COLUMNS}", (now, account_key))
                return self._account_from_row(cursor.fetchone())
            return account

    def apply_account_signal(self, account_key: str, signal: str, *, now: datetime) -> CrawlerAccountState | None:
        """Atomically apply a conservative safety signal while holding the account row."""
        with self._connect() as cursor:
            cursor.execute(f"SELECT {_ACCOUNT_COLUMNS} FROM crawler_account_state WHERE account_key = %s FOR UPDATE", (account_key,))
            row = cursor.fetchone()
            if row is None:
                return None
            return self._apply_account_signal_locked(
                cursor,
                self._account_from_row(row),
                signal,
                now=now,
                rate_limit_cooldown=timedelta(hours=6),
            )

    def acknowledge_account(self, account_key: str, *, now: datetime) -> CrawlerAccountState | None:
        with self._connect() as cursor:
            cursor.execute(f"SELECT {_ACCOUNT_COLUMNS} FROM crawler_account_state WHERE account_key = %s FOR UPDATE", (account_key,))
            row = cursor.fetchone()
            if row is None:
                return None
            account = self._account_from_row(row)
            status = AccountStatus.COOLDOWN if account.cooldown_until is not None and account.cooldown_until > now else AccountStatus.READY
            cursor.execute(
                f"UPDATE crawler_account_state SET status = %s, cooldown_until = %s, acknowledged_at = %s, updated_at = %s WHERE account_key = %s RETURNING {_ACCOUNT_COLUMNS}",
                (status.value, account.cooldown_until if status is AccountStatus.COOLDOWN else None, now, now, account_key),
            )
            return self._account_from_row(cursor.fetchone())

    def set_account_state(
        self, *, account_key: str, status: AccountStatus, now: datetime,
        cooldown_until: datetime | None = None, last_job_id: UUID | None = None,
        rate_limit_count_24h: int | None = None, last_rate_limit_at: datetime | None = None,
        last_warning_code: str = "", block_reason: str = "", acknowledged_at: datetime | None = None,
    ) -> CrawlerAccountState:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValidationError("Account policy time must be UTC-aware.")
        with self._connect() as cursor:
            cursor.execute(
                f"""
                UPDATE crawler_account_state
                SET status = %s, cooldown_until = %s, last_job_id = COALESCE(%s, last_job_id),
                    rate_limit_count_24h = COALESCE(%s, rate_limit_count_24h),
                    last_rate_limit_at = COALESCE(%s, last_rate_limit_at), last_warning_code = CASE WHEN %s <> '' THEN %s ELSE last_warning_code END,
                    last_warning_at = CASE WHEN %s <> '' THEN %s ELSE last_warning_at END,
                    block_reason = %s, acknowledged_at = %s, updated_at = %s
                WHERE account_key = %s
                RETURNING {_ACCOUNT_COLUMNS}
                """,
                (status.value, cooldown_until, last_job_id, rate_limit_count_24h,
                 last_rate_limit_at, last_warning_code, last_warning_code, last_warning_code, now,
                 block_reason, acknowledged_at, now, account_key),
            )
            row = cursor.fetchone()
            if row is None:
                raise DatabaseError("Crawler account is unavailable.")
            return self._account_from_row(row)

    def get_retry_child(self, parent_id: UUID) -> CrawlJob | None:
        with self._connect() as cursor:
            cursor.execute(f"SELECT {_JOB_COLUMNS} FROM crawl_jobs WHERE retry_of_job_id = %s", (parent_id,))
            row = cursor.fetchone()
        return None if row is None else self._job_from_row(row)

    def create_retry_child(self, parent_id: UUID) -> CrawlJob | None:
        """Copy only retryable targets and preserve their server-owned checkpoints."""
        child_id = uuid4()
        with self._connect() as cursor:
            cursor.execute(f"SELECT {_ACCOUNT_COLUMNS} FROM crawler_account_state WHERE account_key = 'default' FOR UPDATE")
            account_row = cursor.fetchone()
            if account_row is None:
                return None
            if self._account_from_row(account_row).status is not AccountStatus.READY:
                return None
            cursor.execute(f"SELECT {_JOB_COLUMNS} FROM crawl_jobs WHERE id = %s FOR UPDATE", (parent_id,))
            parent_row = cursor.fetchone()
            if parent_row is None:
                return None
            cursor.execute(f"SELECT {_JOB_COLUMNS} FROM crawl_jobs WHERE retry_of_job_id = %s FOR UPDATE", (parent_id,))
            existing = cursor.fetchone()
            if existing is not None:
                return self._job_from_row(existing)
            cursor.execute(
                """
                SELECT id, target_key, target_url, target_kind, position, checkpoint_path
                FROM crawl_targets
                WHERE job_id = %s AND status IN ('partial', 'failed', 'cancelled', 'blocked')
                ORDER BY position, id
                """,
                (parent_id,),
            )
            targets = cursor.fetchall()
            if not targets:
                return None
            cursor.execute(
                f"""
                INSERT INTO crawl_jobs ({_JOB_COLUMNS})
                SELECT %s, mode, action, account_key, 'queued', request_options,
                    request_fingerprint, NULL, %s, priority, 0, NULL, NULL, NULL, NULL, NULL,
                    NULL, now(), now(), %s, 0, 0, 0, 0, 0, NULL, '', ''
                FROM crawl_jobs WHERE id = %s
                RETURNING {_JOB_COLUMNS}
                """,
                (child_id, parent_id, len(targets), parent_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            for position, (_, key, url, kind, _, checkpoint_path) in enumerate(targets):
                cursor.execute(
                    """
                    INSERT INTO crawl_targets (
                        id, job_id, target_key, target_url, target_kind, position, status, checkpoint_path
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)
                    """,
                    (uuid4(), child_id, key, url, kind, position, checkpoint_path),
                )
            self._insert_event(cursor, child_id, "job_created", "info")
            return self._job_from_row(row)

    @staticmethod
    def _interval(value: timedelta) -> str:
        return f"{value.total_seconds():.6f} seconds"

    @staticmethod
    def _lock_owned_job(cursor: Any, job_id: UUID, worker_id: str, *, statuses: tuple[str, ...]) -> bool:
        cursor.execute(
            """SELECT id FROM crawl_jobs WHERE id = %s AND worker_id = %s
            AND status = ANY(%s) AND lease_expires_at > clock_timestamp() FOR UPDATE""",
            (job_id, worker_id, list(statuses)),
        )
        return cursor.fetchone() is not None

    @staticmethod
    def _validate_worker_cooldowns(
        normal_cooldown: timedelta,
        rate_limit_cooldown: timedelta,
    ) -> None:
        if (
            not isinstance(normal_cooldown, timedelta)
            or not isinstance(rate_limit_cooldown, timedelta)
            or normal_cooldown < timedelta(hours=1)
            or rate_limit_cooldown < timedelta(hours=6)
        ):
            raise ValidationError("Worker cooldowns may not relax account safety policy.")

    def _apply_normal_cooldown_locked(
        self,
        cursor: Any,
        *,
        normal_cooldown: timedelta,
        now: datetime,
    ) -> None:
        cursor.execute(
            """
            UPDATE crawler_account_state
            SET status = CASE WHEN status IN ('ready', 'cooldown') THEN 'cooldown' ELSE status END,
                cooldown_until = CASE WHEN status IN ('ready', 'cooldown')
                    THEN GREATEST(COALESCE(cooldown_until, %s), %s)
                    ELSE cooldown_until END,
                last_finished_at = %s, updated_at = %s
            WHERE account_key = 'default'
            """,
            (now, now + normal_cooldown, now, now),
        )

    def _apply_account_signal_locked(
        self,
        cursor: Any,
        account: CrawlerAccountState,
        signal: str,
        *,
        now: datetime,
        rate_limit_cooldown: timedelta,
    ) -> CrawlerAccountState:
        if signal in {"facebook_rate_limited", "authenticated_rate_limited"}:
            recent = (
                account.last_rate_limit_at is not None
                and now - account.last_rate_limit_at <= timedelta(hours=24)
            )
            count = account.rate_limit_count_24h + 1 if recent else 1
            status = AccountStatus.MANUAL_REVIEW if count >= 2 else AccountStatus.COOLDOWN
            cooldown = max(
                account.cooldown_until or now,
                now + rate_limit_cooldown,
            )
            cursor.execute(
                f"""UPDATE crawler_account_state
                SET status = CASE WHEN status IN ('manual_review', 'blocked') THEN status ELSE %s END,
                    cooldown_until = %s, rate_limit_count_24h = %s, last_rate_limit_at = %s,
                    last_warning_code = 'facebook_rate_limited', last_warning_at = %s,
                    block_reason = CASE WHEN %s = 'manual_review' THEN 'repeated_rate_limit' ELSE block_reason END,
                    updated_at = %s
                WHERE account_key = %s RETURNING {_ACCOUNT_COLUMNS}""",
                (status.value, cooldown, count, now, now, status.value, now, account.account_key),
            )
        else:
            status = AccountStatus.BLOCKED if signal == "session_expired" else AccountStatus.MANUAL_REVIEW
            cursor.execute(
                f"""UPDATE crawler_account_state
                SET status = CASE WHEN status = 'blocked' THEN 'blocked' ELSE %s END,
                    last_warning_code = %s, last_warning_at = %s, block_reason = %s, updated_at = %s
                WHERE account_key = %s RETURNING {_ACCOUNT_COLUMNS}""",
                (status.value, signal, now, signal, now, account.account_key),
            )
        row = cursor.fetchone()
        if row is None:
            raise DatabaseError("Crawler account is unavailable.")
        return self._account_from_row(row)

    @staticmethod
    def _validate_idempotency_key(value: str) -> None:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 128
            or any(not character.isprintable() or character.isspace() for character in value)
        ):
            raise ValidationError("Invalid idempotency key.")

    @staticmethod
    def _bounded_limit(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValidationError("Pagination limit must be positive.")
        return min(value, 100)

    @staticmethod
    def _sanitize_message(value: str) -> str:
        normalized = "".join(
            character if character.isprintable() else " " for character in value
        )
        return " ".join(normalized.split())[:500]

    def _validate_event(
        self,
        event_type: str,
        level: str,
        safe_message: str,
        counters: Mapping[str, int] | None,
    ) -> None:
        if event_type not in _EVENT_TYPES or level not in _EVENT_LEVELS:
            raise ValidationError("Invalid crawl event.")
        if not isinstance(safe_message, str):
            raise ValidationError("Invalid crawl event message.")
        if counters is None:
            return
        if not isinstance(counters, Mapping):
            raise ValidationError("Invalid crawl event counters.")
        for key, value in counters.items():
            if key not in _COUNTER_KEYS or isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValidationError("Invalid crawl event counters.")

    def _insert_event(
        self,
        cursor: Any,
        job_id: UUID,
        event_type: str,
        level: str,
        *,
        target_id: UUID | None = None,
        safe_message: str = "",
        counters: Mapping[str, int] | None = None,
    ) -> CrawlEvent:
        cursor.execute(
            f"""
            INSERT INTO crawl_job_events (
                job_id, target_id, event_type, level, safe_message, counters
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING {_EVENT_COLUMNS}
            """,
            (
                job_id, target_id, event_type, level, safe_message,
                json.dumps(dict(counters or {}), sort_keys=True),
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise DatabaseError("Database event insert failed.")
        return self._event_from_row(row)

    @staticmethod
    def _job_from_row(row: tuple[object, ...]) -> CrawlJob:
        options = row[5]
        if not isinstance(options, Mapping):
            raise DatabaseError("Database job data is invalid.")
        action = AuthenticatedAction(str(row[2]))
        option_values = dict(options)
        if action not in {
            AuthenticatedAction.FRIENDS,
            AuthenticatedAction.FOLLOWERS,
        }:
            # The canonical database representation always includes defaults,
            # while the public parser intentionally permits these two knobs
            # only for relationship jobs.
            option_values.pop("depth", None)
            option_values.pop("max_users", None)
        return CrawlJob(
            id=UUID(str(row[0])), mode=ScrapeMode(str(row[1])),
            action=action, account_key=str(row[3]),
            status=JobStatus(str(row[4])),
            request_options=SafeJobOptions.from_mapping(action, option_values),
            request_fingerprint=str(row[6]), idempotency_key=row[7] if isinstance(row[7], str) else None,
            retry_of_job_id=UUID(str(row[8])) if row[8] is not None else None,
            priority=int(row[9]), attempt=int(row[10]), worker_id=row[11] if isinstance(row[11], str) else None,
            lease_expires_at=row[12], heartbeat_at=row[13], cancel_requested_at=row[14],
            started_at=row[15], finished_at=row[16], created_at=row[17], updated_at=row[18],
            requested_targets=int(row[19]), completed_targets=int(row[20]), failed_targets=int(row[21]),
            discovered_users=int(row[22]), persisted_users=int(row[23]),
            provider_retries_required=int(row[24]),
            current_target_id=UUID(str(row[25])) if row[25] is not None else None,
            error_code=str(row[26]), error_message=str(row[27]),
        )

    @staticmethod
    def _target_from_row(row: tuple[object, ...]) -> CrawlTarget:
        return CrawlTarget(
            id=UUID(str(row[0])), job_id=UUID(str(row[1])), target_key=str(row[2]),
            target_url=str(row[3]), target_kind=str(row[4]), position=int(row[5]),
            status=TargetStatus(str(row[6])), checkpoint_path=str(row[7]),
            created_at=row[8], updated_at=row[9], attempt=int(row[10]),
            started_at=row[11], finished_at=row[12], steps_completed=int(row[13]),
            items_discovered=int(row[14]), users_persisted=int(row[15]),
            provider_retries_required=int(row[16]), error_code=str(row[17]), error_message=str(row[18]),
        )

    @staticmethod
    def _event_from_row(row: tuple[object, ...]) -> CrawlEvent:
        counters = row[7]
        if not isinstance(counters, Mapping):
            raise DatabaseError("Database event data is invalid.")
        return CrawlEvent(
            id=int(row[0]), job_id=UUID(str(row[1])), event_type=str(row[2]),
            level=str(row[3]), created_at=row[4],
            target_id=UUID(str(row[5])) if row[5] is not None else None,
            safe_message=str(row[6]), counters={str(key): int(value) for key, value in counters.items()},
        )

    @staticmethod
    def _account_from_row(row: tuple[object, ...]) -> CrawlerAccountState:
        return CrawlerAccountState(
            account_key=str(row[0]), status=AccountStatus(row[1]), cooldown_until=row[2],
            last_job_id=row[3], last_started_at=row[4], last_finished_at=row[5],
            rate_limit_count_24h=int(row[6]), last_rate_limit_at=row[7],
            last_warning_code=str(row[8]), last_warning_at=row[9], block_reason=str(row[10]),
            acknowledged_at=row[11], created_at=row[12], updated_at=row[13],
        )

    @staticmethod
    def _encode_uuid_cursor(sort_at: datetime, row_id: UUID) -> str:
        return base64.urlsafe_b64encode(json.dumps({"sort_at": sort_at.astimezone(UTC).isoformat(), "row_id": str(row_id)}, separators=(",", ":")).encode()).decode().rstrip("=")

    @staticmethod
    def _decode_uuid_cursor(value: str, *, name: str) -> tuple[datetime, UUID]:
        try:
            payload = JobRepository._decode_cursor_payload(value)
            sort_at = datetime.fromisoformat(payload["sort_at"])
            if sort_at.tzinfo is None or sort_at.utcoffset() is None:
                raise ValueError
            return sort_at, UUID(payload["row_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValidationError(f"Invalid {name} pagination cursor.") from error

    @staticmethod
    def _encode_target_cursor(position: int, row_id: UUID) -> str:
        return base64.urlsafe_b64encode(json.dumps({"position": position, "row_id": str(row_id)}, separators=(",", ":")).encode()).decode().rstrip("=")

    @staticmethod
    def _decode_target_cursor(value: str) -> tuple[int, UUID]:
        try:
            payload = JobRepository._decode_cursor_payload(value)
            position = payload["position"]
            if isinstance(position, bool) or not isinstance(position, int) or position < 0:
                raise ValueError
            return position, UUID(payload["row_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValidationError("Invalid target pagination cursor.") from error

    @staticmethod
    def _decode_cursor_payload(value: str) -> dict[str, object]:
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise ValueError
        encoded = value.encode("ascii")
        decoded = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
        payload = json.loads(decoded)
        if not isinstance(payload, dict):
            raise ValueError
        return payload
