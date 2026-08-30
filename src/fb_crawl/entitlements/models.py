from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Entitlements:
    monthly_contact_limit: int
    max_devices: int
    allow_group_crawl: bool
    allow_comment_crawl: bool
    subscription_id: int | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.monthly_contact_limit < 0:
            raise ValueError("monthly contact limit must not be negative")
        if self.max_devices < 1:
            raise ValueError("max devices must be positive")
