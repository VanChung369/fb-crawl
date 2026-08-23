from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from fb_crawl.core.exceptions import BrowserNavigationError, RateLimitError, SessionError
from fb_crawl.core.jobs import CrawlJob, CrawlTarget, JobStatus, SafeJobOptions, TargetStatus
from fb_crawl.core.models import (
    AuthenticatedAction,
    ScrapeIssue,
    ScrapeMode,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_crawl.adapters.browser.account_safety import SafetyCode, SafetySignal
from fb_crawl.services.execution_control import (
    AccountSafetyStop,
    CrawlCancelled,
    JobBudgetReached,
    guard_execution,
)
from fb_crawl.services.worker import (
    CrawlWorker,
    JobExecutionControl,
    LeaseHeartbeat,
    LeaseLost,
    WorkerPolicy,
    promote_facebook_safety_issues,
    request_for_target,
)
from fb_data_pipeline.repositories.errors import DatabaseError
from fb_data_pipeline.repositories.jobs import ClaimedJob
from fb_data_pipeline.services.ingestion import IngestionReport
from fb_data_pipeline.services.persistence import PersistenceReport
from fb_data_pipeline.services.pipeline import PipelineReport


NOW = datetime(2026, 8, 21, tzinfo=UTC)


def _job() -> CrawlJob:
    return CrawlJob(
        id=uuid4(), action=AuthenticatedAction.MEMBERS, status=JobStatus.RUNNING,
        request_options=SafeJobOptions(), request_fingerprint="fingerprint",
        created_at=NOW, updated_at=NOW, requested_targets=2,
    )


def _target(job: CrawlJob, position: int, *, checkpoint_path: str) -> CrawlTarget:
    return CrawlTarget(
        id=uuid4(), job_id=job.id, target_key=f"members:{position}",
        target_url=f"https://www.facebook.com/groups/{position}/members",
        target_kind="group_members", position=position, status=TargetStatus.PENDING,
        checkpoint_path=checkpoint_path, created_at=NOW, updated_at=NOW,
    )


def _result(*, records: int = 1, issues: tuple[ScrapeIssue, ...] = ()) -> ScrapeResult[UserRecord]:
    return ScrapeResult(
        records=tuple(
            UserRecord(str(index), "Person", f"https://www.facebook.com/{index}", "test", "test")
            for index in range(records)
        ),
        issues=issues,
        stats=ScrapeStats(requested=1, discovered=records, succeeded=1, failed=0),
    )


def _report(*, persisted: int = 1, retries: int = 0, failures: int = 0) -> IngestionReport:
    return IngestionReport(
        PipelineReport(persisted, persisted, 0, 0, 0, 0, 0, 0, 0),
        PersistenceReport(persisted, persisted, retries, tuple(range(persisted)), tuple(
            object() for _ in range(failures)  # type: ignore[arg-type]
        )),
    )


@dataclass
class Repository:
    claim: ClaimedJob | None
    events: list[tuple] = None  # type: ignore[assignment]
    current_job: CrawlJob | None = None
    heartbeat_result: bool = True

    def __post_init__(self) -> None:
        self.events = []
        self.current_job = self.claim.job if self.claim else None

    def claim_next(self, worker_id: str, *, lease_duration: timedelta):
        self.events.append(("claim", worker_id, lease_duration))
        return self.claim

    def get_job(self, job_id):
        assert self.current_job is not None and job_id == self.current_job.id
        return self.current_job

    def heartbeat(self, job_id, worker_id, *, lease_duration: timedelta) -> bool:
        self.events.append(("heartbeat", job_id, worker_id, lease_duration))
        return self.heartbeat_result

    def start_target(self, job_id, target_id, worker_id) -> bool:
        self.events.append(("start", target_id))
        return True

    def update_target_progress(self, job_id, target_id, worker_id, **counters) -> bool:
        self.events.append(("progress", target_id, counters))
        return True

    def finish_target(self, job_id, target_id, worker_id, **kwargs) -> bool:
        self.events.append(("finish_target", target_id, kwargs))
        return True

    def finish_job(self, job_id, worker_id, **kwargs) -> bool:
        self.events.append(("finish_job", job_id, kwargs))
        return True

    def apply_account_signal(self, account_key, signal, *, now):
        self.events.append(("account", account_key, signal))
        return object()

    def append_event(self, job_id, event_type, level, **kwargs):
        self.events.append(("event", event_type, kwargs))


class Session:
    def __init__(self, results: list[ScrapeResult[UserRecord] | BaseException], report: IngestionReport) -> None:
        self.results = iter(results)
        self.report = report
        self.events: list[tuple] = []

    def validate(self, request) -> None:
        self.events.append(("validate", request))

    def run(self, request):
        self.events.append(("run", request))
        value = next(self.results)
        if isinstance(value, BaseException):
            raise value
        return value

    def ingest(self, result):
        self.events.append(("ingest", result))
        return self.report


def _runtime(session: Session, events: list[str]):
    @contextmanager
    def factory(control, pacer):
        events.append("open")
        try:
            yield session
        finally:
            events.append("close")
    return factory


def _finalization(repository: Repository) -> dict:
    event = next(event for event in repository.events if event[0] == "finish_job")
    return event[2]


def test_run_once_returns_false_without_opening_a_runtime() -> None:
    """Break caught: an empty worker poll starts a Facebook browser."""
    repository = Repository(None)
    opened: list[str] = []

    assert CrawlWorker(repository, lambda *_: pytest.fail("runtime opened"), worker_id="worker").run_once() is False
    assert repository.events == [("claim", "worker", timedelta(seconds=60))]
    assert opened == []


def test_worker_processes_ordered_one_target_requests_and_persists_before_next_target() -> None:
    """Break caught: target two begins before target one's collected data is durable."""
    job = _job()
    second = _target(job, 2, checkpoint_path="checkpoint-two.json")
    first = _target(job, 1, checkpoint_path="checkpoint-one.json")
    repository = Repository(ClaimedJob(job, (second, first)))
    session = Session([_result(), _result()], _report())
    runtime_events: list[str] = []

    assert CrawlWorker(repository, _runtime(session, runtime_events), worker_id="worker").run_once() is True

    requests = [event[1] for event in session.events if event[0] == "validate"]
    assert [request.targets for request in requests] == [(first.target_url,), (second.target_url,)]
    assert [request.checkpoint_path for request in requests] == ["checkpoint-one.json", "checkpoint-two.json"]
    assert all(request.resume is True for request in requests)
    names = [event[0] for event in repository.events]
    assert names.index("finish_target") < names.index("start", names.index("start") + 1)
    assert runtime_events == ["open", "close"]
    final = _finalization(repository)
    assert final["status"] is JobStatus.SUCCEEDED
    assert final["active_target_id"] is None
    assert final["normal_cooldown"] == timedelta(hours=1)
    assert final["rate_limit_cooldown"] == timedelta(hours=6)


def test_shared_ledger_proves_ingestion_precedes_next_target_and_cleanup_precedes_finalizer() -> None:
    """Break caught: separate mocks can hide a cross-layer ordering violation."""
    job = _job()
    first = _target(job, 0, checkpoint_path="runtime/checkpoints/jobs/fresh/one.json")
    # A retry child retains the server-owned parent checkpoint rather than
    # receiving a newly generated path.  The worker must pass it through.
    copied_retry = _target(job, 1, checkpoint_path="runtime/checkpoints/jobs/parent/two.json")
    names = {first.id: "fresh", copied_retry.id: "copied-retry"}
    ledger: list[str] = []

    class LedgerRepository(Repository):
        def claim_next(self, *args, **kwargs):
            ledger.append("claim")
            return super().claim_next(*args, **kwargs)

        def start_target(self, job_id, target_id, worker_id):
            ledger.append(f"start:{names[target_id]}")
            return super().start_target(job_id, target_id, worker_id)

        def update_target_progress(self, job_id, target_id, worker_id, **counters):
            ledger.append(f"progress:{names[target_id]}")
            return super().update_target_progress(job_id, target_id, worker_id, **counters)

        def finish_target(self, job_id, target_id, worker_id, **kwargs):
            ledger.append(f"finish-target:{names[target_id]}")
            return super().finish_target(job_id, target_id, worker_id, **kwargs)

        def finish_job(self, job_id, worker_id, **kwargs):
            ledger.append("atomic-finalize")
            return super().finish_job(job_id, worker_id, **kwargs)

    class LedgerSession(Session):
        def validate(self, request) -> None:
            ledger.append(f"validate:{'fresh' if request.checkpoint_path == first.checkpoint_path else 'copied-retry'}")
            super().validate(request)

        def run(self, request):
            ledger.append(f"run:{'fresh' if request.checkpoint_path == first.checkpoint_path else 'copied-retry'}")
            return super().run(request)

        def ingest(self, result):
            ledger.append("ingest:fresh" if len([item for item in ledger if item.startswith("ingest:")]) == 0 else "ingest:copied-retry")
            return super().ingest(result)

    @contextmanager
    def runtime(_control, _pacer):
        ledger.append("runtime-open")
        try:
            yield LedgerSession([_result(), _result()], _report())
        finally:
            ledger.append("runtime-close")

    repository = LedgerRepository(ClaimedJob(job, (copied_retry, first)))
    assert CrawlWorker(repository, runtime, worker_id="worker").run_once() is True

    assert ledger == [
        "claim",
        "runtime-open",
        "start:fresh",
        "validate:fresh",
        "run:fresh",
        "ingest:fresh",
        "progress:fresh",
        "finish-target:fresh",
        "start:copied-retry",
        "validate:copied-retry",
        "run:copied-retry",
        "ingest:copied-retry",
        "progress:copied-retry",
        "finish-target:copied-retry",
        "runtime-close",
        "atomic-finalize",
    ]


@pytest.mark.parametrize(
    ("records", "issues", "report", "expected"),
    [
        (1, (ScrapeIssue("ordinary", "ordinary", None, ScrapeMode.AUTHENTICATED, "members"),), _report(), TargetStatus.PARTIAL),
        (0, (ScrapeIssue("ordinary", "ordinary", None, ScrapeMode.AUTHENTICATED, "members"),), _report(persisted=0), TargetStatus.FAILED),
        (1, (), _report(retries=1), TargetStatus.PARTIAL),
        (1, (), _report(failures=1), TargetStatus.FAILED),
    ],
)
def test_result_and_ingestion_outcomes_control_target_status(records, issues, report, expected) -> None:
    """Break caught: ordinary crawl/provider/database outcomes are all recorded as success."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    session = Session([_result(records=records, issues=issues)], report)

    CrawlWorker(repository, _runtime(session, []), worker_id="worker").run_once()

    finished = next(event for event in repository.events if event[0] == "finish_target")
    assert finished[2]["status"] is expected


def test_budget_exhaustion_is_partial_even_when_no_record_was_discovered() -> None:
    """Break caught: an exhausted target is reported failed/unretryable instead of partial."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    issue = ScrapeIssue(
        "authenticated_budget_exhausted",
        "Authenticated crawl budget was exhausted.",
        target.target_url,
        ScrapeMode.AUTHENTICATED,
        "members",
        True,
    )
    session = Session([_result(records=0, issues=(issue,))], _report(persisted=0))

    CrawlWorker(repository, _runtime(session, []), worker_id="worker").run_once()

    finished = next(event for event in repository.events if event[0] == "finish_target")
    assert finished[2]["status"] is TargetStatus.PARTIAL


