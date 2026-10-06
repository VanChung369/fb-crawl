"""Shared maintenance settings, read across API workers and retained on restart."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_MESSAGE = "Hệ thống đang bảo trì. Vui lòng thử lại sau. Bạn vẫn có thể xem và xuất dữ liệu đã thu thập."


class MaintenanceError(Exception):
    pass


class MaintenanceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    message: str = Field(default=DEFAULT_MESSAGE, max_length=1000)
    updated_at: str | None = None


class MaintenanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    message: str = Field(default=DEFAULT_MESSAGE, max_length=1000)


class MaintenanceStore:
    def __init__(self, path: Path = Path("runtime/maintenance.json")):
        self.path = path

    def read(self) -> MaintenanceStatus:
        try:
            return MaintenanceStatus.model_validate(json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return MaintenanceStatus()
        except (OSError, ValueError, ValidationError):
            # A damaged settings file must not silently reopen paid operations.
            return MaintenanceStatus(enabled=True)

    def write(self, enabled: bool, message: str) -> MaintenanceStatus:
        value = MaintenanceStatus(enabled=enabled, message=message.strip() or DEFAULT_MESSAGE,
                                  updated_at=datetime.now(UTC).isoformat())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="maintenance-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(value.model_dump_json())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return value


def blocks_new_work(method: str, path: str) -> bool:
    path = path.rstrip("/")
    return method == "POST" and (
        path in {"/api/v1/contacts/lookup", "/api/v1/contacts/batch-lookup",
                 "/api/v1/crawl-jobs", "/api/v1/interaction-sessions"}
        or (path.startswith("/api/v1/interaction-sessions/") and path.endswith("/lookup"))
    )
