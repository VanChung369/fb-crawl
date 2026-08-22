"""Application policy for persisted authenticated crawl jobs.

The repository owns conditional SQL; this layer owns decisions that are stable
across HTTP and worker callers.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fb_crawl.core.jobs import (
    AccountStatus,
    CrawlJob,
    CrawlerAccountState,
    JobConflict,
    JobCreateCommand,
    JobNotFound,
    JobStatus,
    SafetyCode,
    canonical_request_fingerprint,
    is_terminal_job_status,
)


class JobService:
    def __init__(self, repository: Any, *, now: Callable[[], datetime] | None = None) -> None:
        self.repository = repository
        self._now = now or (lambda: datetime.now(UTC))

    def create(self, command: JobCreateCommand, *, idempotency_key: str) -> tuple[CrawlJob, bool]:
        return self.repository.create_job(
            command,
            idempotency_key=idempotency_key,
            request_fingerprint=canonical_request_fingerprint(command),
        )

    def cancel(self, job_id: UUID) -> CrawlJob:
        job = self.repository.request_cancel(job_id)
        if job is None:
            raise JobNotFound("Crawl job was not found.")
        return job

    def retry(self, job_id: UUID) -> CrawlJob:
        job = self.repository.get_job(job_id)
        if job is None:
            raise JobNotFound("Crawl job was not found.")
        if not is_terminal_job_status(job.status):
            raise JobConflict("Only terminal crawl jobs can be retried.")
        account = self.get_account(job.account_key)
        if account.status is not AccountStatus.READY:
            raise JobConflict("Crawler account is not ready for retry.")
        child = self.repository.create_retry_child(job_id)
        if child is None:
            raise JobConflict("This crawl job has no retryable targets.")
        return child

    def get_account(self, account_key: str = "default") -> CrawlerAccountState:
        account = self.repository.refresh_account(account_key, now=self._utc_now())
        if account is None:
            raise JobNotFound("Crawler account was not found.")
        return account

    def acknowledge_account(
        self, *, account_key: str = "default", acknowledged: bool,
    ) -> CrawlerAccountState:
        if acknowledged is not True:
            raise JobConflict("Account acknowledgement must be true.")
        now = self._utc_now()
        account = self.repository.acknowledge_account(account_key, now=now)
        if account is None:
            raise JobNotFound("Crawler account was not found.")
        return account

    def record_account_signal(
        self, account_key: str = "default", signal: str | SafetyCode = "",
    ) -> CrawlerAccountState:
        """Persist a conservative account hold without attempting recovery."""
        now = self._utc_now()
        value = signal.value if isinstance(signal, SafetyCode) else str(signal)
        account = self.repository.apply_account_signal(account_key, value, now=now)
        if account is None:
            raise JobNotFound("Crawler account was not found.")
        return account

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("JobService clock must return a timezone-aware UTC time.")
        return value.astimezone(UTC)
