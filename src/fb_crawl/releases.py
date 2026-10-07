"""Validated extension artifacts and a version policy shared by all API workers."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from fb_data_pipeline.repositories.errors import DatabaseError

MAX_RELEASE_BYTES = 25 * 1024 * 1024
DEFAULT_UPDATE_MESSAGE = "Đã có phiên bản {version}. Vui lòng cập nhật để sử dụng ổn định."
_VERSION = re.compile(r"(?:0|[1-9]\d{0,4})(?:\.(?:0|[1-9]\d{0,4})){0,3}\Z")


def version_parts(value: str | None):
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        return None
    parts = tuple(map(int, value.split(".")))
    if any(part > 65535 for part in parts):
        return None
    return parts + (0,) * (4 - len(parts))


class ReleaseError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.status = status
        super().__init__(message)


class Release(BaseModel):
    id: UUID
    version: str
    size_bytes: int
    sha256: str
    created_at: str


class UpdatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_release_id: UUID | None = None
    announcement_enabled: bool = False
    enforcement_enabled: bool = False
    min_supported_version: str = "0.0.0"
    message: str = Field(default=DEFAULT_UPDATE_MESSAGE, max_length=1000)


class ReleaseService:
    def __init__(self, repository, directory: Path):
        self.repository = repository
        self.directory = directory

    def path(self, release_id: UUID) -> Path:
        return self.directory / f"{UUID(str(release_id))}.zip"

    def upload(self, version: str, temporary: Path) -> Release:
        if version_parts(version) is None or version_parts(version) == (0, 0, 0, 0):
            raise ReleaseError("Version không hợp lệ. Ví dụ: 0.3.0.")
        size = temporary.stat().st_size
        if not 0 < size <= MAX_RELEASE_BYTES:
            raise ReleaseError("File ZIP phải nhỏ hơn hoặc bằng 25 MB.")
        try:
            with zipfile.ZipFile(temporary) as archive:
                entries = archive.infolist()
                if len(entries) > 10000 or sum(e.file_size for e in entries) > 150 * 1024 * 1024:
                    raise ValueError("Archive too large")
                names = set()
                for entry in entries:
                    name = entry.filename
                    if name in names or "\\" in name or ":" in name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or entry.flag_bits & 1:
                        raise ValueError("Unsafe archive")
                    if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("Symlinks are not supported")
                    names.add(name)
                info = archive.getinfo("manifest.json")
                if info.file_size > 64 * 1024:
                    raise ValueError("Manifest too large")
                manifest = json.loads(archive.read(info))
                if not isinstance(manifest, dict) or manifest.get("manifest_version") != 3 or manifest.get("version") != version:
                    raise ValueError("Manifest version mismatch")
                if archive.testzip() is not None:
                    raise ValueError("Corrupt archive")
        except (ValueError, KeyError, zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as error:
            raise ReleaseError("ZIP không hợp lệ; cần manifest.json ở gốc, Manifest V3 và version khớp.") from error
        release = Release(id=uuid4(), version=version, size_bytes=size,
                          sha256=hashlib.sha256(temporary.read_bytes()).hexdigest(), created_at=datetime.now(UTC).isoformat())
        destination = self.path(release.id)
        temporary.replace(destination)
        try:
            self.repository.add(release)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return release

    def list(self):
        return self.repository.snapshot()

    def save_policy(self, policy: UpdatePolicy):
        if version_parts(policy.min_supported_version) is None:
            raise ReleaseError("Phiên bản tối thiểu không hợp lệ.")
        if (policy.announcement_enabled or policy.enforcement_enabled) and not policy.active_release_id:
            raise ReleaseError("Chọn bản phát hành trước khi kích hoạt.")
        if policy.active_release_id is not None and not self.path(policy.active_release_id).is_file():
            raise ReleaseError("Không tìm thấy ZIP của bản phát hành.")
        policy.message = policy.message.strip() or DEFAULT_UPDATE_MESSAGE
        return self.repository.save_policy(policy)

    def delete(self, release_id: UUID):
        self.repository.delete(release_id)
        self.path(release_id).unlink(missing_ok=True)

    def public(self):
        releases, policy = self.list()
        active = next((r for r in releases if r.id == policy.active_release_id), None)
        return {"latest_version": active.version if active else "0.0.0",
                "min_supported_version": policy.min_supported_version,
                "download_url": f"/api/v1/app/releases/{active.id}/download" if active else "",
                "release_notes": policy.message.replace("{version}", active.version) if active else "",
                "release_date": active.created_at if active else "",
                "announcement_enabled": policy.announcement_enabled,
                "enforcement_enabled": policy.enforcement_enabled}

    def blocked(self, version: str | None):
        value = self.public()
        current = version_parts(version)
        return value if value["enforcement_enabled"] and (current is None or current < version_parts(value["min_supported_version"])) else None


def validate_policy(policy, releases):
    if policy.active_release_id:
        release = next((r for r in releases if r.id == policy.active_release_id), None)
        if release is None:
            raise ReleaseError("Bản phát hành không tồn tại.", 404)
        if version_parts(policy.min_supported_version) > version_parts(release.version):
            raise ReleaseError("Version tối thiểu không được cao hơn bản phát hành.")


class MemoryReleaseRepository:
    """In-memory adapter for injected test/non-product apps."""
    def __init__(self):
        self.releases = []
        self.policy = UpdatePolicy()
        self.lock = threading.RLock()

    def snapshot(self):
        with self.lock:
            return list(self.releases), self.policy.model_copy(deep=True)

    def add(self, release):
        with self.lock:
            if any(r.version == release.version for r in self.releases):
                raise ReleaseError("Version đã tồn tại.", 409)
            self.releases.insert(0, release)

    def save_policy(self, policy):
        with self.lock:
            validate_policy(policy, self.releases)
            self.policy = policy.model_copy(deep=True)
            return self.policy

    def delete(self, release_id):
        with self.lock:
            if self.policy.active_release_id == release_id:
                raise ReleaseError("Đổi hoặc bỏ bản đang phát hành trước khi xóa.", 409)
            if not any(r.id == release_id for r in self.releases):
                raise ReleaseError("Bản phát hành không tồn tại.", 404)
            self.releases = [r for r in self.releases if r.id != release_id]


class PostgresReleaseRepository:
    def __init__(self, database_url, connect_factory=psycopg.connect):
        self.database_url = database_url
        self.connect_factory = connect_factory

    @contextmanager
    def connect(self, *, mutation=False, snapshot=False):
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    if snapshot:
                        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    cursor.execute("SELECT set_config('statement_timeout', '5000ms', true)")
                    if mutation:
                        cursor.execute("SELECT pg_advisory_xact_lock(78139224)")
                    yield cursor
        except psycopg.errors.UniqueViolation as error:
            raise ReleaseError("Version đã tồn tại.", 409) from error
        except psycopg.Error as error:
            raise DatabaseError("Database operation failed.") from error

    def read(self, cursor):
        cursor.execute("SELECT id, version, size_bytes, sha256, created_at FROM extension_releases ORDER BY created_at DESC")
        releases = [Release(id=r[0], version=r[1], size_bytes=r[2], sha256=r[3], created_at=r[4].isoformat()) for r in cursor.fetchall()]
        cursor.execute("SELECT active_release_id, announcement_enabled, enforcement_enabled, min_supported_version, message FROM extension_update_policy WHERE singleton")
        row = cursor.fetchone()
        policy = UpdatePolicy(active_release_id=row[0], announcement_enabled=row[1], enforcement_enabled=row[2], min_supported_version=row[3], message=row[4]) if row else UpdatePolicy()
        return releases, policy

    def snapshot(self):
        with self.connect(snapshot=True) as cursor:
            return self.read(cursor)

    def add(self, release):
        with self.connect(mutation=True) as cursor:
            cursor.execute("INSERT INTO extension_releases(id,version,size_bytes,sha256,created_at) VALUES (%s,%s,%s,%s,%s)",
                           (release.id, release.version, release.size_bytes, release.sha256, release.created_at))

    def save_policy(self, policy):
        with self.connect(mutation=True) as cursor:
            releases, _ = self.read(cursor)
            validate_policy(policy, releases)
            cursor.execute("UPDATE extension_update_policy SET active_release_id=%s, announcement_enabled=%s, enforcement_enabled=%s, min_supported_version=%s, message=%s, updated_at=now() WHERE singleton",
                           (policy.active_release_id, policy.announcement_enabled, policy.enforcement_enabled, policy.min_supported_version, policy.message))
        return policy

    def delete(self, release_id):
        with self.connect(mutation=True) as cursor:
            releases, policy = self.read(cursor)
            if policy.active_release_id == release_id:
                raise ReleaseError("Đổi hoặc bỏ bản đang phát hành trước khi xóa.", 409)
            if not any(r.id == release_id for r in releases):
                raise ReleaseError("Bản phát hành không tồn tại.", 404)
            cursor.execute("DELETE FROM extension_releases WHERE id=%s", (release_id,))