@pytest.mark.parametrize(
    ("failure", "target_status", "job_status", "code"),
    [
        (CrawlCancelled(), TargetStatus.CANCELLED, JobStatus.CANCELLED, "crawl_cancelled"),
        (SessionError("session unavailable"), TargetStatus.BLOCKED, JobStatus.BLOCKED, "session_expired"),
        (RateLimitError("rate limited"), TargetStatus.BLOCKED, JobStatus.BLOCKED, "facebook_rate_limited"),
        (BrowserNavigationError("navigation failed"), TargetStatus.FAILED, JobStatus.FAILED, "authenticated_navigation_failed"),
        (DatabaseError("database failed"), TargetStatus.FAILED, JobStatus.FAILED, "database_error"),
        (JobBudgetReached(), TargetStatus.PARTIAL, JobStatus.PARTIAL, "job_budget_reached"),
        (KeyboardInterrupt(), TargetStatus.CANCELLED, JobStatus.CANCELLED, "crawl_cancelled"),
    ],
)
def test_worker_maps_typed_stops_to_safe_terminal_states(failure, target_status, job_status, code) -> None:
    """Break caught: a worker failure leaves a running target or uses an unsafe terminal state."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    session = Session([failure], _report())
    closed: list[str] = []

    assert CrawlWorker(repository, _runtime(session, closed), worker_id="worker").run_once() is True

    assert not [event for event in repository.events if event[0] == "finish_target"]
    final = _finalization(repository)
    assert final["status"] is job_status
    assert final["error_code"] == code
    assert final["active_target_id"] == target.id
    assert final["active_target_status"] is target_status
    assert closed == ["open", "close"]


def test_rate_limit_issue_is_promoted_before_provider_ingestion() -> None:
    """Break caught: Facebook circuit-breaker results are sent to FBNumber."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    issue = ScrapeIssue("authenticated_rate_limited", "unsafe browser detail", None, ScrapeMode.AUTHENTICATED, "members")
    session = Session([_result(issues=(issue,))], _report())

    CrawlWorker(repository, _runtime(session, []), worker_id="worker").run_once()

    assert not [event for event in session.events if event[0] == "ingest"]
    assert not [event for event in repository.events if event[0] == "account"]
    final = _finalization(repository)
    assert final["status"] is JobStatus.BLOCKED
    assert final["account_signal"] == "facebook_rate_limited"


