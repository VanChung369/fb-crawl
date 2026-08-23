from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Thread
from uuid import UUID, uuid4

import psycopg
import pytest

from fb_crawl.core.jobs import (
    AccountStatus, IdempotencyConflict, JobConflict, JobCreateCommand, JobStatus, SafeJobOptions,
    TargetStatus, SafetyCode,
    canonical_job_target, canonical_request_fingerprint,
)
from fb_crawl.core.models import AuthenticatedAction
from fb_crawl.services.jobs import JobService
from fb_data_pipeline.repositories.jobs import JobRepository
from fb_data_pipeline.repositories.errors import DatabaseError
from fb_data_pipeline.repositories.migrations import MigrationRunner


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not TEST_DATABASE_URL.endswith("/fb_pipeline_test"),
    reason="TEST_DATABASE_URL must explicitly target fb_pipeline_test",
)


def _assert_dedicated_test_database() -> None:
    assert TEST_DATABASE_URL.endswith("/fb_pipeline_test")
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        assert connection.info.dbname == "fb_pipeline_test"


@pytest.fixture(autouse=True)
def clean_job_tables() -> None:
    _assert_dedicated_test_database()
    MigrationRunner(TEST_DATABASE_URL).apply()
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM crawl_jobs")
            cursor.execute(
                """
                UPDATE crawler_account_state
                SET status = 'ready', cooldown_until = NULL, last_job_id = NULL,
                    last_started_at = NULL, last_finished_at = NULL,
                    rate_limit_count_24h = 0, last_rate_limit_at = NULL,
                    last_warning_code = '', last_warning_at = NULL,
                    block_reason = '', acknowledged_at = NULL, updated_at = now()
                WHERE account_key = 'default'
                """
            )


def command(*urls: str) -> JobCreateCommand:
    return JobCreateCommand(
        AuthenticatedAction.MEMBERS,
        tuple(canonical_job_target(AuthenticatedAction.MEMBERS, url) for url in urls),
        SafeJobOptions(),
    )


def test_create_job_is_atomic_and_idempotency_replays_only_the_same_fingerprint() -> None:
    """Break caught: replaying a request duplicates targets or accepts a changed body."""
    repository = JobRepository(TEST_DATABASE_URL)
    created_command = command(
        "https://www.facebook.com/groups/100",
        "https://www.facebook.com/groups/200",
    )
    fingerprint = canonical_request_fingerprint(created_command)

    job, created = repository.create_job(created_command, idempotency_key="request-1", request_fingerprint=fingerprint)
    replay, replay_created = repository.create_job(created_command, idempotency_key="request-1", request_fingerprint=fingerprint)

    assert created is True
    assert replay_created is False
    assert replay == job
    assert [target.target_url for target in repository.list_targets(job.id).items] == [
        "https://www.facebook.com/groups/100/members",
        "https://www.facebook.com/groups/200/members",
    ]
    assert [event.event_type for event in repository.list_events(job.id).items] == ["job_created"]
    with pytest.raises(IdempotencyConflict) as captured:
        repository.create_job(created_command, idempotency_key="request-1", request_fingerprint="different")
    assert captured.value.code == "job_idempotency_conflict"
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM crawl_jobs")
            assert cursor.fetchone() == (1,)
            cursor.execute("SELECT count(*) FROM crawl_targets")
            assert cursor.fetchone() == (2,)


def test_keyset_reads_preserve_order_without_duplicates_and_events_poll_by_id() -> None:
    """Break caught: page boundaries use offset semantics and lose or repeat records."""
    repository = JobRepository(TEST_DATABASE_URL)
    ids: list[UUID] = []
    for index in range(3):
        item, _ = repository.create_job(command(f"https://www.facebook.com/groups/{100 + index}"), idempotency_key=f"key-{index}", request_fingerprint=f"fp-{index}")
        ids.append(item.id)
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            for index, job_id in enumerate(ids):
                cursor.execute("UPDATE crawl_jobs SET created_at = %s, updated_at = %s WHERE id = %s", (datetime(2026, 8, 21, tzinfo=UTC) + timedelta(seconds=index), datetime(2026, 8, 21, tzinfo=UTC), job_id))
    first = repository.list_jobs(limit=2)
    second = repository.list_jobs(limit=2, cursor=first.next_cursor)
    assert [job.id for job in first.items + second.items] == list(reversed(ids))
    assert first.next_cursor is not None
    events = repository.list_events(ids[0], after_id=0, limit=500)
    assert len(events.items) == 1
    assert events.next_cursor is None


def test_job_keysets_handle_equal_timestamps_in_descending_uuid_order() -> None:
    """Break caught: jobs sharing a timestamp disappear at the UUID keyset boundary."""
    repository = JobRepository(TEST_DATABASE_URL)
    ids = [
        repository.create_job(
            command(f"https://www.facebook.com/groups/{100 + index}"),
            idempotency_key=f"equal-time-{index}", request_fingerprint=f"equal-time-{index}",
        )[0].id
        for index in range(3)
    ]
    same_time = datetime(2026, 8, 21, tzinfo=UTC)
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE crawl_jobs SET created_at = %s, updated_at = %s", (same_time, same_time))

    first = repository.list_jobs(limit=2)
    second = repository.list_jobs(limit=2, cursor=first.next_cursor)

    assert [job.id for job in first.items + second.items] == sorted(ids, reverse=True)


