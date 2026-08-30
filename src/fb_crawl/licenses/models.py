from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from enum import StrEnum
from typing import Literal


DurationUnit = Literal["day", "month"]


@dataclass(frozen=True, slots=True)
class LicenseDuration:
    unit: DurationUnit
    value: int

    def __post_init__(self) -> None:
        if self.unit not in {"day", "month"}:
            raise ValueError("license duration unit must be day or month")
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value <= 0:
            raise ValueError("license duration value must be a positive integer")


@dataclass(frozen=True, slots=True)
class LicenseGrant:
    duration: LicenseDuration
    monthly_contact_limit: int
    max_devices: int
    allow_group_crawl: bool
    allow_comment_crawl: bool

    def __post_init__(self) -> None:
        if (
            isinstance(self.monthly_contact_limit, bool)
            or not isinstance(self.monthly_contact_limit, int)
            or self.monthly_contact_limit < 0
        ):
            raise ValueError("monthly contact limit must be a non-negative integer")
        if (
            isinstance(self.max_devices, bool)
            or not isinstance(self.max_devices, int)
            or self.max_devices < 1
        ):
            raise ValueError("max devices must be a positive integer")
        if not isinstance(self.allow_group_crawl, bool) or not isinstance(
            self.allow_comment_crawl, bool
        ):
            raise ValueError("crawl permissions must be booleans")


class SubscriptionStatus(StrEnum):
    VALID = "valid"
    REVOKED = "revoked"


class LicenseKeyStatus(StrEnum):
    AVAILABLE = "available"
    REDEEMED = "redeemed"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class LicenseKey:
    id: int
    key_digest: str = field(repr=False)
    key_version: int
    masked_key: str
    grant: LicenseGrant
    status: LicenseKeyStatus
    created_by_account_id: int | None
    redeemed_by_account_id: int | None
    redeemed_at: datetime | None
    created_at: datetime
    revoked_at: datetime | None

    def __post_init__(self) -> None:
        if self.id <= 0 or self.key_version <= 0:
            raise ValueError("license key identifiers must be positive")
        if not self.key_digest:
            raise ValueError("license key digest is required")


@dataclass(frozen=True, slots=True)
class Subscription:
    id: int
    account_id: int
    license_key_id: int
    grant: LicenseGrant
    starts_at: datetime
    ends_at: datetime
    status: SubscriptionStatus
    revoked_at: datetime | None
    created_at: datetime

    def __post_init__(self) -> None:
        if self.id <= 0 or self.account_id <= 0 or self.license_key_id <= 0:
            raise ValueError("subscription identifiers must be positive")
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("subscription times must be timezone-aware")
        if self.ends_at <= self.starts_at:
            raise ValueError("subscription end must be after start")
