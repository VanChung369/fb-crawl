"""Safe, single-job authenticated crawl worker orchestration.

The worker deliberately owns no browser, provider, or database transaction.
Those resources are supplied at narrow boundaries so an account-safety stop can
close the runtime before durable terminal state is attempted.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from fb_crawl.adapters.browser.account_safety import SafetySignal, classify_account_safety
from fb_crawl.core.exceptions import BrowserNavigationError, FbCrawlError, RateLimitError, SessionError
from fb_crawl.core.jobs import CrawlJob, CrawlTarget, JobStatus, SafetyCode, TargetStatus
from fb_crawl.core.models import ScrapeRequest, ScrapeResult, UserRecord
from fb_crawl.services.execution_control import (
    AccountSafetyStop,
    CrawlCancelled,
    ExecutionControl,
    JobBudgetReached,
    NavigationPacer,
    SafeNavigationPacer,
)
from fb_data_pipeline.repositories.errors import DatabaseError

if TYPE_CHECKING:
    from fb_crawl.composition.authenticated import AuthenticatedJobSession
    from fb_data_pipeline.repositories.jobs import JobRepository
    from fb_data_pipeline.services.ingestion import IngestionReport


_LOG = logging.getLogger(__name__)
_FACEBOOK_RATE_LIMIT = "facebook_rate_limited"


class LeaseLost(RuntimeError):
    """The database no longer confirms this worker owns the job lease."""

    code = "lease_lost"

    def __init__(self) -> None:
        super().__init__("Crawl job lease was lost.")


@dataclass(frozen=True, slots=True)
class WorkerPolicy:
    """Mandatory worker safety bounds; stricter deployment values are allowed."""

    lease_seconds: int = 60
    heartbeat_seconds: int = 10
    navigation_interval_seconds: int = 8
    job_timeout_seconds: int = 1800
    normal_cooldown_seconds: int = 3600
    rate_limit_cooldown_seconds: int = 21600

    def __post_init__(self) -> None:
        values = (
            self.lease_seconds,
            self.heartbeat_seconds,
            self.navigation_interval_seconds,
            self.job_timeout_seconds,
            self.normal_cooldown_seconds,
            self.rate_limit_cooldown_seconds,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
            raise ValueError("Worker policy durations must be positive integers.")
        if self.lease_seconds > 60:
            raise ValueError("Worker leases may not exceed sixty seconds.")
        if self.heartbeat_seconds > 10 or self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("Worker heartbeat must be shorter than the lease and at most ten seconds.")
        if self.navigation_interval_seconds < 8:
            raise ValueError("Navigation pacing must be at least eight seconds.")
        if self.job_timeout_seconds > 1800:
            raise ValueError("Worker timeout may not exceed thirty minutes.")
        if self.normal_cooldown_seconds < 3600:
            raise ValueError("Normal cooldown must be at least sixty minutes.")
        if self.rate_limit_cooldown_seconds < 21600:
            raise ValueError("Rate-limit cooldown must be at least six hours.")


class _WaitEvent(Protocol):
    def set(self) -> None: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class LeaseHeartbeat:
    """Renew a claimed lease on a dedicated daemon without blocking crawl work."""

    def __init__(
        self,
        repository: JobRepository,
        job_id: object,
        worker_id: str,
        *,
        lease_duration: timedelta,
        heartbeat_seconds: int = 10,
        stop_event: _WaitEvent | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        if heartbeat_seconds <= 0 or lease_duration <= timedelta():
            raise ValueError("Lease heartbeat requires positive durations.")
        self._repository = repository
        self._job_id = job_id
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._heartbeat_seconds = heartbeat_seconds
        self._stop_event = stop_event or threading.Event()
        self._monotonic = monotonic
        self._thread_factory = thread_factory
        self._thread: threading.Thread | None = None
        self._lost = False
        self._state_lock = threading.Lock()

    @property
    def lost(self) -> bool:
        with self._state_lock:
            return self._lost

    def _mark_lost(self) -> None:
        with self._state_lock:
            self._lost = True

    def __enter__(self) -> "LeaseHeartbeat":
        self._thread = self._thread_factory(target=self._run, daemon=True, name="crawl-job-heartbeat")
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
        return False

    def _run(self) -> None:
        next_heartbeat = self._monotonic() + self._heartbeat_seconds
        while True:
            delay = max(0.0, next_heartbeat - self._monotonic())
            if self._stop_event.wait(delay):
                return
            try:
                renewed = self._repository.heartbeat(
                    self._job_id,
                    self._worker_id,
                    lease_duration=self._lease_duration,
                )
            except Exception:
                self._mark_lost()
                return
            if renewed is not True:
                self._mark_lost()
                return
            next_heartbeat += self._heartbeat_seconds


class JobExecutionControl:
    """Repository-backed cancellation, event, safety, budget, and lease control."""

    def __init__(
        self,
        repository: JobRepository,
        job: CrawlJob,
        heartbeat: LeaseHeartbeat | object,
        *,
        timeout_seconds: int = 1800,
        monotonic: Callable[[], float] = time.monotonic,
        deadline_monotonic: float | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Job timeout must be positive.")
        self._repository = repository
        self._job = job
        self._heartbeat = heartbeat
        self._monotonic = monotonic
        self._deadline = (
            deadline_monotonic if deadline_monotonic is not None else monotonic() + timeout_seconds
        )
        self._now = now or (lambda: datetime.now(UTC))

    def is_cancel_requested(self) -> bool:
        self._raise_if_lease_lost_or_deadline()
        return self._read_cancel_requested()

    def _read_cancel_requested(self) -> bool:
        current = self._repository.get_job(self._job.id)
        if current is None:
            return True
        return current.status is JobStatus.CANCELLING or current.cancel_requested_at is not None

    def _raise_if_lease_lost_or_deadline(self) -> None:
        if bool(getattr(self._heartbeat, "lost", False)):
            raise LeaseLost()
        if self._monotonic() >= self._deadline:
            raise JobBudgetReached()

    def emit(
        self,
        event_type: str,
        *,
        counters: Mapping[str, int] | None = None,
        safe_message: str = "",
    ) -> None:
        self._repository.append_event(
            self._job.id,
            event_type,
            "info",
            safe_message=safe_message,
            counters=counters,
        )

    def check_account_safety(self, browser: object) -> SafetySignal | None:
        return classify_account_safety(browser)

    def guard_deadline_and_lease(self) -> None:
        self._raise_if_lease_lost_or_deadline()
        if self._read_cancel_requested():
            raise CrawlCancelled()

    def guard_before_persistence(self) -> None:
        self.guard_deadline_and_lease()


def request_for_target(
    job: CrawlJob,
    target: CrawlTarget,
    policy: WorkerPolicy = WorkerPolicy(),
) -> ScrapeRequest:
    """Create a resumable, server-checkpointed one-target request.

    The job contract may make collection stricter, but not weaker than the
    worker's immutable timeout and navigation boundaries.
    """
    request = job.request_options.to_scrape_request(
        job.action,
        (target.target_url,),
        checkpoint_path=target.checkpoint_path,
    )
    duration = min(float(request.max_duration_seconds or policy.job_timeout_seconds), policy.job_timeout_seconds)
    delay = max(float(request.delay_seconds), float(policy.navigation_interval_seconds))
    return replace(
        request,
        max_duration_seconds=duration,
        delay_seconds=delay,
        profile_delay_seconds=max(float(request.profile_delay_seconds), delay),
        resume=True,
        checkpoint_path=target.checkpoint_path,
    )


def promote_facebook_safety_issues(result: ScrapeResult[UserRecord]) -> None:
    """Convert a normal-looking result carrying Facebook rate-limit evidence to a stop."""
    if any(issue.code == RateLimitError.code for issue in result.issues):
        signal = SafetySignal(
            SafetyCode.TEMPORARY_BLOCK,
            "Facebook temporarily blocked requests.",
            False,
        )
        raise AccountSafetyStop(signal)


@dataclass(frozen=True, slots=True)
class _TerminalState:
    job_status: JobStatus
    target_status: TargetStatus
    error_code: str
    account_signal: str | None = None


def _safe_error_code(error: BaseException) -> str:
    if isinstance(error, KeyboardInterrupt):
        return CrawlCancelled.code
    if isinstance(error, FbCrawlError):
        return error.code
    return "worker_error"


def _terminal_for(error: BaseException) -> _TerminalState:
    if isinstance(error, (CrawlCancelled, KeyboardInterrupt)):
        return _TerminalState(JobStatus.CANCELLED, TargetStatus.CANCELLED, CrawlCancelled.code)
    if isinstance(error, LeaseLost):
        raise error
    if isinstance(error, AccountSafetyStop):
        signal = _FACEBOOK_RATE_LIMIT if error.signal.code is SafetyCode.TEMPORARY_BLOCK else error.signal.code.value
        return _TerminalState(JobStatus.BLOCKED, TargetStatus.BLOCKED, signal, signal)
    if isinstance(error, SessionError):
        return _TerminalState(JobStatus.BLOCKED, TargetStatus.BLOCKED, "session_expired", "session_expired")
    if isinstance(error, RateLimitError):
        return _TerminalState(JobStatus.BLOCKED, TargetStatus.BLOCKED, _FACEBOOK_RATE_LIMIT, _FACEBOOK_RATE_LIMIT)
    if isinstance(error, DatabaseError):
        return _TerminalState(JobStatus.FAILED, TargetStatus.FAILED, error.code)
    if isinstance(error, JobBudgetReached):
        return _TerminalState(JobStatus.PARTIAL, TargetStatus.PARTIAL, error.code)
    if isinstance(error, BrowserNavigationError):
        return _TerminalState(JobStatus.FAILED, TargetStatus.FAILED, error.code)
    return _TerminalState(JobStatus.FAILED, TargetStatus.FAILED, _safe_error_code(error))


def _status_for_result(result: ScrapeResult[UserRecord], report: IngestionReport) -> TargetStatus:
    if report.has_database_failures:
        return TargetStatus.FAILED
    if report.has_provider_retries:
        return TargetStatus.PARTIAL
    if result.issues or result.stats.failed:
        return TargetStatus.PARTIAL if result.records else TargetStatus.FAILED
    return TargetStatus.SUCCEEDED


def _job_status(target_statuses: list[TargetStatus]) -> JobStatus:
    if not target_statuses or all(status is TargetStatus.SUCCEEDED for status in target_statuses):
        return JobStatus.SUCCEEDED
    if any(status is TargetStatus.SUCCEEDED for status in target_statuses):
        return JobStatus.PARTIAL
    if any(status is TargetStatus.PARTIAL for status in target_statuses):
        return JobStatus.PARTIAL
    return JobStatus.FAILED


def _reclassify_rejected_target_write(control: JobExecutionControl) -> None:
    """Read cooperative stop state before calling an ownership rejection a lease loss.

    A false target write can be caused by a concurrent request_cancel,
    deadline, or heartbeat loss after the worker's preceding guard. The
    repository has deliberately made no write in that case, so immediately
    reread the control state and preserve its typed stop if one exists.
    """
    control.guard_deadline_and_lease()
    raise LeaseLost()


RuntimeFactory = Callable[[ExecutionControl, NavigationPacer], AbstractContextManager["AuthenticatedJobSession"]]


class CrawlWorker:
    def __init__(
        self,
        repository: JobRepository,
        runtime_factory: RuntimeFactory,
        *,
        worker_id: str,
        policy: WorkerPolicy = WorkerPolicy(),
        monotonic: Callable[[], float] = time.monotonic,
        heartbeat_factory: Callable[..., LeaseHeartbeat] = LeaseHeartbeat,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("Worker identifier is required.")
        if not isinstance(policy, WorkerPolicy):
            raise TypeError("Worker policy must be a WorkerPolicy.")
        self._repository = repository
        self._runtime_factory = runtime_factory
        self._worker_id = worker_id.strip()
        self._policy = policy
        self._monotonic = monotonic
        self._heartbeat_factory = heartbeat_factory
        self._now = now or (lambda: datetime.now(UTC))

    def _persist_terminal(
        self,
        job: CrawlJob,
        active_target: CrawlTarget | None,
        terminal: _TerminalState,
    ) -> None:
        try:
            if self._repository.finish_job(
                job.id,
                self._worker_id,
                status=terminal.job_status,
                error_code=terminal.error_code,
                error_message="",
                active_target_id=None if active_target is None else active_target.id,
                active_target_status=(
                    None if active_target is None else terminal.target_status
                ),
                account_signal=terminal.account_signal,
                normal_cooldown=timedelta(seconds=self._policy.normal_cooldown_seconds),
                rate_limit_cooldown=timedelta(seconds=self._policy.rate_limit_cooldown_seconds),
                now=self._now(),
            ) is not True:
                raise LeaseLost()
        except Exception:
            # Do not stringify a database/provider exception: it may carry credentials.
            _LOG.error("Unable to persist terminal crawl job state; stale recovery will resolve it.")

    def run_once(self) -> bool:
        claim = self._repository.claim_next(
            self._worker_id,
            lease_duration=timedelta(seconds=self._policy.lease_seconds),
        )
        if claim is None:
            return False

        job = claim.job
        active_target: CrawlTarget | None = None
        terminal: _TerminalState | None = None
        completed: list[TargetStatus] = []
        lease_lost = False
        heartbeat = self._heartbeat_factory(
            self._repository,
            job.id,
            self._worker_id,
            lease_duration=timedelta(seconds=self._policy.lease_seconds),
            heartbeat_seconds=self._policy.heartbeat_seconds,
        )
        control = JobExecutionControl(
            self._repository,
            job,
            heartbeat,
            timeout_seconds=self._policy.job_timeout_seconds,
            monotonic=self._monotonic,
            now=self._now,
        )

        try:
            with heartbeat:
                try:
                    control.guard_deadline_and_lease()
                    pacer = SafeNavigationPacer(self._policy.navigation_interval_seconds, control)
                    with self._runtime_factory(control, pacer) as session:
                        for target in sorted(claim.targets, key=lambda item: (item.position, item.id)):
                            control.guard_deadline_and_lease()
                            if self._repository.start_target(job.id, target.id, self._worker_id) is not True:
                                _reclassify_rejected_target_write(control)
                            active_target = target
                            request = request_for_target(job, target, self._policy)
                            control.guard_deadline_and_lease()
                            session.validate(request)
                            control.guard_deadline_and_lease()
                            result = session.run(request)
                            promote_facebook_safety_issues(result)
                            control.guard_before_persistence()
                            report = session.ingest(result)
                            control.guard_before_persistence()
                            if self._repository.update_target_progress(
                                job.id,
                                target.id,
                                self._worker_id,
                                steps_completed=result.stats.succeeded,
                                items_discovered=result.stats.discovered,
                                users_persisted=report.persistence.persisted,
                                provider_retries_required=report.persistence.provider_retries_required,
                            ) is not True:
                                _reclassify_rejected_target_write(control)
                            status = _status_for_result(result, report)
                            control.guard_before_persistence()
                            if self._repository.finish_target(
                                job.id,
                                target.id,
                                self._worker_id,
                                status=status,
                                error_code="",
                                error_message="",
                            ) is not True:
                                _reclassify_rejected_target_write(control)
                            completed.append(status)
                            active_target = None
                            control.guard_deadline_and_lease()
                except BaseException as error:
                    if isinstance(error, LeaseLost):
                        lease_lost = True
                    else:
                        terminal = _terminal_for(error)
        except BaseException as error:
            # A context-manager cleanup exception follows the same safe terminal mapping.
            if isinstance(error, LeaseLost):
                lease_lost = True
            elif terminal is None:
                terminal = _terminal_for(error)

        if lease_lost or bool(getattr(heartbeat, "lost", False)):
            _LOG.warning("Crawl job lease was lost; stale recovery will resolve it.")
            return True

        # Cleanup has completed; observe cancellation one final time before the
        # single ownership-checked terminal write.  A safety block remains a
        # block even if a user requested cancellation concurrently.
        try:
            control.guard_deadline_and_lease()
        except LeaseLost:
            _LOG.warning("Crawl job lease was lost; stale recovery will resolve it.")
            return True
        except CrawlCancelled:
            # The atomic finalizer will still make a stored `cancelling` job
            # canonical-cancelled.  Do not discard the primary cause observed
            # before cleanup (notably an interrupt or database failure).
            if terminal is None:
                terminal = _terminal_for(CrawlCancelled())
        except JobBudgetReached:
            # Cleanup can cross the deadline.  It must not rewrite an already
            # observed interrupt, database, cancellation, or safety outcome.
            if terminal is None:
                terminal = _terminal_for(JobBudgetReached())
        except DatabaseError:
            # The final cancellation read is deliberately not followed by a
            # terminal/account write: ownership and cancellation are unknown.
            _LOG.warning("Unable to read final crawl job state; stale recovery will resolve it.")
            return True

        if terminal is None:
            terminal = _TerminalState(_job_status(completed), TargetStatus.FAILED, "")
        elif completed and terminal.job_status is JobStatus.FAILED:
            terminal = replace(terminal, job_status=JobStatus.PARTIAL)
        self._persist_terminal(job, active_target, terminal)
        return True


__all__ = [
    "CrawlWorker",
    "JobExecutionControl",
    "LeaseHeartbeat",
    "LeaseLost",
    "RuntimeFactory",
    "WorkerPolicy",
    "promote_facebook_safety_issues",
    "request_for_target",
]
