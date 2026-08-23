from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import os
from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from fb_crawl.adapters.browser.account_safety import SafetySignal
from fb_crawl.core.jobs import (
    AccountStatus,
    JobConflict,
    JobCreateCommand,
    JobStatus,
    SafeJobOptions,
    SafetyCode,
    TargetStatus,
    canonical_job_target,
)
from fb_crawl.core.models import (
    AuthenticatedAction,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_crawl.services.execution_control import AccountSafetyStop
from fb_crawl.services.jobs import JobService
from fb_crawl.services.worker import CrawlWorker
from fb_data_pipeline.core.models import (
    PhoneEvidence,
    ProviderResult,
    ProviderStatus,
)
from fb_data_pipeline.repositories.jobs import JobRepository
from fb_data_pipeline.repositories.migrations import MigrationRunner
from fb_data_pipeline.repositories.postgres import PostgresRepository
from fb_data_pipeline.repositories.users import UserQuery, UserQueryRepository
from fb_data_pipeline.services.ingestion import AuthenticatedIngestionService
from fb_data_pipeline.services.persistence import PipelinePersistenceService
from fb_data_pipeline.services.pipeline import EnrichmentPipeline


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
API_KEY = "e" * 32
TARGET = "https://www.facebook.com/groups/123/members"

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL
    or TEST_DATABASE_URL
    != "postgresql://fb_pipeline:fb_pipeline_dev@127.0.0.1:5432/fb_pipeline_test",
    reason="TEST_DATABASE_URL must be the dedicated local fb_pipeline_test URL",
)


def _assert_dedicated_test_database() -> None:
    assert TEST_DATABASE_URL.endswith("/fb_pipeline_test")
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        assert connection.info.dbname == "fb_pipeline_test"


@pytest.fixture(autouse=True)
def clean_database() -> None:
    _assert_dedicated_test_database()
    MigrationRunner(TEST_DATABASE_URL).apply()
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM crawl_jobs")
            cursor.execute(
                """
                TRUNCATE TABLE
                    enrichment_attempts,
                    user_phone_evidence,
                    facebook_user_profiles,
                    phone_numbers,
                    facebook_users
                RESTART IDENTITY
                """
            )
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


def _command() -> JobCreateCommand:
    return JobCreateCommand(
        AuthenticatedAction.MEMBERS,
        (canonical_job_target(AuthenticatedAction.MEMBERS, TARGET),),
        SafeJobOptions(),
    )


def _result() -> ScrapeResult[UserRecord]:
    return ScrapeResult(
        records=(
            UserRecord(
                user_id="100013347102233",
                username="thang.duc.961556",
                name="Bui Duc Thang",
                profile_url="https://www.facebook.com/thang.duc.961556",
                source="members",
                source_url=TARGET,
                phone_numbers=("0912 222 333",),
                phone_sources=("profile:contact",),
                address="Ha Noi",
                birth_date="1990-04-12",
                gender="male",
                last_seen="2026-08-22T12:00:00+00:00",
            ),
        ),
        issues=(),
        stats=ScrapeStats(requested=1, discovered=1, succeeded=1, failed=0),
    )


class FakeProvider:
    name = "fbnumber"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, identity) -> ProviderResult:
        self.calls.append(identity.uid)
        return ProviderResult(
            provider="fbnumber",
            status=ProviderStatus.FOUND,
            evidence=(
                PhoneEvidence(
                    phone_number="+84 901 111 222",
                    normalized_phone="+84901111222",
                    source="external:fbnumber",
                    source_url=identity.profile_url,
                    provider="fbnumber",
                    confidence="provider",
                    captured_at=datetime(2026, 8, 22, 12, tzinfo=UTC),
                ),
            ),
            checked_at=datetime(2026, 8, 22, 12, tzinfo=UTC),
        )

    def close(self) -> None:
        return None


class FakeSession:
    def __init__(
        self,
        ingestion: AuthenticatedIngestionService,
        events: list[str],
        *,
        failure: BaseException | None = None,
    ) -> None:
        self.ingestion = ingestion
        self.events = events
        self.failure = failure

    def validate(self, request) -> None:
        assert request.targets == (TARGET,)
        self.events.append("validate")

    def run(self, _request) -> ScrapeResult[UserRecord]:
        self.events.append("run")
        if self.failure is not None:
            raise self.failure
        return _result()

    def ingest(self, result: ScrapeResult[UserRecord]):
        self.events.append("ingest")
        return self.ingestion.ingest(result)


def _runtime(session: FakeSession, events: list[str]):
    @contextmanager
    def open_runtime(_control, _pacer):
        events.append("runtime_open")
        try:
            yield session
        finally:
            events.append("runtime_close")

    return open_runtime


def _ingestion(provider: FakeProvider) -> AuthenticatedIngestionService:
    return AuthenticatedIngestionService(
        EnrichmentPipeline(provider),
        PipelinePersistenceService(PostgresRepository(TEST_DATABASE_URL)),
    )


