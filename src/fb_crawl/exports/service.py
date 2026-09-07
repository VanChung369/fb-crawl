from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

from fb_crawl.contacts.models import LookupOutcome
from fb_crawl.core.exceptions import ValidationError
from fb_crawl.exports.models import ExportFormat, ExportJob, ExportStatus
from fb_crawl.history.models import AccountHistoryQuery, HistoryItem
from fb_crawl.history.service import HistoryService


_FILTER_FIELDS = frozenset(
    {
        "outcome",
        "name",
        "uid",
        "username",
        "phone",
        "created_from",
        "created_to",
    }
)


class ExportWorkerUnavailable(ValidationError):
    code = "export_worker_unavailable"


@dataclass(frozen=True, slots=True)
class ExportWorkerHealth:
    available: bool
    last_seen_at: datetime | None


def normalize_filter_snapshot(
    values: Mapping[str, object],
) -> dict[str, str]:
    if not isinstance(values, Mapping) or any(
        key not in _FILTER_FIELDS for key in values
    ):
        raise ValidationError("Invalid export filters.")
    normalized = {
        key: _snapshot_value(key, value)
        for key, value in values.items()
        if value is not None
    }
    query = AccountHistoryQuery(account_id=1, **normalized)
    snapshot: dict[str, str] = {}
    for field in _FILTER_FIELDS:
        value = getattr(query, field)
        if value is None:
            continue
        if isinstance(value, datetime):
            snapshot[field] = value.isoformat().replace("+00:00", "Z")
        elif isinstance(value, LookupOutcome):
            snapshot[field] = value.value
        else:
            snapshot[field] = str(value)
    return snapshot


def _snapshot_value(field: str, value: object) -> object:
    if field not in {"created_from", "created_to"} or not isinstance(value, str):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError("Invalid export time filter.") from error


class HistoryExportSource:
    def __init__(self, history: HistoryService) -> None:
        self.history = history

    def iter_export(
        self,
        account_id: int,
        snapshot: Mapping[str, object],
    ) -> Iterator[HistoryItem]:
        filters = normalize_filter_snapshot(snapshot)
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = self.history.list(
                AccountHistoryQuery(
                    account_id=account_id,
                    cursor=cursor,
                    limit=100,
                    **{
                        key: _snapshot_value(key, value)
                        for key, value in filters.items()
                    },
                )
            )
            yield from page.items
            if page.next_cursor is None:
                return
            if page.next_cursor in seen_cursors:
                raise ValidationError("Invalid repeated history cursor.")
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor


class ExportRepository(Protocol):
    def worker_is_recent(
        self,
        now: datetime,
        max_age: timedelta,
    ) -> bool: ...

    def worker_last_seen(self) -> datetime | None: ...

    def create(
        self,
        account_id: int,
        format_name: ExportFormat,
        filter_snapshot: Mapping[str, object],
        now: datetime,
    ) -> ExportJob: ...

    def get(self, account_id: int, job_id) -> ExportJob | None: ...

    def delete(self, account_id: int, job_id) -> tuple[bool, str]: ...

    def expire_completed(self, now: datetime) -> tuple[str, ...]: ...

    def clear_artifact(self, artifact_path: str, now: datetime) -> bool: ...