def test_target_keysets_and_event_polling_are_bounded() -> None:
    """Break caught: target/event reads grow unbounded or skip a keyset boundary."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(
        command(
            "https://www.facebook.com/groups/100",
            "https://www.facebook.com/groups/200",
            "https://www.facebook.com/groups/300",
        ),
        idempotency_key="paged-targets",
        request_fingerprint="paged-targets",
    )
    first_targets = repository.list_targets(job.id, limit=2)
    second_targets = repository.list_targets(job.id, limit=2, cursor=first_targets.next_cursor)
    assert [item.position for item in first_targets.items + second_targets.items] == [0, 1, 2]

    for count in range(101):
        repository.append_event(job.id, "target_progress", "info", counters={"steps_completed": count})
    page = repository.list_events(job.id, after_id=0, limit=500)
    assert len(page.items) == 100
    assert page.next_cursor == str(page.items[-1].id)
    later = repository.list_events(job.id, after_id=page.items[-1].id, limit=100)
    event_ids = [event.id for event in page.items + later.items]
    assert event_ids == sorted(event_ids)
    assert len(event_ids) == len(set(event_ids)) == 102


def test_append_event_sanitizes_controls_without_splitting_ordinary_text_and_caps_length() -> None:
    """Break caught: sanitization expands normal text into spaced characters or stores unbounded text."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(
        command("https://www.facebook.com/groups/100"),
        idempotency_key="event-message", request_fingerprint="event-message",
    )

    appended = repository.append_event(
        job.id,
        "target_progress",
        "info",
        safe_message="ordinary text\twith\ncontrols " + "x" * 600,
    )
    read_back = repository.list_events(job.id).items[-1]

    assert appended.safe_message == read_back.safe_message
    assert appended.safe_message.startswith("ordinary text with controls ")
    assert len(appended.safe_message) == 500


def test_create_job_rolls_back_all_rows_when_the_second_target_insert_fails() -> None:
    """Break caught: a target write failure leaves a partial job/event transaction behind."""
    repository = JobRepository(TEST_DATABASE_URL)
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE FUNCTION reject_second_crawl_target() RETURNS trigger AS $$
                BEGIN
                    IF NEW.position = 1 THEN
                        RAISE EXCEPTION 'injected target failure';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """
            )
            cursor.execute(
                """
                CREATE TRIGGER reject_second_crawl_target_trigger
                BEFORE INSERT ON crawl_targets
                FOR EACH ROW EXECUTE FUNCTION reject_second_crawl_target()
                """
            )
    try:
        with pytest.raises(DatabaseError) as captured:
            repository.create_job(
                command(
                    "https://www.facebook.com/groups/100",
                    "https://www.facebook.com/groups/200",
                ),
                idempotency_key="rollback", request_fingerprint="rollback",
            )
        assert isinstance(captured.value.__cause__, psycopg.DatabaseError)
    finally:
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DROP TRIGGER reject_second_crawl_target_trigger ON crawl_targets")
                cursor.execute("DROP FUNCTION reject_second_crawl_target()")
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM crawl_jobs")
            assert cursor.fetchone() == (0,)
            cursor.execute("SELECT count(*) FROM crawl_targets")
            assert cursor.fetchone() == (0,)
            cursor.execute("SELECT count(*) FROM crawl_job_events")
            assert cursor.fetchone() == (0,)


def test_request_cancel_changes_queued_and_running_jobs_once_and_leaves_terminal_jobs_alone() -> None:
    """Break caught: cancellation repeats events or mutates a terminal job."""
    repository = JobRepository(TEST_DATABASE_URL)
    queued, _ = repository.create_job(command("https://www.facebook.com/groups/100"), idempotency_key="queued", request_fingerprint="queued")
    running, _ = repository.create_job(command("https://www.facebook.com/groups/200"), idempotency_key="running", request_fingerprint="running")
    terminal, _ = repository.create_job(command("https://www.facebook.com/groups/300"), idempotency_key="terminal", request_fingerprint="terminal")
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE crawl_jobs SET status = 'running' WHERE id = %s", (running.id,))
            cursor.execute("UPDATE crawl_jobs SET status = 'succeeded' WHERE id = %s", (terminal.id,))
    queued_cancelled = repository.request_cancel(queued.id)
    assert queued_cancelled is not None
    assert queued_cancelled.status is JobStatus.CANCELLED
    assert queued_cancelled.cancel_requested_at is not None
    repeated_queued = repository.request_cancel(queued.id)
    assert repeated_queued is not None
    assert repeated_queued.status is JobStatus.CANCELLED
    assert repeated_queued.cancel_requested_at == queued_cancelled.cancel_requested_at
    running_cancelled = repository.request_cancel(running.id)
    assert running_cancelled is not None
    assert running_cancelled.status is JobStatus.CANCELLING
    assert running_cancelled.cancel_requested_at is not None
    repeated_running = repository.request_cancel(running.id)
    assert repeated_running is not None
    assert repeated_running.status is JobStatus.CANCELLING
    assert repeated_running.cancel_requested_at == running_cancelled.cancel_requested_at
    terminal_before = repository.get_job(terminal.id)
    terminal_after = repository.request_cancel(terminal.id)
    assert terminal_before is not None
    assert terminal_after is not None
    assert terminal_after.status is JobStatus.SUCCEEDED
    assert terminal_after.cancel_requested_at == terminal_before.cancel_requested_at
    assert repository.request_cancel(uuid4()) is None
    assert [target.status.value for target in repository.list_targets(queued.id).items] == ["cancelled"]
    assert [event.event_type for event in repository.list_events(queued.id).items] == [
        "job_created",
        "cancel_requested",
        "job_cancelled",
    ]
    assert [event.event_type for event in repository.list_events(running.id).items] == ["job_created", "cancel_requested"]
    assert [event.event_type for event in repository.list_events(terminal.id).items] == ["job_created"]


def test_claim_lease_target_lifecycle_and_terminal_closure_are_owner_guarded() -> None:
    """Break caught: another worker can mutate a lease or terminal jobs retain active targets."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(
        command("https://www.facebook.com/groups/100", "https://www.facebook.com/groups/200"),
        idempotency_key="lifecycle", request_fingerprint="lifecycle",
    )

    claim = repository.claim_next("worker-one", lease_duration=timedelta(minutes=2))
    assert claim is not None
    assert claim.job.id == job.id
    assert claim.job.attempt == 1
    assert claim.job.worker_id == "worker-one"
    assert claim.job.lease_expires_at is not None
    assert repository.claim_next("worker-two", lease_duration=timedelta(minutes=2)) is None
    assert [event.event_type for event in repository.list_events(job.id).items] == ["job_created", "job_claimed"]
    assert not repository.heartbeat(job.id, "worker-two", lease_duration=timedelta(minutes=2))
    assert repository.heartbeat(job.id, "worker-one", lease_duration=timedelta(minutes=2))

    first, second = repository.list_targets(job.id).items
    assert not repository.start_target(job.id, first.id, "worker-two")
    assert repository.start_target(job.id, first.id, "worker-one")
    assert repository.update_target_progress(
        job.id, first.id, "worker-one", steps_completed=4, items_discovered=5, users_persisted=3
    )
    assert repository.update_target_progress(
        job.id, first.id, "worker-one", steps_completed=2, items_discovered=1, users_persisted=1
    )
    progressed = repository.list_targets(job.id).items[0]
    assert (progressed.steps_completed, progressed.items_discovered, progressed.users_persisted) == (4, 5, 3)
    assert not repository.update_target_progress(job.id, first.id, "worker-two", steps_completed=99)
    assert not repository.finish_target(job.id, first.id, "worker-two", status=TargetStatus.FAILED)
    assert not repository.finish_job(job.id, "worker-two", status=JobStatus.FAILED)
    assert repository.finish_target(job.id, first.id, "worker-one", status=TargetStatus.SUCCEEDED)
    assert repository.finish_job(job.id, "worker-one", status=JobStatus.SUCCEEDED)

    stored = repository.get_job(job.id)
    assert stored is not None and stored.status is JobStatus.SUCCEEDED
    assert stored.completed_targets == 1
    assert [target.status for target in repository.list_targets(job.id).items] == [
        TargetStatus.SUCCEEDED, TargetStatus.SKIPPED,
    ]
    assert repository.list_targets(job.id).items[1].attempt == 0
    account = repository.get_account("default")
    assert account.status is AccountStatus.COOLDOWN
    assert account.cooldown_until is not None and stored.finished_at is not None
    assert account.cooldown_until >= stored.finished_at + timedelta(minutes=60)


