from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from fb_crawl.core.jobs import (
    AccountStatus, CrawlJob, CrawlerAccountState, JobConflict, JobCreateCommand, JobNotFound,
    JobStatus, SafeJobOptions, SafetyCode, canonical_job_target,
)
from fb_crawl.core.models import AuthenticatedAction
from fb_crawl.services.jobs import JobService


class RecordingRepository:
    def __init__(self) -> None:
        now = datetime(2026, 8, 21, tzinfo=UTC)
        self.account = CrawlerAccountState("default", AccountStatus.READY, now, now)
        self.calls: list[tuple[object, ...]] = []

    def get_account(self, account_key: str):
        assert account_key == "default"
        return self.account

    def refresh_account(self, account_key: str, *, now: datetime):
        return self.get_account(account_key)

    def acknowledge_account(self, account_key: str, *, now: datetime):
        status = AccountStatus.COOLDOWN if self.account.cooldown_until and self.account.cooldown_until > now else AccountStatus.READY
        self.account = CrawlerAccountState(account_key, status, now, now, cooldown_until=self.account.cooldown_until if status is AccountStatus.COOLDOWN else None, acknowledged_at=now)
        return self.account

    def apply_account_signal(self, account_key: str, signal: str, *, now: datetime):
        count = self.account.rate_limit_count_24h + 1 if signal == "facebook_rate_limited" else self.account.rate_limit_count_24h
        status = AccountStatus.BLOCKED if signal == SafetyCode.SESSION_EXPIRED.value else (AccountStatus.MANUAL_REVIEW if signal != "facebook_rate_limited" or count >= 2 else AccountStatus.COOLDOWN)
        self.account = CrawlerAccountState(account_key, status, now, now, cooldown_until=now + timedelta(hours=6) if status is AccountStatus.COOLDOWN else self.account.cooldown_until, rate_limit_count_24h=count)
        return self.account

    def set_account_state(self, *, account_key: str, status: AccountStatus, now: datetime, **values: object):
        self.calls.append(("state", status, values))
        self.account = CrawlerAccountState(account_key, status, now, now, **values)
        return self.account

    def create_job(self, command: JobCreateCommand, *, idempotency_key: str, request_fingerprint: str):
        self.calls.append(("create", command, idempotency_key, request_fingerprint))
        now = datetime(2026, 8, 21, tzinfo=UTC)
        return CrawlJob(
            uuid4(), command.action, JobStatus.QUEUED, command.options,
            request_fingerprint, now, now,
        ), True

    def get_job(self, job_id):
        now = datetime(2026, 8, 21, tzinfo=UTC)
        return CrawlJob(
            job_id, AuthenticatedAction.MEMBERS, JobStatus.FAILED, SafeJobOptions(),
            "retry-parent", now, now,
        )

    def request_cancel(self, job_id):
        return None


def test_acknowledgement_requires_true_and_preserves_an_unexpired_cooldown() -> None:
    """Break caught: a false acknowledgement clears operational account safeguards."""
    repository = RecordingRepository()
    repository.account = CrawlerAccountState(
        "default", AccountStatus.MANUAL_REVIEW, datetime(2026, 8, 21, tzinfo=UTC), datetime(2026, 8, 21, tzinfo=UTC),
        cooldown_until=datetime(2026, 8, 21, 6, tzinfo=UTC),
    )
    service = JobService(repository, now=lambda: datetime(2026, 8, 21, 1, tzinfo=UTC))

    with pytest.raises(JobConflict):
        service.acknowledge_account(acknowledged=False)
    acknowledged = service.acknowledge_account(acknowledged=True)

    assert acknowledged.status is AccountStatus.COOLDOWN
    assert acknowledged.acknowledged_at == datetime(2026, 8, 21, 1, tzinfo=UTC)


def test_account_safety_policy_escalates_repeat_rate_limits_and_blocks_session_expiry() -> None:
    """Break caught: risky account warnings return a worker to ready automatically."""
    repository = RecordingRepository()
    service = JobService(repository, now=lambda: datetime(2026, 8, 21, tzinfo=UTC))

    first = service.record_account_signal("default", "facebook_rate_limited")
    second = service.record_account_signal("default", "facebook_rate_limited")
    blocked = service.record_account_signal("default", SafetyCode.SESSION_EXPIRED)

    assert first.status is AccountStatus.COOLDOWN
    assert first.rate_limit_count_24h == 1
    assert first.cooldown_until >= datetime(2026, 8, 21, 6, tzinfo=UTC)
    assert second.status is AccountStatus.MANUAL_REVIEW
    assert blocked.status is AccountStatus.BLOCKED


def test_create_uses_canonical_fingerprint_and_retry_rejects_non_ready_account() -> None:
    """Break caught: service creation bypasses canonical idempotency or retries during account hold."""
    repository = RecordingRepository()
    service = JobService(repository)
    command = JobCreateCommand(
        AuthenticatedAction.MEMBERS,
        (canonical_job_target(AuthenticatedAction.MEMBERS, "https://facebook.com/groups/100"),),
        SafeJobOptions(),
    )

    created, was_created = service.create(command, idempotency_key="service-create")
    assert was_created is True
    assert created.status is JobStatus.QUEUED
    assert repository.calls[-1][0] == "create"
    repository.account = CrawlerAccountState("default", AccountStatus.COOLDOWN, datetime.now(UTC), datetime.now(UTC))
    with pytest.raises(JobConflict):
        service.retry(uuid4())


def test_service_maps_missing_jobs_and_rejects_nonterminal_retry() -> None:
    """Break caught: transport callers receive generic errors for missing or active jobs."""
    repository = RecordingRepository()
    service = JobService(repository)
    with pytest.raises(JobNotFound):
        service.cancel(uuid4())
    repository.get_job = lambda _job_id: None  # type: ignore[method-assign]
    with pytest.raises(JobNotFound):
        service.retry(uuid4())
    now = datetime(2026, 8, 21, tzinfo=UTC)
    repository.get_job = lambda job_id: CrawlJob(  # type: ignore[method-assign]
        job_id, AuthenticatedAction.MEMBERS, JobStatus.RUNNING, SafeJobOptions(), "active", now, now,
    )
    with pytest.raises(JobConflict):
        service.retry(uuid4())


@pytest.mark.parametrize(
    "signal",
    [SafetyCode.CHECKPOINT, SafetyCode.CAPTCHA, SafetyCode.ACCOUNT_RESTRICTED, SafetyCode.UNUSUAL_ACTIVITY, SafetyCode.ACCOUNT_RECOVERY],
)
def test_account_warning_codes_require_manual_review(signal: SafetyCode) -> None:
    """Break caught: a safety warning leaves the account available to a worker."""
    repository = RecordingRepository()
    assert JobService(repository).record_account_signal(signal=signal).status is AccountStatus.MANUAL_REVIEW


def test_service_maps_missing_account_for_every_account_operation() -> None:
    """Break caught: account routes leak an attribute/driver error when the default row is absent."""
    class MissingAccountRepository:
        def refresh_account(self, _account_key: str, *, now: datetime):
            return None

        def acknowledge_account(self, _account_key: str, *, now: datetime):
            return None

        def apply_account_signal(self, _account_key: str, _signal: str, *, now: datetime):
            return None

    service = JobService(MissingAccountRepository())
    with pytest.raises(JobNotFound):
        service.get_account()
    with pytest.raises(JobNotFound):
        service.acknowledge_account(acknowledged=True)
    with pytest.raises(JobNotFound):
        service.record_account_signal(signal=SafetyCode.CHECKPOINT)
