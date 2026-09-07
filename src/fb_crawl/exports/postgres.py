from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.exports.models import ExportFormat, ExportJob, ExportStatus
from fb_data_pipeline.repositories.errors import DatabaseError


_COLUMNS = """
    id, account_id, format, filter_snapshot, status, owner_token,
    leased_until, attempt_count, safe_error_code, artifact_path,
    expires_at, created_at, updated_at, completed_at
"""


class ExportQueueFull(ValidationError):
    code = "export_queue_full"


class PostgresExportRepository:
    def __init__(
        self,
        database_url: str,
        *,
        statement_timeout_seconds: float = 5.0,
        lease_duration: timedelta = timedelta(minutes=15),
        max_active_jobs_per_account: int = 5,
        connect_factory=psycopg.connect,
    ) -> None:
        self.database_url = database_url
        self.statement_timeout_ms = max(
            1, round(statement_timeout_seconds * 1000)
        )
        self.lease_duration = lease_duration
        self.max_active_jobs_per_account = max_active_jobs_per_account
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

    def create(
        self,
        account_id: int,
        format_name: ExportFormat,
        filter_snapshot: Mapping[str, object],
        now: datetime,
        *,
        job_id: UUID | None = None,
    ) -> ExportJob:
        self._account_id(account_id)
        try:
            resolved_format = ExportFormat(format_name)
        except (TypeError, ValueError) as error:
            raise ValidationError("Invalid export format.") from error
        if not isinstance(filter_snapshot, Mapping):
            raise ValidationError("Invalid export filters.")
        resolved_id = job_id or uuid4()
        self._job_id(resolved_id)
        try:
            snapshot_json = json.dumps(
                dict(filter_snapshot), sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError) as error:
            raise ValidationError("Invalid export filters.") from error
        with self._connect() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (account_id,))
            cursor.execute(
                f"""
                SELECT {_COLUMNS}
                FROM export_jobs
                WHERE account_id = %s AND format = %s
                  AND filter_snapshot = %s::jsonb
                  AND status IN ('queued', 'running')
                ORDER BY created_at, id
                LIMIT 1
                """,
                (account_id, resolved_format.value, snapshot_json),
            )
            existing = cursor.fetchone()
            if existing is not None:
                return self._job(existing)
            cursor.execute(
                """
                SELECT count(*)
                FROM export_jobs
                WHERE account_id = %s AND status IN ('queued', 'running')
                """,
                (account_id,),
            )
            active = cursor.fetchone()
            if active is None:
                raise DatabaseError("Database export queue read failed.")
            if int(active[0]) >= self.max_active_jobs_per_account:
                raise ExportQueueFull("Too many active export jobs.")
            cursor.execute(
                f"""
                INSERT INTO export_jobs (
                    id, account_id, format, filter_snapshot, status,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s::jsonb, 'queued', %s, %s)
                RETURNING {_COLUMNS}
                """,
                (
                    resolved_id,
                    account_id,
                    resolved_format.value,
                    snapshot_json,
                    now,
                    now,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise DatabaseError("Database export creation failed.")
        return self._job(row)

    def get(self, account_id: int, job_id: UUID) -> ExportJob | None:
        self._account_id(account_id)
        self._job_id(job_id)
        with self._connect() as cursor:
            cursor.execute(
                f"""SELECT {_COLUMNS}
                FROM export_jobs WHERE account_id = %s AND id = %s""",
                (account_id, job_id),
            )
            row = cursor.fetchone()
        return None if row is None else self._job(row)

    def delete(self, account_id: int, job_id: UUID) -> tuple[bool, str]:
        self._account_id(account_id)
        self._job_id(job_id)
        with self._connect() as cursor:
            cursor.execute(
                """
                DELETE FROM export_jobs
                WHERE account_id = %s AND id = %s
                RETURNING artifact_path
                """,
                (account_id, job_id),
            )
            row = cursor.fetchone()
        return (False, "") if row is None else (True, str(row[0] or ""))

    def claim_next(self, owner: str, now: datetime) -> ExportJob | None:
        self._owner(owner)
        lease_until = now + self.lease_duration
        with self._connect() as cursor:
            cursor.execute(
                f"""
                WITH candidate AS (
                    SELECT id
                    FROM export_jobs
                    WHERE status = 'queued'
                       OR (status = 'running' AND leased_until <= %s)
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE export_jobs AS jobs
                SET status = 'running', owner_token = %s,
                    leased_until = %s,
                    attempt_count = jobs.attempt_count + 1,
                    safe_error_code = NULL, updated_at = %s
                FROM candidate
                WHERE jobs.id = candidate.id
                RETURNING {_returning("jobs")}
                """,
                (now, owner, lease_until, now),
            )
            row = cursor.fetchone()
        return None if row is None else self._job(row)

    def touch_worker(self, worker_id: str, now: datetime) -> None:
        self._owner(worker_id)
        with self._connect() as cursor:
            cursor.execute(
                """
                INSERT INTO product_worker_heartbeats (
                    worker_kind, worker_id, heartbeat_at
                ) VALUES ('export', %s, %s)
                ON CONFLICT (worker_kind) DO UPDATE
                SET worker_id = EXCLUDED.worker_id,
                    heartbeat_at = EXCLUDED.heartbeat_at
                """,
                (worker_id, now),
            )

    def worker_is_recent(
        self,
        now: datetime,
        max_age: timedelta,
    ) -> bool:
        with self._connect() as cursor:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM product_worker_heartbeats
                    WHERE worker_kind = 'export'
                      AND heartbeat_at >= %s
                )
                """,
                (now - max_age,),
            )
            row = cursor.fetchone()
        return bool(row and row[0])

    def worker_last_seen(self) -> datetime | None:
        with self._connect() as cursor:
            cursor.execute(
                """
                SELECT heartbeat_at
                FROM product_worker_heartbeats
                WHERE worker_kind = 'export'
                """
            )
            row = cursor.fetchone()
        return None if row is None else row[0]

    def complete(
        self,
        job_id: UUID,
        owner: str,
        artifact_path: str,
        completed_at: datetime,
        expires_at: datetime,
    ) -> bool:
        self._job_id(job_id)
        self._owner(owner)
        if not artifact_path:
            raise ValidationError("Invalid export artifact path.")
        with self._connect() as cursor:
            cursor.execute(
                """
                UPDATE export_jobs
                SET status = 'completed', artifact_path = %s,
                    completed_at = %s, expires_at = %s,
                    updated_at = %s, owner_token = NULL,
                    leased_until = NULL, safe_error_code = NULL
                WHERE id = %s AND status = 'running'
                  AND owner_token = %s AND leased_until > %s
                """,
                (
                    artifact_path,
                    completed_at,
                    expires_at,
                    completed_at,
                    job_id,
                    owner,
                    completed_at,
                ),
            )
            return bool(cursor.rowcount and cursor.rowcount > 0)

    def fail(
        self,
        job_id: UUID,
        owner: str,
        safe_error_code: str,
        now: datetime,
    ) -> bool:
        self._job_id(job_id)
        self._owner(owner)
        if not safe_error_code:
            raise ValidationError("Invalid export failure code.")
        with self._connect() as cursor:
            cursor.execute(
                """
                UPDATE export_jobs
                SET status = 'failed', safe_error_code = %s,
                    completed_at = %s, updated_at = %s,
                    owner_token = NULL, leased_until = NULL
                WHERE id = %s AND status = 'running' AND owner_token = %s
                  AND leased_until > %s
                """,
                (safe_error_code, now, now, job_id, owner, now),
            )
            return bool(cursor.rowcount and cursor.rowcount > 0)

    def expire_completed(self, now: datetime) -> tuple[str, ...]:
        with self._connect() as cursor:
            cursor.execute(
                """
                WITH newly_expired AS (
                    UPDATE export_jobs
                    SET status = 'expired', updated_at = %s
                    WHERE status = 'completed' AND expires_at <= %s
                    RETURNING id
                )
                SELECT artifact_path
                FROM export_jobs
                WHERE status = 'expired' AND artifact_path IS NOT NULL
                """,
                (now, now),
            )
            rows = cursor.fetchall()
        return tuple(str(row[0]) for row in rows if row[0])

    def clear_artifact(self, artifact_path: str, now: datetime) -> bool:
        if not isinstance(artifact_path, str) or not artifact_path:
            raise ValidationError("Invalid export artifact path.")
        with self._connect() as cursor:
            cursor.execute(
                """
                UPDATE export_jobs
                SET artifact_path = NULL, updated_at = %s
                WHERE status = 'expired' AND artifact_path = %s
                """,
                (now, artifact_path),
            )
            return bool(cursor.rowcount and cursor.rowcount > 0)

    @staticmethod
    def _job(row: tuple[object, ...]) -> ExportJob:
        snapshot = row[3] if isinstance(row[3], Mapping) else {}
        return ExportJob(
            id=row[0],  # type: ignore[arg-type]
            account_id=int(row[1]),
            format=ExportFormat(str(row[2])),
            filter_snapshot=dict(snapshot),
            status=ExportStatus(str(row[4])),
            owner_token=str(row[5] or ""),
            leased_until=row[6],  # type: ignore[arg-type]
            attempt_count=int(row[7]),
            safe_error_code=str(row[8] or ""),
            artifact_path=str(row[9] or ""),
            expires_at=row[10],  # type: ignore[arg-type]
            created_at=row[11],  # type: ignore[arg-type]
            updated_at=row[12],  # type: ignore[arg-type]
            completed_at=row[13],  # type: ignore[arg-type]
        )

    @staticmethod
    def _account_id(value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValidationError("Invalid export account.")

    @staticmethod
    def _job_id(value: UUID) -> None:
        if not isinstance(value, UUID):
            raise ValidationError("Invalid export identifier.")

    @staticmethod
    def _owner(value: str) -> None:
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValidationError("Invalid export worker owner.")


def _returning(alias: str) -> str:
    return ", ".join(
        f"{alias}.{column.strip()}"
        for column in _COLUMNS.split(",")
        if column.strip()
    )
