from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from fb_crawl.exports.models import ExportFormat, ExportJob, ExportStatus
from fb_crawl.exports.service import ExportService, ExportWorker


NOW = datetime(2026, 8, 31, 3, tzinfo=UTC)
JOB_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def job() -> ExportJob:
    return ExportJob(
        id=JOB_ID,
        account_id=7,
        format=ExportFormat.CSV,
        filter_snapshot={"outcome": "found"},
        status=ExportStatus.RUNNING,
        owner_token="worker-1",
        leased_until=NOW + timedelta(minutes=15),
        attempt_count=1,
        safe_error_code="",
        artifact_path="",
        expires_at=None,
        created_at=NOW,
        updated_at=NOW,
        completed_at=None,
    )


class Repository:
    def __init__(self, claimed=None) -> None:
        self.claimed = claimed
        self.completed = []
        self.failed = []
        self.expired_paths: tuple[str, ...] = ()
        self.cleared = []
        self.deleted_jobs = []
        self.delete_path = ""

    def expire_completed(self, now):
        return self.expired_paths

    def claim_next(self, owner, now):
        self.claim_call = (owner, now)
        value, self.claimed = self.claimed, None
        return value

    def clear_artifact(self, path, now):
        self.cleared.append((path, now))
        return True

    def get(self, account_id, job_id):
        value = self.claimed
        return value if value and (value.account_id, value.id) == (account_id, job_id) else None

    def delete(self, account_id, job_id):
        self.deleted_jobs.append((account_id, job_id))
        return True, self.delete_path

    def complete(self, job_id, owner, artifact_path, completed_at, expires_at):
        self.completed.append(
            (job_id, owner, artifact_path, completed_at, expires_at)
        )
        return True

    def fail(self, job_id, owner, safe_error_code, now):
        self.failed.append((job_id, owner, safe_error_code, now))
        return True


class ArtifactStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.deleted = []
        self.purged = []

    def write_history(self, job_id, format_name, rows):
        if self.fail:
            raise OSError("private filesystem detail")
        list(rows)
        return f"{job_id.hex}.{format_name.value}"

    def delete(self, path):
        self.deleted.append(path)
        return True

    def purge_older_than(self, before):
        self.purged.append(before)
        return ()


class History:
    def iter_export(self, account_id, snapshot):
        self.call = (account_id, snapshot)
        return iter(())


def test_export_worker_claims_generates_and_completes_with_24_hour_expiry() -> None:
    repository = Repository(job())
    artifacts = ArtifactStore()
    history = History()
    worker = ExportWorker(
        repository,
        history,
        artifacts,
        worker_id="worker-1",
        clock=lambda: NOW,
    )

    assert worker.run_once() is True
    assert history.call == (7, {"outcome": "found"})
    assert repository.completed == [
        (JOB_ID, "worker-1", f"{JOB_ID.hex}.csv", NOW, NOW + timedelta(hours=24))
    ]


def test_export_worker_records_only_safe_error_code_and_continues() -> None:
    repository = Repository(job())
    worker = ExportWorker(
        repository,
        History(),
        ArtifactStore(fail=True),
        worker_id="worker-1",
        clock=lambda: NOW,
    )

    assert worker.run_once() is True
    assert repository.failed == [
        (JOB_ID, "worker-1", "export_generation_failed", NOW)
    ]


def test_export_worker_cleans_expired_artifacts_even_when_queue_is_empty() -> None:
    repository = Repository()
    repository.expired_paths = ("old.csv",)
    artifacts = ArtifactStore()
    worker = ExportWorker(
        repository,
        History(),
        artifacts,
        worker_id="worker-1",
        clock=lambda: NOW,
    )

    assert worker.run_once() is False
    assert artifacts.deleted == ["old.csv"]
    assert repository.cleared == [("old.csv", NOW)]
    assert artifacts.purged == [NOW - timedelta(hours=24)]


def test_user_delete_cleans_artifact_completed_during_the_delete_race() -> None:
    repository = Repository(job())
    repository.delete_path = "completed-during-delete.csv"
    artifacts = ArtifactStore()
    service = ExportService(repository, artifacts)

    assert service.delete(7, JOB_ID) is True

    assert artifacts.deleted == ["completed-during-delete.csv"]
