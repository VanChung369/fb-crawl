from __future__ import annotations

from datetime import datetime
import re
from typing import Literal
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fb_crawl.contacts.service import ContactLookupRequest as DomainContactLookupRequest


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=128)


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=2048)


class ResetPasswordRequest(TokenRequest):
    new_password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)
    installation_id: UUID
    device_name: str = Field(min_length=1, max_length=128)
    transport: Literal["extension", "web"] = "extension"


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = Field(default=None, min_length=16, max_length=2048)
    installation_id: UUID
    transport: Literal["extension", "web"] = "extension"


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = Field(default=None, min_length=16, max_length=2048)
    transport: Literal["extension", "web"] = "extension"


class ReauthenticateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=128)


class RegistrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: int
    email: str
    verification_required: bool


class GenericAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool = True


class AuthTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    refresh_token: str | None
    token_type: Literal["bearer"] = "bearer"
    access_expires_at: datetime


class AccountMeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    email: str
    role: Literal["user", "admin"]
    status: Literal["pending", "active", "suspended", "deleted"]
    email_verified_at: datetime | None
    device_allowed: bool


class DeviceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    installation_id: UUID
    display_name: str
    status: Literal["active", "revoked"]
    first_seen_at: datetime
    last_seen_at: datetime
    current: bool


class DeviceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DeviceResponse]


class DeviceRevokedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["revoked"] = "revoked"
    device_id: int


class AccountDeletionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["deleted"] = "deleted"


class RedeemLicenseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=10, max_length=256)


class LicenseDurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit: Literal["day", "month"]
    value: int = Field(ge=1, le=3650)


class LicenseGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration: LicenseDurationRequest
    monthly_contact_limit: int = Field(ge=0, le=10_000_000)
    max_devices: int = Field(ge=1, le=100)
    allow_group_crawl: bool
    allow_comment_crawl: bool


class EntitlementsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monthly_contact_limit: int
    monthly_contact_used: int = Field(ge=0)
    max_devices: int
    allow_group_crawl: bool
    allow_comment_crawl: bool
    allow_auto_group_crawl: bool
    allow_auto_comment_crawl: bool
    max_auto_crawl_identities: int
    subscription_id: int | None
    starts_at: datetime | None
    ends_at: datetime | None


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscription_id: int
    account_id: int
    license_key_id: int
    duration: LicenseDurationRequest
    monthly_contact_limit: int
    max_devices: int
    allow_group_crawl: bool
    allow_comment_crawl: bool
    starts_at: datetime
    ends_at: datetime
    status: Literal["valid", "revoked"]


class LicenseKeyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    masked_key: str
    key_version: int
    duration: LicenseDurationRequest
    monthly_contact_limit: int
    max_devices: int
    allow_group_crawl: bool
    allow_comment_crawl: bool
    status: Literal["available", "redeemed", "revoked"]
    created_by_account_id: int | None
    redeemed_by_account_id: int | None
    redeemed_at: datetime | None
    created_at: datetime
    revoked_at: datetime | None


class CreatedLicenseKeyResponse(LicenseKeyResponse):
    key: str


class LicenseKeyListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LicenseKeyResponse]
    next_cursor: int | None = None


class AccountAdminResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    email: str
    role: Literal["user", "admin"]
    status: Literal["pending", "active", "suspended", "deleted"]
    email_verified_at: datetime | None
    created_at: datetime


class AccountAdminListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AccountAdminResponse]
    next_cursor: int | None = None


class SubscriptionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SubscriptionResponse]


class SessionsRevokedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["revoked"] = "revoked"
    account_id: int


class AdminAuditEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    actor_account_id: int | None
    action: str
    target_type: str
    target_id: str
    details: dict[str, object]
    created_at: datetime


class AdminAuditEventListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminAuditEventResponse]
    next_cursor: int | None = None


class ContactLookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facebook_uid: str = Field(default="", max_length=32)
    username: str = Field(default="", max_length=100)
    name: str = Field(default="", max_length=256)
    profile_url: str = Field(default="", max_length=2048)
    force_refresh: bool = False

    @field_validator("facebook_uid", "username", "name", "profile_url")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_facebook_identity(self) -> ContactLookupRequest:
        if self.facebook_uid and re.fullmatch(
            r"[0-9]+", self.facebook_uid
        ) is None:
            raise ValueError("facebook_uid must contain digits only")
        if self.username and re.fullmatch(
            r"[A-Za-z0-9.]+", self.username
        ) is None:
            raise ValueError("username has invalid characters")

        url_uid = ""
        url_username = ""
        if self.profile_url:
            parsed = urlsplit(self.profile_url)
            host = (parsed.hostname or "").casefold()
            if parsed.scheme != "https" or host not in {
                "facebook.com",
                "www.facebook.com",
                "m.facebook.com",
            }:
                raise ValueError("profile_url must be an HTTPS Facebook URL")
            path = parsed.path.strip("/")
            if path.casefold() == "profile.php":
                values = parse_qs(parsed.query).get("id", ())
                url_uid = values[0].strip() if values else ""
                if re.fullmatch(r"[0-9]+", url_uid) is None:
                    raise ValueError("Facebook profile id is invalid")
            elif path and "/" not in path:
                url_username = path
                if re.fullmatch(
                    r"[A-Za-z0-9.]+", url_username
                ) is None:
                    raise ValueError("Facebook profile username is invalid")
            else:
                raise ValueError("Facebook profile URL is invalid")

        if self.facebook_uid and url_uid and self.facebook_uid != url_uid:
            raise ValueError("Facebook UID aliases conflict")
        if (
            self.username
            and url_username
            and self.username.casefold() != url_username.casefold()
        ):
            raise ValueError("Facebook username aliases conflict")
        if not (self.facebook_uid or self.username or url_uid or url_username):
            raise ValueError("a Facebook UID or username is required")
        if not self.facebook_uid and url_uid:
            object.__setattr__(self, "facebook_uid", url_uid)
        if not self.username and url_username:
            object.__setattr__(self, "username", url_username)
        return self

    def to_domain(self) -> DomainContactLookupRequest:
        return DomainContactLookupRequest(
            facebook_uid=self.facebook_uid,
            username=self.username,
            name=self.name,
            profile_url=self.profile_url,
        )


class ContactUserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facebook_uid: str
    username: str
    name: str
    profile_url: str


class ContactDataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str


class ContactLookupMetaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: int
    state: Literal[
        "found", "not_found", "processing", "quota_exceeded", "failed"
    ]
    source: Literal["cache", "provider", "negative_cache", "none"]
    observed_at: datetime | None
    provider_called: bool
    quota_charged: bool
    monthly_used: int
    monthly_limit: int
    poll_url: str | None
    safe_error_code: str


class ContactLookupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: ContactUserResponse
    contact: ContactDataResponse
    meta: ContactLookupMetaResponse


class BatchContactItemRequest(ContactLookupRequest):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["member", "post_author", "comment_author"]
    source_url: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def validate_manual_scan_context(self) -> BatchContactItemRequest:
        from fb_crawl.contacts.models import LookupScanContext

        if self.force_refresh:
            raise ValueError("Batch lookup cannot force refresh")
        LookupScanContext(
            mode="manual_loaded",
            source_type=self.source_type,
            source_url=self.source_url,
        )
        return self


class BatchContactLookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BatchContactItemRequest] = Field(min_length=1, max_length=1000)


class BatchContactResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    duplicate_of: int | None
    user: ContactUserResponse
    contact: ContactDataResponse
    meta: ContactLookupMetaResponse


class BatchContactLookupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BatchContactResultResponse]
    detected_count: int
    unique_count: int
    processed_count: int
    found_count: int
    quota_exceeded_count: int


class ProductCrawlJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_url: str = Field(min_length=1, max_length=2048)
    scan_scope: Literal["members", "engagement", "both"]
    max_identities: int = Field(default=1000, ge=1, le=1000)


class ProductCrawlJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    scan_scope: Literal["members", "engagement", "both"]
    target_url: str
    max_identities: int
    status: Literal[
        "queued", "running", "succeeded", "partial", "failed",
        "cancelled", "blocked"
    ]
    discovered_count: int
    processed_count: int
    found_count: int
    not_found_count: int
    quota_exceeded_count: int
    safe_error_code: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class HistoryItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    device_id: int | None
    facebook_user_id: int
    user: ContactUserResponse
    phone: str
    outcome: Literal[
        "found", "not_found", "processing", "quota_exceeded", "failed"
    ]
    source: Literal["cache", "provider", "negative_cache", "none"]
    provider_called: bool
    quota_charged: bool
    safe_error_code: str
    created_at: datetime
    completed_at: datetime | None
    scan_mode: Literal["single", "manual_loaded", "automatic"]
    source_type: Literal[
        "profile", "member", "post_author", "comment_author"
    ]
    source_url: str
    product_crawl_job_id: UUID | None


class HistoryPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[HistoryItemResponse]
    next_cursor: str | None


class HistoryDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]


class HistoryDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted_count: int


class ExportFilterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal[
        "found", "not_found", "processing", "quota_exceeded", "failed"
    ] | None = None
    name: str | None = Field(default=None, max_length=256)
    uid: str | None = Field(default=None, max_length=32)
    username: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=64)
    created_from: datetime | None = None
    created_to: datetime | None = None


class ExportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["csv", "xlsx"]
    filters: ExportFilterRequest = Field(default_factory=ExportFilterRequest)


class ExportJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    format: Literal["csv", "xlsx"]
    filters: dict[str, str]
    status: Literal["queued", "running", "completed", "failed", "expired"]
    attempt_count: int
    safe_error_code: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    expires_at: datetime | None
    download_url: str | None


class ExportDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted: Literal[True]


class ExportWorkerHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    last_seen_at: datetime | None


class ProductWorkersHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    export: ExportWorkerHealthResponse


class ProductWorkerHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workers: ProductWorkersHealthResponse


class ProductMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounts_total: int
    accounts_active: int
    subscriptions_valid: int
    devices_active: int
    license_keys_total: int
    license_keys_available: int
    lookups_total: int
    lookups_found: int
    lookups_not_found: int
    lookups_failed: int
    lookups_processing: int
    quota_rejections: int
    cache_hits: int
    negative_cache_hits: int
    provider_calls: int
    provider_found: int
    provider_not_found: int
    provider_failed: int
    provider_latency_average_ms: int
    unique_contact_reveals: int
    exports_total: int
    exports_queued: int
    exports_running: int
    exports_completed: int
    exports_failed: int
    exports_expired: int
