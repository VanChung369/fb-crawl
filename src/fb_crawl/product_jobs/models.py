from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class ProductCrawlScope(StrEnum):
    MEMBERS = "members"
    ENGAGEMENT = "engagement"
    BOTH = "both"


class ProductCrawlStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ProductCrawlJob:
    id: UUID
    account_id: int
    scope: ProductCrawlScope
    target_url: str
    max_identities: int
    status: ProductCrawlStatus
    discovered_count: int
    processed_count: int
    found_count: int
    not_found_count: int
    quota_exceeded_count: int
    safe_error_code: str
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProductCrawlOwner:
    product_job_id: UUID
    account_id: int
    max_identities: int