def test_account_safety_stop_closes_the_runtime_then_blocks_the_target_and_account() -> None:
    """Break caught: a CAPTCHA-like stop is treated as ordinary crawl data."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    stop = AccountSafetyStop(SafetySignal(SafetyCode.CAPTCHA, "Facebook CAPTCHA requires manual review.", True))
    session = Session([stop], _report())
    lifecycle: list[str] = []

    CrawlWorker(repository, _runtime(session, lifecycle), worker_id="worker").run_once()

    assert lifecycle == ["open", "close"]
    assert not [event for event in repository.events if event[0] == "account"]
    final = _finalization(repository)
    assert final["active_target_id"] == target.id
    assert final["active_target_status"] is TargetStatus.BLOCKED
    assert final["account_signal"] == "captcha"


def test_second_target_failure_makes_a_partially_completed_job_partial() -> None:
    """Break caught: a later ordinary failure erases an earlier successful target."""
    job = _job()
    first = _target(job, 0, checkpoint_path="one.json")
    second = _target(job, 1, checkpoint_path="two.json")
    repository = Repository(ClaimedJob(job, (first, second)))
    session = Session([_result(), BrowserNavigationError("failed")], _report())

    CrawlWorker(repository, _runtime(session, []), worker_id="worker").run_once()

    final = _finalization(repository)
    assert final["status"] is JobStatus.PARTIAL
    assert final["error_code"] == "authenticated_navigation_failed"


def test_control_cancellation_before_runtime_prevents_browser_construction() -> None:
    """Break caught: a cancelling job opens a browser before cancellation is observed."""
    job = _job()
    job = CrawlJob(**{field: getattr(job, field) for field in job.__dataclass_fields__} | {"status": JobStatus.CANCELLING, "cancel_requested_at": NOW})
    repository = Repository(ClaimedJob(job, (_target(job, 0, checkpoint_path="checkpoint.json"),)))

    assert CrawlWorker(repository, lambda *_: pytest.fail("runtime opened"), worker_id="worker").run_once() is True
    assert not [event for event in repository.events if event[0] == "start"]


def test_control_persists_live_progress_against_the_active_target() -> None:
    """Break caught: collector progress is a target-less event until after ingestion."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    control = JobExecutionControl(
        repository,
        job,
        type("Heartbeat", (), {"lost": False})(),
        deadline_monotonic=9999,
        monotonic=lambda: 0,
    )

    control.set_active_target(target.id)
    control.emit("target_progress", counters={"steps_completed": 3})
    control.emit("provider_progress", counters={"users_persisted": 2})

    assert repository.events == [
        ("progress", target.id, {"steps_completed": 3}),
        ("event", "provider_progress", {"target_id": target.id, "counters": {"users_persisted": 2}}),
    ]


