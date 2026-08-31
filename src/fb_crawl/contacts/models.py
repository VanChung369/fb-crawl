from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from fb_data_pipeline.core.models import FacebookIdentity, ProviderStatus


class LookupOutcome(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    PROCESSING = "processing"
    QUOTA_EXCEEDED = "quota_exceeded"
    FAILED = "failed"


class LookupSource(StrEnum):
    CACHE = "cache"
    PROVIDER = "provider"
    NEGATIVE_CACHE = "negative_cache"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ContactIdentity:
    id: int
    identity: FacebookIdentity


@dataclass(frozen=True, slots=True)
class LookupState:
    facebook_user_id: int
    provider: str
    field: str
    latest_status: ProviderStatus
    checked_at: datetime
    refresh_after: datetime
    latest_attempt_id: int | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CachedContact:
    facebook_user_id: int
    phone_number_id: int | None
    phone: str
    observed_at: datetime | None
    state: LookupState | None

    @property
    def found(self) -> bool:
        return bool(self.phone_number_id and self.phone)


@dataclass(frozen=True, slots=True)
class EnrichmentLease:
    facebook_user_id: int
    provider: str
    field: str
    owner_token: str
    leased_until: datetime
    acquired: bool


@dataclass(frozen=True, slots=True)
class LookupEvent:
    id: int
    account_id: int
    device_id: int | None
    facebook_user_id: int
    requested_uid: str
    requested_username: str
    requested_profile_url: str
    outcome: LookupOutcome
    result_source: LookupSource
    provider_called: bool
    quota_charged: bool
    safe_error_code: str
    created_at: datetime
    completed_at: datetime | None
    revealed_phone_number_id: int | None = None
    revealed_phone: str = ""
    revealed_observed_at: datetime | None = None
