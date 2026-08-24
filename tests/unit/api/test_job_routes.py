from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.jobs import (
    AccountStatus,
    CrawlEvent,
    CrawlJob,
    CrawlTarget,
    CrawlerAccountState,
    IdempotencyConflict,
    JobConflict,
    JobCreateCommand,
    JobNotFound,
    JobStatus,
    Page,
    SafeJobOptions,
    TargetStatus,
)
from fb_crawl.core.models import AuthenticatedAction
from fb_crawl.services.jobs import JobService


API_KEY = "j" * 32
IDEMPOTENCY_KEY = "create-members-001"
ROOT = Path(__file__).parents[3]
SCHEMAS_PATH = ROOT / "src" / "fb_crawl" / "api" / "schemas.py"
JOBS_ROUTE_PATH = ROOT / "src" / "fb_crawl" / "api" / "routes" / "jobs.py"

try:
    import fastapi
    import pydantic
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


def _headers(*, idempotency_key: str | None = None) -> dict[str, str]:
    values = {"X-API-Key": API_KEY}
    if idempotency_key is not None:
        values["Idempotency-Key"] = idempotency_key
    return values


def _body(**updates: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "mode": "authenticated",
        "action": "members",
        "targets": ["https://www.facebook.com/groups/123/members"],
        "options": {
            "steps": 10,
            "max_duration_seconds": 300,
            "navigation_delay_seconds": 8,
        },
    }
    body.update(updates)
    return body


def _job(
    *,
    job_id: UUID | None = None,
    status: JobStatus = JobStatus.QUEUED,
    retry_of_job_id: UUID | None = None,
    **updates: Any,
) -> CrawlJob:
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    job = CrawlJob(
        id=job_id or uuid4(),
        action=AuthenticatedAction.MEMBERS,
        status=status,
        request_options=SafeJobOptions(),
        request_fingerprint="do-not-expose-fingerprint",
        idempotency_key="do-not-expose-idempotency-key",
        retry_of_job_id=retry_of_job_id,
        worker_id="do-not-expose-worker-id",
        lease_expires_at=now,
        heartbeat_at=now,
        created_at=now,
        updated_at=now,
        requested_targets=1,
    )
    return replace(job, **updates)


def _target(job_id: UUID) -> CrawlTarget:
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    return CrawlTarget(
        id=uuid4(),
        job_id=job_id,
        target_key="members:https://www.facebook.com/groups/123/members",
        target_url="https://www.facebook.com/groups/123/members",
        target_kind="group_members",
        position=0,
        status=TargetStatus.RUNNING,
        checkpoint_path="runtime/checkpoints/private-secret.json",
        created_at=now,
        updated_at=now,
        steps_completed=2,
        items_discovered=4,
    )


class FakeJobRepository:
    def __init__(self, jobs: tuple[CrawlJob, ...] = ()) -> None:
        self.jobs = {job.id: job for job in jobs}
        self.targets: dict[UUID, tuple[CrawlTarget, ...]] = {}
        self.events: dict[UUID, tuple[CrawlEvent, ...]] = {}
        self.event_list_calls: list[tuple[UUID, int, int]] = []

    def get_job(self, job_id: UUID) -> CrawlJob | None:
        return self.jobs.get(job_id)

    def list_jobs(self, *, limit: int, cursor: str | None) -> Page[CrawlJob]:
        if cursor == "not-opaque":
            raise ValidationError("Invalid job pagination cursor.")
        return Page(tuple(self.jobs.values())[:limit], "opaque-next" if cursor is None else None)

    def list_targets(
        self, job_id: UUID, *, limit: int, cursor: str | None,
    ) -> Page[CrawlTarget]:
        if cursor == "not-opaque":
            raise ValidationError("Invalid target pagination cursor.")
        return Page(self.targets.get(job_id, ())[:limit], "target-next" if cursor is None else None)

    def list_events(
        self, job_id: UUID, *, after_id: int, limit: int,
    ) -> Page[CrawlEvent]:
        self.event_list_calls.append((job_id, after_id, limit))
        values = tuple(event for event in self.events.get(job_id, ()) if event.id > after_id)
        return Page(values[:limit], str(values[limit - 1].id) if len(values) > limit else None)