@pytest.mark.parametrize(
    ("cancel_during_write", "expected"),
    [(True, CrawlCancelled), (False, LeaseLost)],
)
def test_live_progress_reclassifies_cancel_and_ownership_races(
    cancel_during_write: bool,
    expected: type[BaseException],
) -> None:
    """Break caught: a rejected live counter write hides cancellation or lease loss."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")

    class RejectingRepository(Repository):
        def update_target_progress(self, *args, **kwargs):
            if cancel_during_write:
                assert self.current_job is not None
                self.current_job = replace(
                    self.current_job,
                    status=JobStatus.CANCELLING,
                    cancel_requested_at=NOW,
                )
            return False

    repository = RejectingRepository(ClaimedJob(job, (target,)))
    control = JobExecutionControl(
        repository,
        job,
        type("Heartbeat", (), {"lost": False})(),
        deadline_monotonic=9999,
        monotonic=lambda: 0,
    )
    control.set_active_target(target.id)

    with pytest.raises(expected):
        control.emit("target_progress", counters={"steps_completed": 1})


def test_collector_progress_is_durable_before_provider_ingestion() -> None:
    """Break caught: target counters remain zero while provider work is running."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))

    class ProgressSession(Session):
        control: JobExecutionControl

        def run(self, request):
            self.control.emit("target_progress", counters={"steps_completed": 2})
            return super().run(request)

        def ingest(self, result):
            assert ("progress", target.id, {"steps_completed": 2}) in repository.events
            return super().ingest(result)

    session = ProgressSession([_result()], _report())

    @contextmanager
    def runtime(control, pacer):
        session.control = control
        yield session

    CrawlWorker(repository, runtime, worker_id="worker").run_once()

    live_index = repository.events.index(
        ("progress", target.id, {"steps_completed": 2})
    )
    final_index = next(
        index
        for index, event in enumerate(repository.events)
        if event[0] == "progress" and event[2].get("items_discovered") == 1
    )
    assert live_index < final_index


