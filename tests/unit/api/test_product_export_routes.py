from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from fb_crawl.entitlements.models import Entitlements
from fb_crawl.auth.rate_limit import RateLimitPolicy
from fb_crawl.exports.models import ExportFormat, ExportJob, ExportStatus
from fb_crawl.exports.service import ExportWorkerUnavailable
from tests.unit.api.product_auth_fakes import INSTALLATION_ID, NOW
from tests.unit.api.test_product_auth_routes import product_client


JOB_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def job(status=ExportStatus.QUEUED, *, account_id=7, path=""):
    return ExportJob(
        id=JOB_ID,
        account_id=account_id,
        format=ExportFormat.CSV,
        filter_snapshot={"outcome": "found"},
        status=status,
        owner_token="",
        leased_until=None,
        attempt_count=0,
        safe_error_code="",
        artifact_path=path,
        expires_at=(NOW + timedelta(hours=24) if path else None),
        created_at=NOW,
        updated_at=NOW,
        completed_at=(NOW if path else None),
    )


class ExportServiceFake:
    def __init__(self, artifact: Path) -> None:
        self.value = job()
        self.artifact_path = artifact
        self.create_calls = []
        self.get_accounts = []
        self.deleted = []
        self.create_error = None

    def create(self, account_id, format_name, filters, now):
        if self.create_error is not None:
            raise self.create_error
        self.create_calls.append((account_id, format_name, filters, now))
        return self.value

    def get(self, account_id, export_id, now):
        self.get_accounts.append(account_id)
        return self.value if (account_id, export_id) == (7, JOB_ID) else None

    def artifact(self, value):
        return self.artifact_path if value.status is ExportStatus.COMPLETED else None

    def delete(self, account_id, export_id):
        self.deleted.append((account_id, export_id))
        return (
            (account_id, export_id) == (7, JOB_ID)
            and self.value.account_id == account_id
        )


class OneDeviceEntitlements:
    def for_account(self, _account_id, _now):
        return Entitlements(100, 1, False, False)


def headers(access):
    return {
        "Authorization": f"Bearer {access}",
        "X-Installation-ID": str(INSTALLATION_ID),
    }


def test_create_export_uses_authenticated_account_and_closed_filters(tmp_path) -> None:
    service = ExportServiceFake(tmp_path / "export.csv")
    client, _repository, _auth, access = product_client(export_service=service)

    response = client.post(
        "/api/v1/exports",
        headers=headers(access),
        json={"format": "csv", "filters": {"outcome": "found", "uid": "100"}},
    )

    assert response.status_code == 202
    assert response.json()["id"] == str(JOB_ID)
    assert service.create_calls[0][0] == 7
    assert service.create_calls[0][2] == {"outcome": "found", "uid": "100"}


def test_export_status_and_delete_hide_foreign_ids(tmp_path) -> None:
    service = ExportServiceFake(tmp_path / "export.csv")
    service.value = job(account_id=8)
    client, _repository, _auth, access = product_client(export_service=service)

    fetched = client.get(f"/api/v1/exports/{JOB_ID}", headers=headers(access))
    deleted = client.delete(f"/api/v1/exports/{JOB_ID}", headers=headers(access))

    assert fetched.status_code == 404
    assert deleted.status_code == 404


def test_completed_export_download_is_account_authorized(tmp_path) -> None:
    artifact = tmp_path / "export.csv"
    artifact.write_text("event_id\n71\n", encoding="utf-8")
    service = ExportServiceFake(artifact)
    service.value = job(status=ExportStatus.COMPLETED, path="safe/export.csv")
    client, _repository, _auth, access = product_client(export_service=service)

    response = client.get(
        f"/api/v1/exports/{JOB_ID}/download", headers=headers(access)
    )

    assert response.status_code == 200
    assert response.content.startswith(b"event_id")
    assert response.headers["cache-control"] == "no-store"
    assert service.get_accounts == [7]


def test_exports_require_bearer_and_an_allowed_device(tmp_path) -> None:
    service = ExportServiceFake(tmp_path / "export.csv")
    client, repository, _auth, access = product_client(
        export_service=service,
        entitlement_service=OneDeviceEntitlements(),
    )
    client.cookies.set("lead_finder_access", access)
    cookie_response = client.get(
        f"/api/v1/exports/{JOB_ID}",
        headers={"X-Installation-ID": str(INSTALLATION_ID)},
    )
    repository.extra_devices.append(
        replace(
            repository.device,
            id=8,
            first_seen_at=repository.device.first_seen_at - timedelta(days=1),
        )
    )
    device_response = client.get(
        f"/api/v1/exports/{JOB_ID}", headers=headers(access)
    )

    assert cookie_response.status_code == 401
    assert device_response.status_code == 403


def test_export_creation_is_rate_limited_before_queue_write(tmp_path) -> None:
    service = ExportServiceFake(tmp_path / "export.csv")
    client, _repository, _auth, access = product_client(
        export_service=service,
        rate_limit_policies={"export_create": RateLimitPolicy(1, 60)},
    )

    first = client.post(
        "/api/v1/exports", headers=headers(access), json={"format": "csv"}
    )
    limited = client.post(
        "/api/v1/exports", headers=headers(access), json={"format": "csv"}
    )

    assert first.status_code == 202
    assert limited.status_code == 429
    assert len(service.create_calls) == 1


def test_create_export_reports_unavailable_worker_without_queueing(tmp_path) -> None:
    service = ExportServiceFake(tmp_path / "export.csv")
    service.create_error = ExportWorkerUnavailable("worker unavailable")
    client, _repository, _auth, access = product_client(export_service=service)

    response = client.post(
        "/api/v1/exports",
        headers=headers(access),
        json={"format": "csv", "filters": {}},
    )

    assert response.status_code == 503
    assert response.json() == {
        "code": "export_worker_unavailable",
        "message": "Export request failed.",
    }
    assert service.create_calls == []