def test_heartbeat_renews_a_cancelling_owned_job_without_losing_its_lease() -> None:
    """Break caught: user cancellation makes a healthy worker abandon its claimed lease."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(
        command("https://www.facebook.com/groups/100"),
        idempotency_key="cancelling-heartbeat",
        request_fingerprint="cancelling-heartbeat",
    )
    claim = repository.claim_next("worker-one", lease_duration=timedelta(seconds=60))
    assert claim is not None and claim.job.id == job.id

    cancelled = repository.request_cancel(job.id)
    assert cancelled is not None and cancelled.status is JobStatus.CANCELLING
    assert repository.heartbeat(job.id, "worker-one", lease_duration=timedelta(seconds=60))

    stored = repository.get_job(job.id)
    assert stored is not None
    assert stored.status is JobStatus.CANCELLING
    assert stored.lease_expires_at is not None and stored.lease_expires_at > datetime.now(UTC)


def test_atomic_finalizer_turns_cancelling_work_into_cancelled_and_preserves_block_override() -> None:
    """Break caught: terminal success races a user cancellation or suppresses an account block."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(
        command("https://www.facebook.com/groups/100"),
        idempotency_key="atomic-cancelling",
        request_fingerprint="atomic-cancelling",
    )
    claim = repository.claim_next("worker-one", lease_duration=timedelta(minutes=2))
    assert claim is not None
    target = claim.targets[0]
    assert repository.start_target(job.id, target.id, "worker-one")
    assert repository.request_cancel(job.id).status is JobStatus.CANCELLING  # type: ignore[union-attr]
    assert not repository.finish_target(
        job.id,
        target.id,
        "worker-one",
        status=TargetStatus.SUCCEEDED,
    )

    assert repository.finish_job(
        job.id,
        "worker-one",
        status=JobStatus.SUCCEEDED,
        active_target_id=target.id,
        active_target_status=TargetStatus.SUCCEEDED,
    )
    stored_cancelled = repository.get_job(job.id)
    cancelled_target = repository.list_targets(job.id).items[0]
    assert stored_cancelled is not None and stored_cancelled.status is JobStatus.CANCELLED
    assert (stored_cancelled.error_code, stored_cancelled.error_message) == ("crawl_cancelled", "")
    assert cancelled_target.status is TargetStatus.CANCELLED
    assert (cancelled_target.error_code, cancelled_target.error_message) == ("crawl_cancelled", "")
    assert repository.get_account("default").status is AccountStatus.COOLDOWN  # type: ignore[union-attr]
    assert [event.event_type for event in repository.list_events(job.id).items][-1] == "job_cancelled"

    repository.set_account_state(account_key="default", status=AccountStatus.READY, now=datetime.now(UTC))
    blocked, _ = repository.create_job(
        command("https://www.facebook.com/groups/200"),
        idempotency_key="atomic-blocked",
        request_fingerprint="atomic-blocked",
    )
    blocked_claim = repository.claim_next("worker-two", lease_duration=timedelta(minutes=2))
    assert blocked_claim is not None
    blocked_target = blocked_claim.targets[0]
    assert repository.start_target(blocked.id, blocked_target.id, "worker-two")
    assert repository.request_cancel(blocked.id).status is JobStatus.CANCELLING  # type: ignore[union-attr]

    assert repository.finish_job(
        blocked.id,
        "worker-two",
        status=JobStatus.BLOCKED,
        active_target_id=blocked_target.id,
        active_target_status=TargetStatus.BLOCKED,
        account_signal="facebook_rate_limited",
    )
    assert repository.get_job(blocked.id).status is JobStatus.BLOCKED  # type: ignore[union-attr]
    assert repository.list_targets(blocked.id).items[0].status is TargetStatus.BLOCKED