def test_readiness_checks_real_migration_003_without_applying_unknown_version() -> None:
    """Break caught: API readiness ignores the dedicated database migration state."""
    runner = MigrationRunner(TEST_DATABASE_URL)

    assert runner.is_applied("003_job_orchestration") is True
    assert runner.is_applied("999_not_applied") is False


try:
    import fastapi  # noqa: F401
    import pydantic  # noqa: F401
except ModuleNotFoundError as error:
    if error.name not in {"fastapi", "pydantic"}:
        raise
    FASTAPI_AVAILABLE = False
else:
    FASTAPI_AVAILABLE = True

if FASTAPI_AVAILABLE:
    from fastapi.testclient import TestClient

    from fb_crawl.api.app import create_app
    from fb_crawl.api.config import ApiSettings


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_http_queue_worker_and_http_reads_share_one_postgres_source_of_truth() -> None:
    """Break caught: HTTP queue/read surfaces diverge from worker persistence."""
    repository = JobRepository(TEST_DATABASE_URL)
    user_repository = UserQueryRepository(TEST_DATABASE_URL)
    provider = FakeProvider()
    lifecycle: list[str] = []
    worker = CrawlWorker(
        repository,
        _runtime(FakeSession(_ingestion(provider), lifecycle), lifecycle),
        worker_id="task14-http-worker",
    )
    application = create_app(
        ApiSettings(api_key=API_KEY),
        JobService(repository),
        repository,
        user_repository,
        MigrationRunner(TEST_DATABASE_URL).is_applied,
    )
    client = TestClient(application)
    headers = {"X-API-Key": API_KEY}
    output_root = Path("runtime/output")
    before_outputs = (
        {path.resolve() for path in output_root.glob("*")}
        if output_root.exists()
        else set()
    )

    response = client.post(
        "/api/v1/jobs",
        headers={**headers, "Idempotency-Key": "task14-http-create"},
        json={
            "mode": "authenticated",
            "action": "members",
            "targets": [TARGET],
            "options": {},
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    stored = repository.get_job(UUID(response.json()["id"]))
    assert stored is not None
    assert stored.status is JobStatus.QUEUED
    assert stored.started_at is None
    assert stored.worker_id is None
    assert lifecycle == []

    assert worker.run_once() is True

    job_id = response.json()["id"]
    job_response = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
    target_response = client.get(
        f"/api/v1/jobs/{job_id}/targets",
        headers=headers,
    )
    event_response = client.get(
        f"/api/v1/jobs/{job_id}/events?after_id=0",
        headers=headers,
    )
    user_response = client.get(
        "/api/v1/users?uid=100013347102233",
        headers=headers,
    )

    assert job_response.status_code == 200
    assert job_response.json()["status"] == "succeeded"
    assert job_response.json()["completed_targets"] == 1
    assert job_response.json()["persisted_users"] == 1
    assert target_response.status_code == 200
    target_payload = target_response.json()["items"]
    assert len(target_payload) == 1
    assert target_payload[0]["status"] == "succeeded"
    assert target_payload[0]["items_discovered"] == 1
    assert target_payload[0]["users_persisted"] == 1
    assert event_response.status_code == 200
    events = event_response.json()["items"]
    assert [event["id"] for event in events] == sorted(
        event["id"] for event in events
    )
    assert [event["event_type"] for event in events] == [
        "job_created",
        "job_claimed",
        "browser_started",
        "target_started",
        "provider_progress",
        "target_progress",
        "target_completed",
        "job_completed",
    ]
    assert user_response.status_code == 200
    users = user_response.json()["items"]
    assert len(users) == 1
    assert users[0]["profile_url"] == (
        "https://www.facebook.com/thang.duc.961556"
    )
    assert (users[0]["phone_1"], users[0]["phone_2"]) == (
        "+84901111222",
        "+84912222333",
    )
    assert lifecycle == ["runtime_open", "validate", "run", "ingest", "runtime_close"]
    assert provider.calls == ["100013347102233"]
    after_outputs = (
        {path.resolve() for path in output_root.glob("*")}
        if output_root.exists()
        else set()
    )
    assert after_outputs == before_outputs


def test_real_queue_worker_and_postgres_pipeline_persist_complete_user(
) -> None:
    """Break caught: queue-to-worker flow skips events, phones/profile, or writes CSV."""
    output_root = Path("runtime/output")
    before_outputs = (
        {path.resolve() for path in output_root.glob("*")}
        if output_root.exists()
        else set()
    )
    job_repository = JobRepository(TEST_DATABASE_URL)
    job_service = JobService(job_repository)
    provider = FakeProvider()
    lifecycle: list[str] = []
    session = FakeSession(_ingestion(provider), lifecycle)
    worker = CrawlWorker(
        job_repository,
        _runtime(session, lifecycle),
        worker_id="task14-worker",
    )

    job, created = job_service.create(
        _command(),
        idempotency_key="task14-service-worker-success",
    )

    assert created is True
    assert job.status is JobStatus.QUEUED
    assert lifecycle == []
    assert worker.run_once() is True

    stored = job_repository.get_job(job.id)
    assert stored is not None
    assert stored.status is JobStatus.SUCCEEDED
    assert (stored.requested_targets, stored.completed_targets, stored.failed_targets) == (
        1,
        1,
        0,
    )
    assert (stored.discovered_users, stored.persisted_users) == (1, 1)
    target = job_repository.list_targets(job.id).items[0]
    assert target.status is TargetStatus.SUCCEEDED
    assert (target.items_discovered, target.users_persisted) == (1, 1)
    assert [event.event_type for event in job_repository.list_events(job.id).items] == [
        "job_created",
        "job_claimed",
        "browser_started",
        "target_started",
        "provider_progress",
        "target_progress",
        "target_completed",
        "job_completed",
    ]
    assert lifecycle == ["runtime_open", "validate", "run", "ingest", "runtime_close"]
    assert provider.calls == ["100013347102233"]

    users = UserQueryRepository(TEST_DATABASE_URL).list_users(
        UserQuery(uid="100013347102233")
    ).items
    assert len(users) == 1
    user = users[0]
    assert (user.facebook_uid, user.username, user.name) == (
        "100013347102233",
        "thang.duc.961556",
        "Bui Duc Thang",
    )
    assert (user.phone_1, user.phone_2) == ("+84901111222", "+84912222333")
    assert (user.address, user.birth_date, user.gender) == (
        "Ha Noi",
        "1990-04-12",
        "male",
    )
    after_outputs = (
        {path.resolve() for path in output_root.glob("*")}
        if output_root.exists()
        else set()
    )
    assert after_outputs == before_outputs


def test_preclaim_cancellation_never_opens_runtime_or_calls_provider() -> None:
    """Break caught: a queued cancellation still claims browser/provider work."""
    repository = JobRepository(TEST_DATABASE_URL)
    service = JobService(repository)
    provider = FakeProvider()
    lifecycle: list[str] = []
    worker = CrawlWorker(
        repository,
        _runtime(FakeSession(_ingestion(provider), lifecycle), lifecycle),
        worker_id="task14-cancel-worker",
    )
    job, _ = service.create(_command(), idempotency_key="task14-preclaim-cancel")

    cancelled = service.cancel(job.id)

    assert cancelled.status is JobStatus.CANCELLED
    assert worker.run_once() is False
    assert lifecycle == []
    assert provider.calls == []
    assert repository.list_targets(job.id).items[0].status is TargetStatus.CANCELLED


def test_account_warning_blocks_job_queue_and_provider_until_acknowledged() -> None:
    """Break caught: account warnings permit another claim/provider call or retry."""
    repository = JobRepository(TEST_DATABASE_URL)
    service = JobService(repository)
    provider = FakeProvider()
    lifecycle: list[str] = []
    warning = AccountSafetyStop(
        SafetySignal(
            SafetyCode.CAPTCHA,
            "Facebook CAPTCHA requires manual review.",
            True,
        )
    )
    blocked_worker = CrawlWorker(
        repository,
        _runtime(
            FakeSession(_ingestion(provider), lifecycle, failure=warning),
            lifecycle,
        ),
        worker_id="task14-block-worker",
    )
    blocked_job, _ = service.create(_command(), idempotency_key="task14-blocked")

    assert blocked_worker.run_once() is True
    stored_block = repository.get_job(blocked_job.id)
    assert stored_block is not None
    assert stored_block.status is JobStatus.BLOCKED
    assert stored_block.error_code == "captcha"
    stored_target = repository.list_targets(blocked_job.id).items[0]
    assert stored_target.status is TargetStatus.BLOCKED
    assert stored_target.error_code == "captcha"
    account = service.get_account()
    assert account.status is AccountStatus.MANUAL_REVIEW
    assert account.last_warning_code == "captcha"
    assert lifecycle == ["runtime_open", "validate", "run", "runtime_close"]
    assert provider.calls == []

    queued, _ = service.create(_command(), idempotency_key="task14-queued-after-block")
    held_worker = CrawlWorker(
        repository,
        lambda *_args: (_ for _ in ()).throw(AssertionError("runtime opened")),
        worker_id="task14-held-worker",
    )
    assert held_worker.run_once() is False
    assert repository.get_job(queued.id).status is JobStatus.QUEUED
    assert provider.calls == []

    with pytest.raises(JobConflict, match="not ready"):
        service.retry(blocked_job.id)

    service.cancel(queued.id)
    acknowledged = service.acknowledge_account(acknowledged=True)
    assert acknowledged.status is AccountStatus.READY
    retry = service.retry(blocked_job.id)
    assert retry.status is JobStatus.QUEUED
    assert retry.retry_of_job_id == blocked_job.id
