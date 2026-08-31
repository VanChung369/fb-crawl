from __future__ import annotations

from uuid import UUID
from datetime import UTC, datetime, timedelta
import os

import pytest

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.exports.artifacts import ExportArtifactStore
from fb_crawl.exports.models import ExportFormat


JOB_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def test_artifact_store_writes_below_root_and_deletes_recoverably(tmp_path) -> None:
    store = ExportArtifactStore(tmp_path)

    relative = store.write(
        JOB_ID,
        ExportFormat.CSV,
        lambda destination: destination.write_text("ok", encoding="utf-8"),
    )

    resolved = store.resolve(relative)
    assert resolved.is_relative_to(tmp_path.resolve())
    assert resolved.read_text(encoding="utf-8") == "ok"
    assert store.delete(relative) is True
    assert not resolved.exists()


def test_artifact_store_forces_private_file_permissions(tmp_path, monkeypatch) -> None:
    calls = []
    real_chmod = os.chmod

    def chmod(path, mode):
        calls.append((path, mode))
        real_chmod(path, mode)

    monkeypatch.setattr("fb_crawl.exports.artifacts.os.chmod", chmod)
    store = ExportArtifactStore(tmp_path)

    store.write(
        JOB_ID,
        ExportFormat.XLSX,
        lambda destination: destination.write_bytes(b"xlsx"),
    )

    assert calls and calls[-1][1] == 0o600


def test_artifact_store_purges_old_orphan_files(tmp_path) -> None:
    store = ExportArtifactStore(tmp_path)
    relative = store.write(
        JOB_ID,
        ExportFormat.CSV,
        lambda destination: destination.write_text("private", encoding="utf-8"),
    )
    path = store.resolve(relative)
    old = datetime(2026, 8, 29, tzinfo=UTC).timestamp()
    os.utime(path, (old, old))

    removed = store.purge_older_than(datetime(2026, 8, 30, tzinfo=UTC))

    assert removed == (relative,)
    assert not path.exists()


@pytest.mark.parametrize("value", ["../secret.csv", "/absolute.csv", "C:/secret.csv"])
def test_artifact_store_rejects_paths_outside_its_root(tmp_path, value) -> None:
    store = ExportArtifactStore(tmp_path)

    with pytest.raises(ValidationError):
        store.resolve(value)