@pytest.mark.parametrize(
    "requested_status",
    [JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.CANCELLED],
)
def test_cancelling_finalizer_uses_canonical_empty_cancellation_result_for_every_nonblock_terminal(
    requested_status: JobStatus,
) -> None:
    """Break caught: one of the ordinary terminal paths leaks an obsolete error into a cancellation."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(
        command("https://www.facebook.com/groups/100"),
        idempotency_key=f"canonical-cancel-{requested_status.value}",
        request_fingerprint=f"canonical-cancel-{requested_status.value}",
    )
    claim = repository.claim_next("worker", lease_duration=timedelta(minutes=2))
    assert claim is not None
    target = claim.targets[0]
    assert repository.start_target(job.id, target.id, "worker")
    assert repository.request_cancel(job.id).status is JobStatus.CANCELLING  # type: ignore[union-attr]

    assert repository.finish_job(
        job.id,
        "worker",
        status=requested_status,
        error_code="obsolete-worker-code",
        error_message="obsolete private detail",
        active_target_id=target.id,
        active_target_status=TargetStatus.FAILED,
    )

    stored = repository.get_job(job.id)
    target = repository.list_targets(job.id).items[0]
    assert stored is not None
    assert (stored.status, stored.error_code, stored.error_message) == (
        JobStatus.CANCELLED,
        "crawl_cancelled",
        "",
    )
    assert (target.status, target.error_code, target.error_message) == (
        TargetStatus.CANCELLED,
        "crawl_cancelled",
        "",
    )


def test_expired_same_owner_cannot_heartbeat_or_atomically_finalize() -> None:
    """Break caught: a worker renews or finalizes after its database lease is expired."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(
        command("https://www.facebook.com/groups/100"),
        idempotency_key="expired-owner",
        request_fingerprint="expired-owner",
    )
    claim = repository.claim_next("owner", lease_duration=timedelta(minutes=2))
    assert claim is not None
    target = claim.targets[0]
    assert repository.start_target(job.id, target.id, "owner")
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE crawl_jobs SET lease_expires_at = clock_timestamp() - interval '1 second' WHERE id = %s",
                (job.id,),
            )

    assert not repository.heartbeat(job.id, "owner", lease_duration=timedelta(minutes=2))
    assert not repository.finish_job(
        job.id,
        "owner",
        status=JobStatus.BLOCKED,
        active_target_id=target.id,
        active_target_status=TargetStatus.BLOCKED,
        account_signal="facebook_rate_limited",
    )
    assert repository.get_job(job.id).status is JobStatus.RUNNING  # type: ignore[union-attr]
    assert repository.list_targets(job.id).items[0].status is TargetStatus.RUNNING
    assert repository.get_account("default").status is AccountStatus.READY  # type: ignore[union-attr]


def test_finalizer_rechecks_wall_clock_after_waiting_for_account_lock() -> None:
    """Break caught: a transaction started before expiry finalizes after an account-lock wait."""
    repository = JobRepository(TEST_DATABASE_URL, statement_timeout_seconds=1)
    job, _ = repository.create_job(
        command("https://www.facebook.com/groups/100"),
        idempotency_key="account-lock-expiry",
        request_fingerprint="account-lock-expiry",
    )
    claim = repository.claim_next("owner", lease_duration=timedelta(milliseconds=100))
    assert claim is not None
    target = claim.targets[0]
    waiting_on_account = Event()
    result: list[bool] = []

    class TracedCursor:
        def __init__(self, cursor) -> None:
            self._cursor = cursor

        def __enter__(self):
            self._cursor.__enter__()
            return self

        def __exit__(self, *args):
            return self._cursor.__exit__(*args)

        def execute(self, sql, params=None):
            if "FROM crawler_account_state" in sql and "FOR UPDATE" in sql:
                waiting_on_account.set()
            return self._cursor.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._cursor, name)

    class TracedConnection:
        def __init__(self, connection) -> None:
            self._connection = connection

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, *args):
            return self._connection.__exit__(*args)

        def cursor(self):
            return TracedCursor(self._connection.cursor())

    def finish_after_wait() -> None:
        delayed = JobRepository(
            TEST_DATABASE_URL,
            statement_timeout_seconds=1,
            connect_factory=lambda _dsn: TracedConnection(psycopg.connect(TEST_DATABASE_URL)),
        )
        result.append(delayed.finish_job(job.id, "owner", status=JobStatus.SUCCEEDED))

    with psycopg.connect(TEST_DATABASE_URL) as holder:
        with holder.cursor() as cursor:
            cursor.execute("SELECT account_key FROM crawler_account_state WHERE account_key = 'default' FOR UPDATE")
            thread = Thread(target=finish_after_wait)
            thread.start()
            assert waiting_on_account.wait(timeout=1)
            cursor.execute("SELECT pg_sleep(0.25)")
    thread.join(timeout=2)

    assert result == [False]
    assert repository.get_job(job.id).status is JobStatus.RUNNING  # type: ignore[union-attr]
    assert repository.list_targets(job.id).items[0].status is TargetStatus.PENDING
    assert repository.get_account("default").status is AccountStatus.READY  # type: ignore[union-attr]


