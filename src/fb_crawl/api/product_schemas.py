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
    max_devices: int
    allow_group_crawl: bool
    allow_comment_crawl: bool
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