class ExportService:
    def __init__(
        self,
        repository: ExportRepository,
        artifacts,
        *,
        worker_max_age: timedelta = timedelta(seconds=15),
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.worker_max_age = worker_max_age

    def create(
        self,
        account_id: int,
        format_name: ExportFormat | str,
        filters: Mapping[str, object],
        now: datetime,
    ) -> ExportJob:
        try:
            resolved_format = ExportFormat(format_name)
        except (TypeError, ValueError) as error:
            raise ValidationError("Invalid export format.") from error
        normalized_filters = normalize_filter_snapshot(filters)
        if not self.repository.worker_is_recent(now, self.worker_max_age):
            raise ExportWorkerUnavailable("Export worker is unavailable.")
        return self.repository.create(
            account_id,
            resolved_format,
            normalized_filters,
            now,
        )

    def get(self, account_id: int, job_id, now: datetime) -> ExportJob | None:
        self._expire(now)
        return self.repository.get(account_id, job_id)

    def worker_health(self, now: datetime) -> ExportWorkerHealth:
        return ExportWorkerHealth(
            available=self.repository.worker_is_recent(
                now,
                self.worker_max_age,
            ),
            last_seen_at=self.repository.worker_last_seen(),
        )

    def artifact(self, job: ExportJob) -> Path | None:
        if job.status is not ExportStatus.COMPLETED or not job.artifact_path:
            return None
        path = self.artifacts.resolve(job.artifact_path)
        return path if path.is_file() else None

    def delete(self, account_id: int, job_id) -> bool:
        job = self.repository.get(account_id, job_id)
        if job is None:
            return False
        removed_path = ""
        if job.artifact_path:
            self.artifacts.delete(job.artifact_path)
            removed_path = job.artifact_path
        deleted, current_path = self.repository.delete(account_id, job_id)
        if deleted and current_path and current_path != removed_path:
            self.artifacts.delete(current_path)
        return deleted

    def _expire(self, now: datetime) -> None:
        for path in self.repository.expire_completed(now):
            try:
                self.artifacts.delete(path)
            except Exception:
                continue
            self.repository.clear_artifact(path, now)


class ExportWorkerRepository(Protocol):
    def touch_worker(self, worker_id: str, now: datetime) -> None: ...

    def expire_completed(self, now: datetime) -> tuple[str, ...]: ...

    def clear_artifact(self, artifact_path: str, now: datetime) -> bool: ...

    def claim_next(self, owner: str, now: datetime) -> ExportJob | None: ...

    def complete(
        self,
        job_id,
        owner: str,
        artifact_path: str,
        completed_at: datetime,
        expires_at: datetime,
    ) -> bool: ...

    def fail(
        self,
        job_id,
        owner: str,
        safe_error_code: str,
        now: datetime,
    ) -> bool: ...


class ExportHistorySource(Protocol):
    def iter_export(self, account_id: int, snapshot): ...


class ExportArtifactWriter(Protocol):
    def write_history(self, job_id, format_name, rows) -> str: ...

    def delete(self, path: str) -> bool: ...

    def purge_older_than(self, before: datetime) -> tuple[str, ...]: ...


class ExportWorker:
    def __init__(
        self,
        repository: ExportWorkerRepository,
        history: ExportHistorySource,
        artifacts: ExportArtifactWriter,
        *,
        worker_id: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        artifact_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        self.repository = repository
        self.history = history
        self.artifacts = artifacts
        self.worker_id = worker_id
        self.clock = clock
        self.artifact_ttl = artifact_ttl

    def run_once(self) -> bool:
        now = self.clock()
        self.repository.touch_worker(self.worker_id, now)
        for path in self.repository.expire_completed(now):
            try:
                self.artifacts.delete(path)
            except Exception:
                continue
            self.repository.clear_artifact(path, now)
        self.artifacts.purge_older_than(now - self.artifact_ttl)
        job = self.repository.claim_next(self.worker_id, now)
        if job is None:
            return False
        artifact_path = ""
        try:
            rows = self.history.iter_export(job.account_id, job.filter_snapshot)
            artifact_path = self.artifacts.write_history(
                job.id, job.format, rows
            )
            completed_at = self.clock()
            completed = self.repository.complete(
                job.id,
                self.worker_id,
                artifact_path,
                completed_at,
                completed_at + self.artifact_ttl,
            )
            if not completed:
                self.artifacts.delete(artifact_path)
        except Exception:
            if artifact_path:
                self.artifacts.delete(artifact_path)
            self.repository.fail(
                job.id,
                self.worker_id,
                "export_generation_failed",
                self.clock(),
            )
        return True