def test_final_progress_does_not_replace_live_steps_with_user_count() -> None:
    """A high-yield page may discover many users while consuming one scroll step."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    many_users = _result(records=200)
    many_users = replace(
        many_users,
        stats=replace(many_users.stats, succeeded=200),
    )

    class OneStepSession(Session):
        control: JobExecutionControl

        def run(self, request):
            self.control.emit(
                "target_progress",
                counters={"steps_completed": 1},
            )
            return super().run(request)

    session = OneStepSession([many_users], _report(persisted=200))

    @contextmanager
    def runtime(control, pacer):
        session.control = control
        yield session

    CrawlWorker(repository, runtime, worker_id="worker").run_once()

    progress = [event[2] for event in repository.events if event[0] == "progress"]
    assert progress[0] == {"steps_completed": 1}
    assert progress[1] == {
        "items_discovered": 200,
        "users_persisted": 200,
        "provider_retries_required": 0,
    }


def test_worker_emits_browser_and_provider_lifecycle_at_truthful_boundaries() -> None:
    """Break caught: API event consumers cannot distinguish runtime/provider phases."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    session = Session([_result()], _report())

    CrawlWorker(repository, _runtime(session, []), worker_id="worker").run_once()

    events = [event for event in repository.events if event[0] == "event"]
    assert events[0] == ("event", "browser_started", {})
    assert events[1] == (
        "event",
        "provider_progress",
        {
            "target_id": target.id,
            "counters": {
                "users_persisted": 1,
                "provider_retries_required": 0,
            },
        },
    )


def test_policy_rejects_relaxing_safety_bounds() -> None:
    """Break caught: a worker configuration weakens a mandatory account-safety bound."""
    with pytest.raises(ValueError):
        WorkerPolicy(navigation_interval_seconds=7)
    with pytest.raises(ValueError):
        WorkerPolicy(job_timeout_seconds=1801)
    with pytest.raises(ValueError):
        WorkerPolicy(heartbeat_seconds=60)


def test_request_for_target_clamps_job_options_to_worker_safety_policy() -> None:
    """Break caught: API job options weaken the worker time or navigation safety limits."""
    job = _job()
    job = CrawlJob(**{field: getattr(job, field) for field in job.__dataclass_fields__} | {"request_options": SafeJobOptions(max_duration_seconds=300, navigation_delay_seconds=8)})
    target = _target(job, 0, checkpoint_path="checkpoint.json")

    request = request_for_target(job, target, WorkerPolicy(job_timeout_seconds=120, navigation_interval_seconds=12))

    assert request.targets == (target.target_url,)
    assert request.resume is True
    assert request.max_duration_seconds == 120
    assert request.delay_seconds == 12