class FakeJobService:
    def __init__(self, repository: FakeJobRepository) -> None:
        self.repository = repository
        self.created_by_key: dict[str, tuple[JobCreateCommand, CrawlJob]] = {}

    def create(
        self, command: JobCreateCommand, *, idempotency_key: str,
    ) -> tuple[CrawlJob, bool]:
        existing = self.created_by_key.get(idempotency_key)
        if existing is not None:
            old_command, old_job = existing
            if old_command.to_canonical_dict() != command.to_canonical_dict():
                raise IdempotencyConflict(
                    "The idempotency key was used for a different request."
                )
            return old_job, False
        job = _job()
        self.created_by_key[idempotency_key] = (command, job)
        self.repository.jobs[job.id] = job
        return job, True

    def cancel(self, job_id: UUID) -> CrawlJob:
        job = self.repository.jobs.get(job_id)
        if job is None:
            raise JobNotFound("Crawl job was not found.")
        cancelled = replace(job, status=JobStatus.CANCELLED)
        self.repository.jobs[job_id] = cancelled
        return cancelled

    def retry(self, job_id: UUID) -> CrawlJob:
        parent = self.repository.jobs.get(job_id)
        if parent is None:
            raise JobNotFound("Crawl job was not found.")
        if parent.status not in {JobStatus.FAILED, JobStatus.PARTIAL, JobStatus.CANCELLED}:
            raise JobConflict("Only terminal crawl jobs can be retried.")
        child = _job(retry_of_job_id=job_id)
        self.repository.jobs[child.id] = child
        return child


def _client(
    repository: FakeJobRepository | None = None,
    service: Any | None = None,
):
    repository = repository or FakeJobRepository()
    service = service or FakeJobService(repository)
    app = create_app(
        ApiSettings(api_key=API_KEY),
        job_service=service,
        job_repository=repository,
        user_repository=object(),
        readiness=lambda _migration: True,
    )
    return TestClient(app, raise_server_exceptions=False), repository, service


def test_domain_contract_rejects_unknown_and_unsafe_options_before_persistence() -> None:
    """Break caught: an HTTP option can bypass the safe typed job boundary."""

    unsafe_names = (
        "persist",
        "output",
        "session_path",
        "proxy",
        "password",
        "access_token",
        "raw_args",
        "cooldown_seconds",
    )
    for name in unsafe_names:
        with pytest.raises(ValidationError, match="Unsupported job option"):
            SafeJobOptions.from_mapping(
                AuthenticatedAction.MEMBERS,
                {name: "private-value"},
            )

    with pytest.raises(ValidationError, match="outside the permitted range"):
        SafeJobOptions.from_mapping(
            AuthenticatedAction.MEMBERS,
            {"navigation_delay_seconds": 7.99},
        )


def test_schema_source_declares_closed_create_and_response_models() -> None:
    """Break caught: request/response schemas become open bags of API data."""

    source = SCHEMAS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    classes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }

    assert {
        "JobCreateRequest",
        "EventCountersResponse",
        "JobResponse",
        "JobTargetResponse",
        "JobEventResponse",
        "JobPageResponse",
        "JobTargetPageResponse",
        "JobEventPageResponse",
    } <= classes.keys()
    assert 'ConfigDict(extra="forbid")' in source
    assert "max_length=2048" in source
    for counter_name in (
        "requested_targets",
        "completed_targets",
        "failed_targets",
        "discovered_users",
        "persisted_users",
        "provider_retries_required",
        "steps_completed",
        "items_discovered",
        "users_persisted",
    ):
        assert counter_name in source
    assert "checkpoint_path" not in source
    assert "worker_id" not in source
    assert "lease_expires_at" not in source
    assert "idempotency_key" not in source
    assert "request_fingerprint" not in source


