from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping
from uuid import UUID


class ExportFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"


class ExportStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ExportJob:
    id: UUID
    account_id: int
    format: ExportFormat
    filter_snapshot: Mapping[str, object]
    status: ExportStatus
    owner_token: str
    leased_until: datetime | None
    attempt_count: int
    safe_error_code: str
    artifact_path: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
