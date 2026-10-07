from pathlib import Path

import psycopg
import pytest

from fb_crawl.releases import PostgresReleaseRepository, ReleaseService, UpdatePolicy, ReleaseError
from fb_data_pipeline.repositories.migrations import MigrationRunner
from tests.integration.data_pipeline.test_user_query_repository import TEST_DATABASE_URL, TEST_DATABASE_NAME
from tests.unit.api.test_extension_releases import archive

pytestmark = pytest.mark.skipif(TEST_DATABASE_NAME is None, reason="Requires a dedicated PostgreSQL database ending in _test")


def test_release_policy_survives_restart_and_protects_active_artifact(tmp_path: Path):
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        assert connection.info.dbname == TEST_DATABASE_NAME
    MigrationRunner(TEST_DATABASE_URL).apply()
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("UPDATE extension_update_policy SET active_release_id=NULL, announcement_enabled=false, enforcement_enabled=false")
        connection.execute("DELETE FROM extension_releases")
    service = ReleaseService(PostgresReleaseRepository(TEST_DATABASE_URL), tmp_path)
    temporary = tmp_path / "upload.zip"
    temporary.write_bytes(archive())
    release = service.upload("0.3.0", temporary)
    service.save_policy(UpdatePolicy(active_release_id=release.id, announcement_enabled=True, enforcement_enabled=True, min_supported_version="0.2.5"))
    restarted = ReleaseService(PostgresReleaseRepository(TEST_DATABASE_URL), tmp_path)
    assert restarted.public()["latest_version"] == "0.3.0"
    assert restarted.blocked("0.2.4")
    assert restarted.blocked("0.2.10") is None
    with pytest.raises(ReleaseError):
        restarted.delete(release.id)
    restarted.save_policy(UpdatePolicy())
    restarted.delete(release.id)
    assert not service.path(release.id).exists()