def test_heartbeat_loss_stops_before_any_further_persistence() -> None:
    """Break caught: a worker persists collected data after losing its lease."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    heartbeat = type("Heartbeat", (), {"lost": True})()
    control = JobExecutionControl(repository, job, heartbeat, deadline_monotonic=9999)

    with pytest.raises(LeaseLost):
        control.guard_before_persistence()


def test_heartbeat_renews_on_a_daemon_thread_and_joins_on_context_exit() -> None:
    """Break caught: lease renewal runs inline or leaks a thread after worker cleanup."""
    job = _job()
    repository = Repository(ClaimedJob(job, ()))
    time_values = iter((0.0, 0.0, 10.0))
    calls: list[object] = []

    class Event:
        def __init__(self) -> None:
            self.waits = 0
            self.set_called = False

        def wait(self, timeout=None) -> bool:
            calls.append(("wait", timeout))
            self.waits += 1
            return self.waits == 2

        def set(self) -> None:
            self.set_called = True

    class Thread:
        def __init__(self, *, target, daemon, name) -> None:
            calls.append(("thread", daemon, name))
            self.target = target

        def start(self) -> None:
            self.target()

        def join(self) -> None:
            calls.append("join")

    event = Event()
    heartbeat = LeaseHeartbeat(
        repository, job.id, "worker", lease_duration=timedelta(seconds=60),
        heartbeat_seconds=10, stop_event=event, monotonic=lambda: next(time_values),
        thread_factory=Thread,
    )

    with heartbeat:
        pass

    assert calls == [("thread", True, "crawl-job-heartbeat"), ("wait", 10.0), ("wait", 10.0), "join"]
    assert event.set_called is True
    assert repository.events[-1] == ("heartbeat", job.id, "worker", timedelta(seconds=60))
    assert heartbeat.lost is False


def test_heartbeat_marks_database_or_ownership_failure_as_lost() -> None:
    """Break caught: failed heartbeat renewal permits further side effects."""
    job = _job()
    repository = Repository(ClaimedJob(job, ()), heartbeat_result=False)

    class Event:
        waits = 0
        def wait(self, timeout=None) -> bool:
            self.waits += 1
            return self.waits > 1
        def set(self) -> None:
            return None

    heartbeat = LeaseHeartbeat(
        repository, job.id, "worker", lease_duration=timedelta(seconds=60),
        stop_event=Event(), monotonic=lambda: 0.0,
        thread_factory=lambda **kwargs: type("Thread", (), {"start": kwargs["target"], "join": lambda self: None})(),
    )

    with heartbeat:
        pass

    assert heartbeat.lost is True


def test_heartbeat_exception_marks_lost_and_still_joins() -> None:
    """Break caught: a renewal database exception leaks the heartbeat or permits work."""
    job = _job()
    repository = Repository(ClaimedJob(job, ()))
    joined: list[str] = []

    def heartbeat(*_args, **_kwargs):
        raise DatabaseError("lease-secret")

    repository.heartbeat = heartbeat  # type: ignore[method-assign]

    class Event:
        calls = 0

        def wait(self, timeout=None) -> bool:
            self.calls += 1
            return self.calls > 1

        def set(self) -> None:
            return None

    heartbeat_worker = LeaseHeartbeat(
        repository,
        job.id,
        "worker",
        lease_duration=timedelta(seconds=60),
        stop_event=Event(),
        monotonic=lambda: 0.0,
        thread_factory=lambda **kwargs: type(
            "Thread",
            (),
            {"start": kwargs["target"], "join": lambda self: joined.append("join")},
        )(),
    )

    with heartbeat_worker:
        pass

    assert heartbeat_worker.lost is True
    assert joined == ["join"]


@pytest.mark.parametrize(
    ("heartbeat_lost", "deadline", "expected"),
    [
        (True, 9999.0, LeaseLost),
        (False, 0.0, JobBudgetReached),
    ],
)
def test_control_rejects_lost_or_expired_work_before_reading_cancellation(
    heartbeat_lost: bool,
    deadline: float,
    expected: type[BaseException],
) -> None:
    """Break caught: collector checks query cancellation after the lease/deadline is unsafe."""
    job = _job()
    repository = Repository(ClaimedJob(job, ()))
    cancellation_reads: list[object] = []

    def get_job(job_id):
        cancellation_reads.append(job_id)
        return job

    repository.get_job = get_job  # type: ignore[method-assign]
    heartbeat = type("Heartbeat", (), {"lost": heartbeat_lost})()
    control = JobExecutionControl(
        repository,
        job,
        heartbeat,
        deadline_monotonic=deadline,
        monotonic=lambda: 1.0,
    )

    with pytest.raises(expected):
        control.is_cancel_requested()
    assert cancellation_reads == []


def test_safety_terminal_uses_one_atomic_finalizer_after_runtime_cleanup() -> None:
    """Break caught: safety completion splits target, account, and job state across transactions."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    session = Session([RateLimitError("rate limited")], _report())
    lifecycle: list[str] = []

    CrawlWorker(
        repository,
        _runtime(session, lifecycle),
        worker_id="worker",
        policy=WorkerPolicy(normal_cooldown_seconds=7200, rate_limit_cooldown_seconds=43200),
    ).run_once()

    assert lifecycle == ["open", "close"]
    assert not [event for event in repository.events if event[0] in {"account", "finish_target"}]
    final = _finalization(repository)
    assert final == {
        "status": JobStatus.BLOCKED,
        "error_code": "facebook_rate_limited",
        "error_message": "",
        "active_target_id": target.id,
        "active_target_status": TargetStatus.BLOCKED,
        "account_signal": "facebook_rate_limited",
        "normal_cooldown": timedelta(seconds=7200),
        "rate_limit_cooldown": timedelta(seconds=43200),
        "now": final["now"],
    }


@pytest.mark.parametrize(
    ("heartbeat_lost", "deadline", "expected"),
    [(True, 9999.0, LeaseLost), (False, 0.0, JobBudgetReached)],
)
def test_collector_guard_stops_lost_or_expired_work_before_browser_inspection(
    heartbeat_lost: bool,
    deadline: float,
    expected: type[BaseException],
) -> None:
    """Break caught: a collector inspects Facebook after the job is already unsafe."""
    job = _job()
    repository = Repository(ClaimedJob(job, ()))
    heartbeat = type("Heartbeat", (), {"lost": heartbeat_lost})()
    control = JobExecutionControl(
        repository,
        job,
        heartbeat,
        deadline_monotonic=deadline,
        monotonic=lambda: 1.0,
    )

    with pytest.raises(expected):
        guard_execution(control, object())