def test_job_route_source_uses_typed_commands_and_whitelisted_serializers() -> None:
    """Break caught: routes pass request dictionaries or dataclasses through directly."""

    source = JOBS_ROUTE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

    assert any(
        isinstance(call.func, ast.Name) and call.func.id == "JobCreateCommand"
        for call in calls
    )
    assert any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "from_mapping"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "SafeJobOptions"
        for call in calls
    )
    assert "response_model=" in source
    assert "Depends(auth)" in source
    assert "le=9223372036854775807" in source
    assert "response_model_exclude_none" not in source
    assert "__dict__" not in source
    assert "asdict(" not in source


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_create_requires_auth_and_idempotency_and_returns_accepted_job() -> None:
    """Break caught: create is unauthenticated, non-idempotent, or synchronously reported."""

    client, _, _ = _client()

    assert client.post("/api/v1/jobs", json=_body()).status_code == 401
    assert (
        client.post("/api/v1/jobs", headers=_headers(), json=_body()).status_code
        == 400
    )
    response = client.post(
        "/api/v1/jobs",
        headers=_headers(idempotency_key=IDEMPOTENCY_KEY),
        json=_body(),
    )

    assert response.status_code == 202
    assert UUID(response.json()["id"])
    assert response.json()["status"] == "queued"
    assert response.json()["mode"] == "authenticated"


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_create_group_batches_queues_multiple_safe_members_jobs() -> None:
    """Break caught: full-group crawl cannot be split into durable safe batches."""

    client, _, service = _client()

    response = client.post(
        "/api/v1/jobs/group-batches",
        headers=_headers(idempotency_key="group-full-123"),
        json={
            "group_url": "https://www.facebook.com/groups/123",
            "batch_count": 3,
            "batch_size": 300,
            "batch_duration_seconds": 900,
            "navigation_delay_seconds": 20,
            "steps": 0,
            "call_fbnumber": True,
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["total_created"] == 3
    assert len(payload["items"]) == 3
    assert set(service.created_by_key) == {
        "group-full-123:batch:1",
        "group-full-123:batch:2",
        "group-full-123:batch:3",
    }
    first_command = service.created_by_key["group-full-123:batch:1"][0]
    assert first_command.action is AuthenticatedAction.MEMBERS
    assert first_command.options.max_users == 300
    assert first_command.options.max_duration_seconds == 900
    assert first_command.options.navigation_delay_seconds == 20
    assert first_command.options.steps == 0
    assert first_command.targets[0].target_url == (
        "https://www.facebook.com/groups/123/members"
    )


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
@pytest.mark.parametrize(
    "body",
    [
        _body(mode="public"),
        _body(action="crawl"),
        _body(action="messages"),
        _body(action="inspect"),
        _body(action="batch"),
        _body(targets=[]),
        _body(targets=[""]),
        _body(targets=["https://www.facebook.com/groups/123"] * 101),
        _body(output="private.csv"),
        _body(session_path="runtime/session.json"),
        _body(proxy="http://secret-proxy"),
        _body(password="private-password"),
        _body(raw_args=["--force"]),
        _body(options={"unknown": 1}),
        _body(options={"persist": True}),
        _body(options={"output": "private.csv"}),
        _body(options={"session_path": "runtime/private-session.json"}),
        _body(options={"proxy": "http://secret-proxy"}),
        _body(options={"access_token": "private-token"}),
        _body(options={"raw_args": ["--force"]}),
        _body(options={"navigation_delay_seconds": 1}),
        _body(options={"max_duration_seconds": 1801}),
    ],
)
def test_create_rejects_public_unknown_credential_and_unsafe_input(body: dict[str, Any]) -> None:
    """Break caught: HTTP clients can submit unsupported or account-risking inputs."""

    client, _, _ = _client()

    response = client.post(
        "/api/v1/jobs",
        headers=_headers(idempotency_key=IDEMPOTENCY_KEY),
        json=body,
    )

    assert response.status_code == 400
    assert "private" not in response.text
    assert "secret-proxy" not in response.text


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_oversized_target_is_rejected_before_job_service_call() -> None:
    """Break caught: an enormous target reaches canonicalization or persistence."""

    repository = FakeJobRepository()
    service = FakeJobService(repository)
    client, _, _ = _client(repository, service)
    oversized = "https://www.facebook.com/" + "x" * 1_000_000

    response = client.post(
        "/api/v1/jobs",
        headers=_headers(idempotency_key=IDEMPOTENCY_KEY),
        json=_body(targets=[oversized]),
    )

    assert response.status_code == 400
    assert service.created_by_key == {}
    assert oversized not in response.text


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_idempotency_replay_and_conflict_have_stable_semantics() -> None:
    """Break caught: replay duplicates work or accepts a different canonical request."""

    client, _, _ = _client()
    headers = _headers(idempotency_key=IDEMPOTENCY_KEY)

    first = client.post("/api/v1/jobs", headers=headers, json=_body())
    replay = client.post("/api/v1/jobs", headers=headers, json=_body())
    conflict = client.post(
        "/api/v1/jobs",
        headers=headers,
        json=_body(targets=["https://www.facebook.com/groups/456/members"]),
    )

    assert first.status_code == replay.status_code == 202
    assert first.json()["id"] == replay.json()["id"]
    assert conflict.status_code == 409
    assert conflict.json() == {
        "code": "job_idempotency_conflict",
        "message": "The idempotency key was used for a different request.",
    }


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_detail_cancel_and_retry_map_missing_and_state_conflicts() -> None:
    """Break caught: job state errors become 500s or retry mutates the parent job."""

    active = _job(status=JobStatus.RUNNING)
    failed = _job(status=JobStatus.FAILED)
    repository = FakeJobRepository((active, failed))
    client, _, _ = _client(repository)
    missing = uuid4()

    assert client.get(f"/api/v1/jobs/{missing}", headers=_headers()).status_code == 404
    assert client.post(f"/api/v1/jobs/{missing}/cancel", headers=_headers()).status_code == 404
    assert client.get(f"/api/v1/jobs/{missing}/targets", headers=_headers()).status_code == 404
    assert client.get(f"/api/v1/jobs/{missing}/events", headers=_headers()).status_code == 404

    retry_conflict = client.post(f"/api/v1/jobs/{active.id}/retry", headers=_headers())
    cancelled = client.post(f"/api/v1/jobs/{active.id}/cancel", headers=_headers())
    cancelled_again = client.post(f"/api/v1/jobs/{active.id}/cancel", headers=_headers())
    retried = client.post(f"/api/v1/jobs/{failed.id}/retry", headers=_headers())

    assert cancelled.status_code == cancelled_again.status_code == 202
    assert cancelled.json()["id"] == cancelled_again.json()["id"]
    assert retry_conflict.status_code == 409
    assert retried.status_code == 202
    assert retried.json()["retry_of_job_id"] == str(failed.id)
    assert retried.json()["id"] != str(failed.id)


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_retry_while_account_is_in_cooldown_returns_conflict() -> None:
    """Break caught: the HTTP retry path bypasses the account safety hold."""

    failed = _job(status=JobStatus.FAILED)
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)

    class CooldownRepository(FakeJobRepository):
        def refresh_account(self, account_key: str, *, now: datetime):
            assert account_key == "default"
            return CrawlerAccountState(
                account_key="default",
                status=AccountStatus.COOLDOWN,
                cooldown_until=datetime(2026, 8, 22, 18, tzinfo=UTC),
                created_at=now,
                updated_at=now,
            )

        def create_retry_child(self, _job_id: UUID):
            raise AssertionError("cooldown must stop retry creation")

    repository = CooldownRepository((failed,))
    service = JobService(repository, now=lambda: now)
    client, _, _ = _client(repository, service)

    response = client.post(f"/api/v1/jobs/{failed.id}/retry", headers=_headers())

    assert response.status_code == 409
    assert response.json()["code"] == "job_state_conflict"


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_list_detail_target_and_event_responses_expose_only_stable_fields() -> None:
    """Break caught: repository internals or checkpoint paths leak through serialization."""

    job = _job(error_code="safe_code", error_message="Safe message.")
    repository = FakeJobRepository((job,))
    repository.targets[job.id] = (_target(job.id),)
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    repository.events[job.id] = (
        CrawlEvent(
            124,
            job.id,
            "target_progress",
            "info",
            now,
            safe_message="Safe progress.",
            counters={
                "requested_targets": 1,
                "completed_targets": 1,
                "failed_targets": 0,
                "discovered_users": 4,
                "persisted_users": 3,
                "provider_retries_required": 1,
                "steps_completed": 2,
                "items_discovered": 4,
                "users_persisted": 3,
                "unknown_legacy_counter": 999,
                "credential_secret": 123,
            },
        ),
        CrawlEvent(125, job.id, "job_completed", "info", now),
    )
    client, _, _ = _client(repository)
    headers = _headers()

    jobs = client.get("/api/v1/jobs?limit=20", headers=headers)
    detail = client.get(f"/api/v1/jobs/{job.id}", headers=headers)
    targets = client.get(f"/api/v1/jobs/{job.id}/targets?limit=20", headers=headers)
    events = client.get(
        f"/api/v1/jobs/{job.id}/events?after_id=123&limit=100",
        headers=headers,
    )

    assert jobs.status_code == detail.status_code == targets.status_code == events.status_code == 200
    assert jobs.json()["next_cursor"] == "opaque-next"
    assert targets.json()["next_cursor"] == "target-next"
    assert [event["id"] for event in events.json()["items"]] == [124, 125]
    assert events.json()["next_cursor"] is None
    assert events.json()["items"][1]["target_id"] is None
    assert events.json()["items"][0]["counters"] == {
        "requested_targets": 1,
        "completed_targets": 1,
        "failed_targets": 0,
        "discovered_users": 4,
        "persisted_users": 3,
        "provider_retries_required": 1,
        "steps_completed": 2,
        "items_discovered": 4,
        "users_persisted": 3,
    }
    assert set(events.json()["items"][1]["counters"]) == {
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
    assert all(
        value is None
        for value in events.json()["items"][1]["counters"].values()
    )
    assert "unknown_legacy_counter" not in events.text
    assert "credential_secret" not in events.text
    combined = " ".join((jobs.text, detail.text, targets.text, events.text))
    for secret in (
        "checkpoint_path",
        "private-secret",
        "worker_id",
        "do-not-expose-worker-id",
        "lease_expires_at",
        "request_fingerprint",
        "do-not-expose-fingerprint",
        "idempotency_key",
        "do-not-expose-idempotency-key",
    ):
        assert secret not in combined


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/jobs?limit=0",
        "/api/v1/jobs?limit=101",
        "/api/v1/jobs?cursor=",
        "/api/v1/jobs?cursor=not-opaque",
        "/api/v1/jobs/not-a-uuid",
        f"/api/v1/jobs/{uuid4()}/targets?limit=101",
        f"/api/v1/jobs/{uuid4()}/events?after_id=-1",
        f"/api/v1/jobs/{uuid4()}/events?after_id=9223372036854775808",
        f"/api/v1/jobs/{uuid4()}/events?limit=101",
    ],
)
def test_job_read_routes_reject_invalid_limits_cursors_and_ids(path: str) -> None:
    """Break caught: unbounded or malformed reads reach PostgreSQL."""

    client, _, _ = _client()

    response = client.get(path, headers=_headers())

    assert response.status_code in {400, 404}


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_event_cursor_above_postgres_bigint_is_rejected_before_repository_call() -> None:
    """Break caught: an out-of-range after_id reaches a PostgreSQL BIGINT parameter."""

    job = _job()
    repository = FakeJobRepository((job,))
    client, _, _ = _client(repository)

    response = client.get(
        f"/api/v1/jobs/{job.id}/events?after_id=9223372036854775808",
        headers=_headers(),
    )

    assert response.status_code == 400
    assert repository.event_list_calls == []


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_openapi_contains_no_unsafe_create_fields() -> None:
    """Break caught: generated API documentation advertises secret or unsafe controls."""

    app = create_app(
        ApiSettings(api_key=API_KEY, docs_enabled=True),
        job_service=FakeJobService(FakeJobRepository()),
        job_repository=FakeJobRepository(),
        user_repository=object(),
        readiness=lambda _migration: True,
    )
    schema = TestClient(app).get("/openapi.json", headers=_headers()).json()
    assert "/api/v1/jobs" in schema["paths"]
    create_schema_text = str(schema["components"]["schemas"]["JobCreateRequest"])
    for unsafe_name in (
        "session_path",
        "proxy",
        "password",
        "credential",
        "access_token",
        "output_path",
        "raw_args",
        "persist",
    ):
        assert unsafe_name not in create_schema_text
