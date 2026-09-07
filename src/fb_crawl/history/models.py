from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fb_crawl.contacts.models import (
    LookupOutcome,
    LookupScanMode,
    LookupSource,
    LookupSourceType,
)
from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.jobs import decode_cursor
from fb_data_pipeline.core.phone import InvalidPhoneNumber, normalize_phone


@dataclass(frozen=True, slots=True)
class AccountHistoryQuery:
    account_id: int
    product_crawl_job_id: UUID | None = None
    outcome: LookupOutcome | str | None = None
    name: str | None = None
    uid: str | None = None
    username: str | None = None
    phone: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    cursor: str | None = None
    limit: int = 20

    def __post_init__(self) -> None:
        if (
            isinstance(self.account_id, bool)
            or not isinstance(self.account_id, int)
            or self.account_id <= 0
        ):
            raise ValidationError("Invalid history account.")
        if self.product_crawl_job_id is not None and not isinstance(
            self.product_crawl_job_id, UUID
        ):
            raise ValidationError("Invalid product crawl job filter.")
        if self.outcome is not None:
            try:
                outcome = LookupOutcome(str(self.outcome))
            except ValueError as error:
                raise ValidationError("Invalid history outcome.") from error
            object.__setattr__(self, "outcome", outcome)
        for field_name in ("name", "uid", "username"):
            value = getattr(self, field_name)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValidationError(f"Invalid history {field_name} filter.")
                object.__setattr__(self, field_name, value.strip())
        if self.phone is not None:
            if not isinstance(self.phone, str):
                raise ValidationError("Invalid history phone filter.")
            try:
                object.__setattr__(self, "phone", normalize_phone(self.phone))
            except InvalidPhoneNumber as error:
                raise ValidationError("Invalid history phone filter.") from error
        for field_name in ("created_from", "created_to"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise ValidationError("History timestamps must be timezone-aware.")
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_from > self.created_to
        ):
            raise ValidationError("Invalid history time range.")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 100:
            raise ValidationError("History limit must be from 1 to 100.")
        if self.cursor is not None:
            decode_cursor(self.cursor)


@dataclass(frozen=True, slots=True)
class HistoryItem:
    id: int
    account_id: int
    device_id: int | None
    facebook_user_id: int
    facebook_uid: str
    username: str
    name: str
    profile_url: str
    phone_number_id: int | None
    phone: str
    outcome: LookupOutcome
    source: LookupSource
    provider_called: bool
    quota_charged: bool
    safe_error_code: str
    created_at: datetime
    completed_at: datetime | None
    scan_mode: LookupScanMode | str = LookupScanMode.SINGLE
    source_type: LookupSourceType | str = LookupSourceType.PROFILE
    source_url: str = ""
    product_crawl_job_id: UUID | None = None
    gender: str = ""
    address: str = ""
    birth_date: str = ""

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "scan_mode", LookupScanMode(str(self.scan_mode)))
            object.__setattr__(
                self,
                "source_type",
                LookupSourceType(str(self.source_type)),
            )
        except ValueError as error:
            raise ValidationError("Invalid history scan context.") from error