def test_cancellation_after_collection_stops_before_ingestion() -> None:
    """Break caught: a cancellation that arrives with collected DOM data calls the provider."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    session = Session([_result()], _report())
    original_run = session.run

    def run_then_cancel(request):
        result = original_run(request)
        repository.current_job = replace(
            job,
            status=JobStatus.CANCELLING,
            cancel_requested_at=NOW,
        )
        return result

    session.run = run_then_cancel  # type: ignore[method-assign]

    CrawlWorker(repository, _runtime(session, []), worker_id="worker").run_once()

    assert not [event for event in session.events if event[0] == "ingest"]
    assert not [event for event in repository.events if event[0] == "progress"]
    assert _finalization(repository)["status"] is JobStatus.CANCELLED


def test_cancellation_before_next_target_never_starts_the_next_target() -> None:
    """Break caught: a cancellation after target persistence starts another target."""
    job = _job()
    first = _target(job, 0, checkpoint_path="first.json")
    second = _target(job, 1, checkpoint_path="second.json")
    repository = Repository(ClaimedJob(job, (first, second)))
    session = Session([_result(), _result()], _report())
    original_finish_target = repository.finish_target

    def finish_target(*args, **kwargs):
        changed = original_finish_target(*args, **kwargs)
        repository.current_job = replace(
            job,
            status=JobStatus.CANCELLING,
            cancel_requested_at=NOW,
        )
        return changed

    repository.finish_target = finish_target  # type: ignore[method-assign]

    CrawlWorker(repository, _runtime(session, []), worker_id="worker").run_once()

    starts = [event for event in repository.events if event[0] == "start"]
    assert starts == [("start", first.id)]
    assert _finalization(repository)["status"] is JobStatus.CANCELLED


def test_cancellation_after_progress_stops_before_target_terminal_persistence() -> None:
    """Break caught: a cancellation during progress still closes the running target as success."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    original_progress = repository.update_target_progress

    def progress_then_cancel(*args, **kwargs):
        changed = original_progress(*args, **kwargs)
        repository.current_job = replace(
            job,
            status=JobStatus.CANCELLING,
            cancel_requested_at=NOW,
        )
        return changed

    repository.update_target_progress = progress_then_cancel  # type: ignore[method-assign]

    assert CrawlWorker(
        repository,
        _runtime(Session([_result()], _report()), []),
        worker_id="worker",
    ).run_once() is True

    assert not [event for event in repository.events if event[0] == "finish_target"]
    assert _finalization(repository)["status"] is JobStatus.CANCELLED


@pytest.mark.parametrize("rejected_write", ["start", "progress", "finish"])
def test_rejected_target_write_is_reclassified_as_cancellation_not_lease_loss(
    rejected_write: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Break caught: a concurrent request_cancel makes a normal write look like lease theft."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))

    def cancel_then_reject(*_args, **_kwargs) -> bool:
        repository.current_job = replace(
            job,
            status=JobStatus.CANCELLING,
            cancel_requested_at=NOW,
        )
        return False

    if rejected_write == "start":
        repository.start_target = cancel_then_reject  # type: ignore[method-assign]
    elif rejected_write == "progress":
        repository.update_target_progress = cancel_then_reject  # type: ignore[method-assign]
    else:
        repository.finish_target = cancel_then_reject  # type: ignore[method-assign]

    assert CrawlWorker(
        repository,
        _runtime(Session([_result()], _report()), []),
        worker_id="worker",
    ).run_once() is True

    final = _finalization(repository)
    assert final["status"] is JobStatus.CANCELLED
    assert final["error_code"] == "crawl_cancelled"
    assert final["active_target_id"] == (None if rejected_write == "start" else target.id)
    assert "lease was lost" not in caplog.text


def test_rejected_target_write_propagates_budget_before_falling_back_to_lease_loss() -> None:
    """Break caught: a deadline reached during a rejected write is recorded as a lease loss."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    clock = [0.0]

    def deadline_then_reject(*_args, **_kwargs) -> bool:
        clock[0] = 1.0
        return False

    repository.start_target = deadline_then_reject  # type: ignore[method-assign]
    assert CrawlWorker(
        repository,
        _runtime(Session([_result()], _report()), []),
        worker_id="worker",
        policy=WorkerPolicy(job_timeout_seconds=1),
        monotonic=lambda: clock[0],
    ).run_once() is True

    final = _finalization(repository)
    assert (final["status"], final["error_code"]) == (JobStatus.PARTIAL, "job_budget_reached")


def test_rejected_target_write_preserves_a_database_reread_as_a_sanitized_failure() -> None:
    """Break caught: rereading a rejected write turns a database failure into lease loss."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    reads = [0]

    def get_job(job_id):
        assert job_id == job.id
        reads[0] += 1
        if reads[0] == 3:
            raise DatabaseError("database-reread-secret")
        return job

    repository.get_job = get_job  # type: ignore[method-assign]
    repository.start_target = lambda *_args, **_kwargs: False  # type: ignore[method-assign]

    assert CrawlWorker(
        repository,
        _runtime(Session([_result()], _report()), []),
        worker_id="worker",
    ).run_once() is True

    final = _finalization(repository)
    assert (final["status"], final["error_code"], final["error_message"]) == (
        JobStatus.FAILED,
        "database_error",
        "",
    )


def test_rejected_target_write_without_cooperative_stop_is_lease_loss() -> None:
    """Break caught: an unexplained rejected write is incorrectly finalized as ordinary failure."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    repository.start_target = lambda *_args, **_kwargs: False  # type: ignore[method-assign]

    assert CrawlWorker(
        repository,
        _runtime(Session([_result()], _report()), []),
        worker_id="worker",
    ).run_once() is True

    assert not [event for event in repository.events if event[0] == "finish_job"]


def test_lost_lease_during_collection_closes_runtime_without_later_persistence() -> None:
    """Break caught: a lost worker lease sends collected data to persistence or finalizes state."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    heartbeat = type(
        "Heartbeat",
        (),
        {
            "lost": False,
            "__enter__": lambda self: self,
            "__exit__": lambda self, *_: False,
        },
    )()
    session = Session([_result()], _report())
    original_run = session.run

    def run_then_lose_lease(request):
        result = original_run(request)
        heartbeat.lost = True
        return result

    session.run = run_then_lose_lease  # type: ignore[method-assign]
    lifecycle: list[str] = []

    CrawlWorker(
        repository,
        _runtime(session, lifecycle),
        worker_id="worker",
        heartbeat_factory=lambda *_args, **_kwargs: heartbeat,
    ).run_once()

    assert lifecycle == ["open", "close"]
    assert not [event for event in repository.events if event[0] in {"progress", "finish_target", "finish_job"}]