def test_stale_worker_cannot_apply_an_atomic_account_signal_or_terminal_target_status() -> None:
    """Break caught: a worker that lost ownership still places the account into review."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(
        command("https://www.facebook.com/groups/100"),
        idempotency_key="atomic-stale-worker",
        request_fingerprint="atomic-stale-worker",
    )
    claim = repository.claim_next("owner", lease_duration=timedelta(minutes=2))
    assert claim is not None
    target = claim.targets[0]
    assert repository.start_target(job.id, target.id, "owner")

    assert not repository.finish_job(
        job.id,
        "stale-worker",
        status=JobStatus.BLOCKED,
        active_target_id=target.id,
        active_target_status=TargetStatus.BLOCKED,
        account_signal="facebook_rate_limited",
    )

    assert repository.get_job(job.id).status is JobStatus.RUNNING  # type: ignore[union-attr]
    assert repository.list_targets(job.id).items[0].status is TargetStatus.RUNNING
    account = repository.get_account("default")
    assert account is not None and account.status is AccountStatus.READY


def test_target_progress_and_atomic_finalizer_store_aggregate_counters_and_stricter_cooldowns() -> None:
    """Break caught: job summaries/cooldowns ignore durable target work or worker policy."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(
        command("https://www.facebook.com/groups/100"),
        idempotency_key="atomic-counters",
        request_fingerprint="atomic-counters",
    )
    claim = repository.claim_next("worker-one", lease_duration=timedelta(minutes=2))
    assert claim is not None
    target = claim.targets[0]
    assert repository.start_target(job.id, target.id, "worker-one")
    assert repository.update_target_progress(
        job.id,
        target.id,
        "worker-one",
        steps_completed=4,
        items_discovered=5,
        users_persisted=3,
        provider_retries_required=2,
    )
    progress = repository.get_job(job.id)
    assert progress is not None
    assert (progress.discovered_users, progress.persisted_users, progress.provider_retries_required) == (5, 3, 2)

    policy_now = datetime.now(UTC)
    assert repository.finish_job(
        job.id,
        "worker-one",
        status=JobStatus.SUCCEEDED,
        active_target_id=target.id,
        active_target_status=TargetStatus.SUCCEEDED,
        normal_cooldown=timedelta(hours=2),
        now=policy_now,
    )
    normal = repository.get_account("default")
    assert normal is not None and normal.cooldown_until is not None
    assert normal.cooldown_until >= policy_now + timedelta(hours=2)

    repository.set_account_state(account_key="default", status=AccountStatus.READY, now=policy_now)
    limited, _ = repository.create_job(
        command("https://www.facebook.com/groups/200"),
        idempotency_key="atomic-rate-cooldown",
        request_fingerprint="atomic-rate-cooldown",
    )
    limited_claim = repository.claim_next("worker-two", lease_duration=timedelta(minutes=2))
    assert limited_claim is not None
    limited_target = limited_claim.targets[0]
    assert repository.start_target(limited.id, limited_target.id, "worker-two")
    assert repository.finish_job(
        limited.id,
        "worker-two",
        status=JobStatus.BLOCKED,
        active_target_id=limited_target.id,
        active_target_status=TargetStatus.BLOCKED,
        account_signal="facebook_rate_limited",
        rate_limit_cooldown=timedelta(hours=12),
        now=policy_now,
    )
    rate_limited = repository.get_account("default")
    assert rate_limited is not None and rate_limited.cooldown_until is not None
    assert rate_limited.cooldown_until >= policy_now + timedelta(hours=12)


def test_stale_recovery_requeues_unstarted_work_and_blocks_uncertain_work_once() -> None:
    """Break caught: an expired lease either loses safe work or resumes uncertain browser work."""
    repository = JobRepository(TEST_DATABASE_URL)
    safe, _ = repository.create_job(command("https://www.facebook.com/groups/100"), idempotency_key="safe-stale", request_fingerprint="safe-stale")
    uncertain, _ = repository.create_job(command("https://www.facebook.com/groups/200"), idempotency_key="uncertain-stale", request_fingerprint="uncertain-stale")
    safe_claim = repository.claim_next("worker-safe", lease_duration=timedelta(minutes=1))
    assert safe_claim is not None and safe_claim.job.id == safe.id
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE crawl_jobs SET lease_expires_at = now() - interval '1 minute' WHERE id = %s", (safe.id,))
    assert repository.recover_stale_jobs() == (safe.id,)
    assert repository.get_job(safe.id).status is JobStatus.QUEUED  # type: ignore[union-attr]
    assert repository.request_cancel(safe.id) is not None

    uncertain_claim = repository.claim_next("worker-uncertain", lease_duration=timedelta(minutes=1))
    assert uncertain_claim is not None and uncertain_claim.job.id == uncertain.id
    target = repository.list_targets(uncertain.id).items[0]
    assert repository.start_target(uncertain.id, target.id, "worker-uncertain")
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE crawl_jobs SET lease_expires_at = now() - interval '1 minute' WHERE id = %s", (uncertain.id,))
    assert repository.recover_stale_jobs() == (uncertain.id,)
    assert repository.get_job(uncertain.id).status is JobStatus.BLOCKED  # type: ignore[union-attr]
    assert repository.list_targets(uncertain.id).items[0].status is TargetStatus.BLOCKED
    assert repository.get_account("default").status is AccountStatus.MANUAL_REVIEW
    assert repository.recover_stale_jobs() == ()
    assert [event.event_type for event in repository.list_events(uncertain.id).items].count("account_warning") == 1


