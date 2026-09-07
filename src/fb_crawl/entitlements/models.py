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

    @property
    def allow_auto_group_crawl(self) -> bool:
        return self.allow_group_crawl

    @property
    def allow_auto_comment_crawl(self) -> bool:
        return self.allow_comment_crawl

    @property
    def max_auto_crawl_identities(self) -> int:
        return 1000 if (
            self.allow_auto_group_crawl or self.allow_auto_comment_crawl
        ) else 0