def test_terminal_database_failure_is_logged_after_runtime_cleanup(caplog: pytest.LogCaptureFixture) -> None:
    """Break caught: final persistence errors mask cleanup or log provider/database detail."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))

    def fail_terminal(*args, **kwargs):
        repository.events.append(("finish_job", args[0], kwargs))
        raise DatabaseError("provider-token-secret")

    repository.finish_job = fail_terminal  # type: ignore[method-assign]
    lifecycle: list[str] = []

    CrawlWorker(repository, _runtime(Session([_result()], _report()), lifecycle), worker_id="worker").run_once()

    assert lifecycle == ["open", "close"]
    assert "Unable to persist terminal crawl job state" in caplog.text
    assert "provider-token-secret" not in caplog.text


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (KeyboardInterrupt(), "crawl_cancelled"),
        (DatabaseError("database-secret"), "database_error"),
    ],
)
def test_cleanup_crossing_deadline_preserves_the_primary_terminal_cause(
    failure: BaseException,
    expected_code: str,
) -> None:
    """Break caught: cleanup timeouts rewrite an interrupt or database failure as budget exhaustion."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    clock = [0.0]
    session = Session([failure], _report())

    @contextmanager
    def runtime(_control, _pacer):
        try:
            yield session
        finally:
            clock[0] = 1.0

    CrawlWorker(
        repository,
        runtime,
        worker_id="worker",
        policy=WorkerPolicy(job_timeout_seconds=1),
        monotonic=lambda: clock[0],
    ).run_once()

    assert _finalization(repository)["error_code"] == expected_code


def test_final_cancellation_read_database_failure_does_not_mutate_terminal_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Break caught: an unreadable cancellation state still writes terminal/account mutations."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))
    fail_final_read = [False]

    def get_job(job_id):
        if fail_final_read[0]:
            raise DatabaseError("database-password-secret")
        assert job_id == job.id
        return job

    repository.get_job = get_job  # type: ignore[method-assign]

    @contextmanager
    def runtime(_control, _pacer):
        try:
            yield Session([_result()], _report())
        finally:
            fail_final_read[0] = True

    assert CrawlWorker(repository, runtime, worker_id="worker").run_once() is True
    assert not [event for event in repository.events if event[0] == "finish_job"]
    assert "Unable to read final crawl job state" in caplog.text
    assert "database-password-secret" not in caplog.text


def test_final_cancellation_observation_does_not_replace_an_existing_database_terminal() -> None:
    """Break caught: post-cleanup cancellation masks the primary provider/database failure."""
    job = _job()
    target = _target(job, 0, checkpoint_path="checkpoint.json")
    repository = Repository(ClaimedJob(job, (target,)))

    @contextmanager
    def runtime(_control, _pacer):
        try:
            yield Session([DatabaseError("database-secret")], _report())
        finally:
            repository.current_job = replace(
                job,
                status=JobStatus.CANCELLING,
                cancel_requested_at=NOW,
            )

    assert CrawlWorker(repository, runtime, worker_id="worker").run_once() is True
    assert _finalization(repository)["error_code"] == "database_error"