def test_stale_recovery_treats_browser_started_as_uncertain_before_target_start() -> None:
    """A browser crash after its durable event must never replay the account work."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(
        command(
            "https://www.facebook.com/groups/100",
            "https://www.facebook.com/groups/200",
        ),
        idempotency_key="stale-browser-started",
        request_fingerprint="stale-browser-started",
    )
    claim = repository.claim_next(
        "browser-worker",
        lease_duration=timedelta(minutes=1),
    )
    assert claim is not None
    repository.append_event(job.id, "browser_started", "info")
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE crawl_jobs SET lease_expires_at = now() - interval '1 minute' WHERE id = %s",
                (job.id,),
            )

    assert repository.recover_stale_jobs() == (job.id,)

    stored = repository.get_job(job.id)
    account = repository.get_account("default")
    events = repository.list_events(job.id).items
    assert stored is not None
    assert stored.status is JobStatus.BLOCKED
    assert stored.failed_targets == 2
    assert [item.status for item in repository.list_targets(job.id).items] == [
        TargetStatus.BLOCKED,
        TargetStatus.BLOCKED,
    ]
    assert account is not None
    assert account.status is AccountStatus.MANUAL_REVIEW
    assert account.last_finished_at is not None
    assert [item.event_type for item in events][-2:] == [
        "account_warning",
        "job_blocked",
    ]
    assert events[-2].safe_message == "Lease expired during target execution."
    assert events[-1].safe_message == ""


def test_stale_browser_recovery_is_single_winner_across_connections() -> None:
    """Concurrent recovery emits one warning/block sequence and returns one winner."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(
        command("https://www.facebook.com/groups/100"),
        idempotency_key="stale-browser-race",
        request_fingerprint="stale-browser-race",
    )
    assert repository.claim_next(
        "browser-race-worker",
        lease_duration=timedelta(minutes=1),
    ) is not None
    repository.append_event(job.id, "browser_started", "info")
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE crawl_jobs SET lease_expires_at = now() - interval '1 minute' WHERE id = %s",
                (job.id,),
            )

    barrier = Barrier(2)

    def recover() -> tuple[UUID, ...]:
        barrier.wait()
        return JobRepository(TEST_DATABASE_URL).recover_stale_jobs(limit=1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=10) for future in (
            executor.submit(recover),
            executor.submit(recover),
        )]

    assert sorted(len(item) for item in results) == [0, 1]
    event_types = [item.event_type for item in repository.list_events(job.id).items]
    assert event_types.count("account_warning") == 1
    assert event_types.count("job_blocked") == 1


def test_service_retry_preserves_retryable_checkpoint_and_account_policy_is_conservative() -> None:
    """Break caught: retries overwrite checkpoints or account safety holds are cleared too early."""
    repository = JobRepository(TEST_DATABASE_URL)
    now = datetime(2026, 8, 21, tzinfo=UTC)
    service = JobService(repository, now=lambda: now)
    parent, _ = service.create(
        command("https://www.facebook.com/groups/100"), idempotency_key="retry-parent"
    )
    claim = repository.claim_next("retry-worker", lease_duration=timedelta(minutes=2))
    assert claim is not None
    target = claim.targets[0]
    assert repository.start_target(parent.id, target.id, "retry-worker")
    assert repository.finish_target(parent.id, target.id, "retry-worker", status=TargetStatus.FAILED)
    assert repository.finish_job(parent.id, "retry-worker", status=JobStatus.FAILED)
    repository.set_account_state(account_key="default", status=AccountStatus.READY, now=now)

    child = service.retry(parent.id)
    repeated = service.retry(parent.id)

    assert child.id != parent.id
    assert child.retry_of_job_id == parent.id
    assert repeated.id == child.id
    assert repository.list_targets(child.id).items[0].checkpoint_path == target.checkpoint_path
    assert repository.get_job(parent.id).status is JobStatus.FAILED  # type: ignore[union-attr]
    first_rate_limit = service.record_account_signal(signal="facebook_rate_limited")
    second_rate_limit = service.record_account_signal(signal="facebook_rate_limited")
    assert first_rate_limit.status is AccountStatus.COOLDOWN
    assert first_rate_limit.rate_limit_count_24h == 1
    assert first_rate_limit.cooldown_until is not None and first_rate_limit.cooldown_until >= now + timedelta(hours=6)
    assert second_rate_limit.status is AccountStatus.MANUAL_REVIEW
    assert service.acknowledge_account(acknowledged=True).status is AccountStatus.COOLDOWN
    assert service.record_account_signal(signal=SafetyCode.SESSION_EXPIRED).status is AccountStatus.BLOCKED


def test_claim_checks_active_job_before_selecting_another_queued_job() -> None:
    """Break caught: a second claim hits the unique index instead of reporting no capacity."""
    repository = JobRepository(TEST_DATABASE_URL)
    first, _ = repository.create_job(command("https://www.facebook.com/groups/100"), idempotency_key="active-first", request_fingerprint="active-first")
    second, _ = repository.create_job(command("https://www.facebook.com/groups/200"), idempotency_key="active-second", request_fingerprint="active-second")
    assert repository.claim_next("first-worker", lease_duration=timedelta(minutes=2)).job.id == first.id  # type: ignore[union-attr]

    assert repository.claim_next("second-worker", lease_duration=timedelta(minutes=2)) is None
    assert repository.get_job(second.id).status is JobStatus.QUEUED  # type: ignore[union-attr]


