from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID

from fb_crawl.core.exceptions import ValidationError

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


class LookupScanMode(StrEnum):
    SINGLE = "single"
    MANUAL_LOADED = "manual_loaded"
    AUTOMATIC = "automatic"


class LookupSourceType(StrEnum):
    PROFILE = "profile"
    MEMBER = "member"
    POST_AUTHOR = "post_author"
    COMMENT_AUTHOR = "comment_author"


@dataclass(frozen=True, slots=True)
class LookupScanContext:
    mode: LookupScanMode | str = LookupScanMode.SINGLE
    source_type: LookupSourceType | str = LookupSourceType.PROFILE
    source_url: str = ""
    product_crawl_job_id: UUID | None = None

    def __post_init__(self) -> None:
        try:
            mode = LookupScanMode(str(self.mode))
            source_type = LookupSourceType(str(self.source_type))
        except ValueError as error:
            raise ValidationError("Invalid lookup scan context.") from error
        if not isinstance(self.source_url, str):
            raise ValidationError("Invalid lookup scan source URL.")
        source_url = self.source_url.strip()
        if len(source_url) > 2048:
            raise ValidationError("Invalid lookup scan source URL.")
        if source_url:
            parsed = urlsplit(source_url)
            if (
                parsed.scheme != "https"
                or (parsed.hostname or "").casefold()
                not in {"facebook.com", "www.facebook.com", "m.facebook.com"}
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValidationError("Invalid lookup scan source URL.")
        if self.product_crawl_job_id is not None and not isinstance(
            self.product_crawl_job_id,
            UUID,
        ):
            raise ValidationError("Invalid product crawl job identifier.")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "source_url", source_url)


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
    scan_context: LookupScanContext = LookupScanContext()