def test_partial_closure_marks_untouched_targets_partial_and_retry_copies_them() -> None:
    """Break caught: partial closure hides untouched work as skipped and drops it from retry."""
    repository = JobRepository(TEST_DATABASE_URL)
    parent, _ = repository.create_job(
        command("https://www.facebook.com/groups/100", "https://www.facebook.com/groups/200"),
        idempotency_key="partial-parent", request_fingerprint="partial-parent",
    )
    claim = repository.claim_next("partial-worker", lease_duration=timedelta(minutes=2))
    assert claim is not None
    assert repository.start_target(parent.id, claim.targets[0].id, "partial-worker")
    assert repository.finish_target(parent.id, claim.targets[0].id, "partial-worker", status=TargetStatus.SUCCEEDED)
    assert repository.finish_job(parent.id, "partial-worker", status=JobStatus.PARTIAL)
    assert [target.status for target in repository.list_targets(parent.id).items] == [TargetStatus.SUCCEEDED, TargetStatus.PARTIAL]
    assert repository.get_job(parent.id).failed_targets == 1  # type: ignore[union-attr]
    repository.set_account_state(account_key="default", status=AccountStatus.READY, now=datetime.now(UTC))
    child = JobService(repository).retry(parent.id)
    assert [target.target_key for target in repository.list_targets(child.id).items] == [claim.targets[1].target_key]
    child_claim = repository.claim_next("child-worker", lease_duration=timedelta(minutes=2))
    assert child_claim is not None and child_claim.job.id == child.id
    assert repository.start_target(child.id, child_claim.targets[0].id, "child-worker")
    assert repository.finish_target(child.id, child_claim.targets[0].id, "child-worker", status=TargetStatus.FAILED)
    assert repository.finish_job(child.id, "child-worker", status=JobStatus.FAILED)
    repository.set_account_state(account_key="default", status=AccountStatus.READY, now=datetime.now(UTC))
    assert JobService(repository).retry(child.id).retry_of_job_id == child.id


def test_stale_recovery_blocks_a_terminal_attempted_target() -> None:
    """Break caught: a stale attempted target remains terminal-but-untrusted after recovery."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(command("https://www.facebook.com/groups/100"), idempotency_key="stale-terminal", request_fingerprint="stale-terminal")
    claim = repository.claim_next("stale-worker", lease_duration=timedelta(minutes=2))
    assert claim is not None
    target = claim.targets[0]
    assert repository.start_target(job.id, target.id, "stale-worker")
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE crawl_targets SET status = 'failed', finished_at = now() WHERE id = %s", (target.id,))
            cursor.execute("UPDATE crawl_jobs SET lease_expires_at = now() - interval '1 minute' WHERE id = %s", (job.id,))
    assert repository.recover_stale_jobs() == (job.id,)
    assert repository.list_targets(job.id).items[0].status is TargetStatus.BLOCKED


def test_atomic_account_policy_keeps_stronger_holds_and_resets_old_rate_history() -> None:
    """Break caught: separated account reads overwrite safety holds or retain stale rate-limit history."""
    repository = JobRepository(TEST_DATABASE_URL)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    service = JobService(repository, now=lambda: clock[0])
    assert service.record_account_signal(signal="facebook_rate_limited").status is AccountStatus.COOLDOWN
    clock[0] += timedelta(hours=6)
    assert service.record_account_signal(signal="facebook_rate_limited").status is AccountStatus.MANUAL_REVIEW
    assert service.record_account_signal(signal=SafetyCode.CHECKPOINT).status is AccountStatus.MANUAL_REVIEW
    assert service.record_account_signal(signal=SafetyCode.SESSION_EXPIRED).status is AccountStatus.BLOCKED
    assert service.record_account_signal(signal="facebook_rate_limited").status is AccountStatus.BLOCKED

    repository.set_account_state(
        account_key="default", status=AccountStatus.READY, now=clock[0],
        rate_limit_count_24h=9, last_rate_limit_at=clock[0] - timedelta(hours=24, seconds=1),
    )
    reset = service.record_account_signal(signal="facebook_rate_limited")
    assert reset.status is AccountStatus.COOLDOWN and reset.rate_limit_count_24h == 1
    repository.set_account_state(account_key="default", status=AccountStatus.COOLDOWN, cooldown_until=clock[0], now=clock[0])
    assert service.get_account().status is AccountStatus.READY
    repository.set_account_state(account_key="default", status=AccountStatus.MANUAL_REVIEW, now=clock[0])
    assert service.acknowledge_account(acknowledged=True).status is AccountStatus.READY


def test_repeated_rate_limit_renews_six_hour_cooldown_before_acknowledgement() -> None:
    """Break caught: the second signal enters review with an expired cooldown."""
    repository = JobRepository(TEST_DATABASE_URL)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    service = JobService(repository, now=lambda: clock[0])

    first = service.record_account_signal(signal="facebook_rate_limited")
    clock[0] += timedelta(hours=1)
    second = service.record_account_signal(signal="facebook_rate_limited")
    acknowledged = service.acknowledge_account(acknowledged=True)

    assert first.cooldown_until == datetime(2026, 8, 21, 6, tzinfo=UTC)
    assert second.status is AccountStatus.MANUAL_REVIEW
    assert second.cooldown_until is not None
    assert second.cooldown_until >= clock[0] + timedelta(hours=6)
    assert acknowledged.status is AccountStatus.COOLDOWN
    assert acknowledged.cooldown_until == second.cooldown_until


def test_blocked_finalization_records_finished_time_and_rate_limit_event() -> None:
    """Break caught: blocked work omits account completion metadata and rate-limit audit."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(
        command("https://www.facebook.com/groups/100"),
        idempotency_key="blocked-finished-metadata",
        request_fingerprint="blocked-finished-metadata",
    )
    claim = repository.claim_next("blocked-worker", lease_duration=timedelta(minutes=2))
    assert claim is not None

    assert repository.finish_job(
        job.id,
        "blocked-worker",
        status=JobStatus.BLOCKED,
        account_signal="facebook_rate_limited",
    )

    account = repository.get_account()
    events = repository.list_events(job.id).items
    assert account is not None and account.last_finished_at is not None
    assert [event.event_type for event in events][-2:] == [
        "facebook_rate_limited",
        "job_blocked",
    ]


def test_two_real_connections_race_to_claim_only_one_default_account_job() -> None:
    """Break caught: concurrent claimers both receive work for the one supported account."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(command("https://www.facebook.com/groups/100"), idempotency_key="race-claim", request_fingerprint="race-claim")
    queued, _ = repository.create_job(command("https://www.facebook.com/groups/200"), idempotency_key="race-queued", request_fingerprint="race-queued")
    barrier = Barrier(2)

    def claim(worker_id: str):
        barrier.wait()
        return JobRepository(TEST_DATABASE_URL).claim_next(worker_id, lease_duration=timedelta(minutes=2))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim, worker_id) for worker_id in ("race-one", "race-two")]
        results = [future.result(timeout=10) for future in futures]

    claimed = [item for item in results if item is not None]
    empty = [item for item in results if item is None]
    assert len(results) == 2
    assert len(claimed) == 1
    assert len(empty) == 1
    assert claimed[0].job.id == job.id  # type: ignore[union-attr]
    stored = repository.get_job(job.id)
    assert stored is not None and stored.attempt == 1
    assert repository.get_job(queued.id).status is JobStatus.QUEUED  # type: ignore[union-attr]


@pytest.mark.parametrize("account_status", [AccountStatus.MANUAL_REVIEW, AccountStatus.BLOCKED])
def test_finish_job_preserves_stronger_account_hold_but_records_finished_metadata(account_status: AccountStatus) -> None:
    """Break caught: normal completion leaves a preserved safety hold without completion metadata."""
    repository = JobRepository(TEST_DATABASE_URL)
    job, _ = repository.create_job(command("https://www.facebook.com/groups/100"), idempotency_key=f"held-finish-{account_status.value}", request_fingerprint=f"held-finish-{account_status.value}")
    claim = repository.claim_next("held-worker", lease_duration=timedelta(minutes=2))
    assert claim is not None
    hold = datetime.now(UTC) + timedelta(hours=4)
    repository.set_account_state(account_key="default", status=account_status, cooldown_until=hold, now=datetime.now(UTC))

    assert repository.finish_job(job.id, "held-worker", status=JobStatus.SUCCEEDED)

    account = repository.get_account("default")
    assert account is not None and account.status is account_status
    assert account.cooldown_until == hold
    assert account.last_finished_at is not None


def test_retry_copies_every_retryable_terminal_status_and_reuses_checkpoints() -> None:
    """Break caught: retry lineage omits cancelled/blocked work or recreates a server checkpoint."""
    repository = JobRepository(TEST_DATABASE_URL)
    parent, _ = repository.create_job(
        command(*[f"https://www.facebook.com/groups/{index}" for index in range(100, 106)]),
        idempotency_key="retry-matrix", request_fingerprint="retry-matrix",
    )
    expected = [TargetStatus.FAILED, TargetStatus.PARTIAL, TargetStatus.CANCELLED, TargetStatus.BLOCKED]
    all_targets = repository.list_targets(parent.id).items
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE crawl_jobs SET status = 'failed', finished_at = now() WHERE id = %s", (parent.id,))
            for target, status in zip(all_targets, [*expected, TargetStatus.SUCCEEDED, TargetStatus.SKIPPED], strict=True):
                cursor.execute("UPDATE crawl_targets SET status = %s, finished_at = now() WHERE id = %s", (status.value, target.id))

    child = JobService(repository).retry(parent.id)
    copied = repository.list_targets(child.id).items
    assert [item.status for item in copied] == [TargetStatus.PENDING] * 4
    assert [item.target_key for item in copied] == [item.target_key for item in all_targets[:4]]
    assert [item.checkpoint_path for item in copied] == [item.checkpoint_path for item in all_targets[:4]]


def test_retry_rechecks_account_under_its_atomic_child_transaction() -> None:
    """Break caught: a safety signal between service readiness and child insert still creates work."""
    class SignalBetweenReadAndRetryRepository(JobRepository):
        def refresh_account(self, account_key: str, *, now: datetime):
            account = super().refresh_account(account_key, now=now)
            super().apply_account_signal(account_key, SafetyCode.CHECKPOINT.value, now=now)
            return account

    repository = SignalBetweenReadAndRetryRepository(TEST_DATABASE_URL)
    parent, _ = repository.create_job(command("https://www.facebook.com/groups/100"), idempotency_key="retry-recheck", request_fingerprint="retry-recheck")
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE crawl_jobs SET status = 'failed', finished_at = now() WHERE id = %s", (parent.id,))
            cursor.execute("UPDATE crawl_targets SET status = 'failed', finished_at = now() WHERE job_id = %s", (parent.id,))

    with pytest.raises(JobConflict):
        JobService(repository).retry(parent.id)
    assert repository.get_retry_child(parent.id) is None
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM crawl_jobs WHERE retry_of_job_id = %s", (parent.id,))
            assert cursor.fetchone() == (0,)


def test_cooldown_refreshes_only_at_or_after_its_expiry_boundary() -> None:
    """Break caught: a job claim/service read releases an ordinary cooldown too early."""
    repository = JobRepository(TEST_DATABASE_URL)
    now = datetime(2026, 8, 21, tzinfo=UTC)
    service = JobService(repository, now=lambda: now)
    repository.set_account_state(account_key="default", status=AccountStatus.COOLDOWN, cooldown_until=now + timedelta(microseconds=1), now=now)
    assert service.get_account().status is AccountStatus.COOLDOWN
    now += timedelta(microseconds=1)
    assert service.get_account().status is AccountStatus.READY
