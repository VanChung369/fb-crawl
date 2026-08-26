"""Closed HTTP schemas for crawl jobs and crawler account state."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from fb_crawl.core.jobs import AccountStatus, JobStatus, TargetStatus


SupportedJobAction: TypeAlias = Literal[
    "members",
    "comments",
    "profile",
    "friends",
    "followers",
    "reactions",
    "engagement",
]
TargetUrl: TypeAlias = Annotated[str, Field(min_length=1, max_length=2048)]

EVENT_COUNTER_NAMES = frozenset(
    {
        "requested_targets",
        "completed_targets",
        "failed_targets",
        "discovered_users",
        "persisted_users",
        "provider_retries_required",
        "steps_completed",
        "items_discovered",
        "users_persisted",
    }
)


ACTION_ALIASES = {
    "group_members": "members",
    "post_comments": "comments",
    "post_reactions": "reactions",
    "profile_about": "profile",
    "relationships_friends": "friends",
    "relationships_followers": "followers",
}


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["authenticated"] = "authenticated"
    action: SupportedJobAction
    targets: list[TargetUrl] = Field(min_length=1, max_length=100)
    options: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_request(cls, data: object) -> object:
        if isinstance(data, dict):
            payload = dict(data)
            if "mode" not in payload:
                payload["mode"] = "authenticated"
            raw_action = payload.get("action")
            if isinstance(raw_action, str) and raw_action in ACTION_ALIASES:
                payload["action"] = ACTION_ALIASES[raw_action]
            return payload
        return data


class GroupBatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_url: TargetUrl
    batch_count: int = Field(default=5, ge=1, le=100)
    batch_size: int = Field(default=300, ge=1, le=1000)
    batch_duration_seconds: float = Field(default=900, ge=60, le=1800)
    navigation_delay_seconds: float = Field(default=20, ge=8, le=1800)
    steps: int = Field(default=0, ge=0, le=20)
    call_fbnumber: bool = True


class JobOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: int
    max_duration_seconds: float
    navigation_delay_seconds: float
    max_retries: int
    depth: int
    max_users: int
    call_fbnumber: bool = True
    enrich_profiles: bool
    profile_fields: list[str]
    profile_limit: int
    phone_post_steps: int
    phone_post_duration_seconds: float | None



class JobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    mode: Literal["authenticated"]
    action: SupportedJobAction
    status: JobStatus
    options: JobOptionsResponse
    retry_of_job_id: UUID | None
    priority: int
    attempt: int
    cancel_requested_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    requested_targets: int
    completed_targets: int
    failed_targets: int
    discovered_users: int
    persisted_users: int
    provider_retries_required: int
    current_target_id: UUID | None
    error_code: str
    error_message: str


class JobTargetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    job_id: UUID
    target_key: str
    target_url: str
    target_kind: str
    position: int
    status: TargetStatus
    created_at: datetime
    updated_at: datetime
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    steps_completed: int
    items_discovered: int
    users_persisted: int
    provider_retries_required: int
    error_code: str
    error_message: str


class EventCountersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_targets: int | None = Field(default=None, ge=0)
    completed_targets: int | None = Field(default=None, ge=0)
    failed_targets: int | None = Field(default=None, ge=0)
    discovered_users: int | None = Field(default=None, ge=0)
    persisted_users: int | None = Field(default=None, ge=0)
    provider_retries_required: int | None = Field(default=None, ge=0)
    steps_completed: int | None = Field(default=None, ge=0)
    items_discovered: int | None = Field(default=None, ge=0)
    users_persisted: int | None = Field(default=None, ge=0)


class JobEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    job_id: UUID
    event_type: str
    level: str
    created_at: datetime
    target_id: UUID | None
    safe_message: str
    counters: EventCountersResponse


class JobPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[JobResponse]
    next_cursor: str | None


class GroupBatchCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_requested: int
    total_created: int
    items: list[JobResponse]


class JobTargetPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[JobTargetResponse]
    next_cursor: str | None


class JobEventPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[JobEventResponse]
    next_cursor: str | None


class AccountAcknowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledged: Literal[True]

    @model_validator(mode="before")
    @classmethod
    def require_exact_true(cls, value: object) -> object:
        if not isinstance(value, dict) or value.get("acknowledged") is not True:
            raise ValueError("Account acknowledgement must be true.")
        return value


class AccountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_key: Literal["default"]
    status: AccountStatus
    cooldown_until: datetime | None
    last_started_at: datetime | None
    last_finished_at: datetime | None
    last_rate_limit_at: datetime | None
    last_warning_code: str
    last_warning_at: datetime | None
    acknowledged_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int = Field(gt=0)
    facebook_uid: str | None
    username: str | None
    name: str | None
    profile_url: str | None
    phone_1: str | None
    phone_2: str | None
    address: str | None
    birth_date: str | None
    gender: str | None
    created_at: datetime
    updated_at: datetime


class PhoneEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int = Field(gt=0)
    normalized_phone: str
    display_phone: str
    origin: Literal["fbnumber", "fb_crawl"]
    source: str
    source_url: str
    provider: str
    confidence: str
    first_captured_at: datetime
    last_captured_at: datetime
    evidence_count: int = Field(gt=0)
    created_at: datetime
    updated_at: datetime


class EnrichmentAttemptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int = Field(gt=0)
    provider: str
    status: Literal["found", "not_found", "rate_limited", "failed"]
    checked_at: datetime
    error_code: str | None
    values_found: int = Field(ge=0)
    created_at: datetime


class UserPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[UserResponse]
    next_cursor: str | None


class PhoneEvidencePageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PhoneEvidenceResponse]
    next_cursor: str | None


class EnrichmentAttemptPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EnrichmentAttemptResponse]
    next_cursor: str | None


class ApiErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ProxyItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_url: str
    scheme: str
    host: str
    port: int
    status: str
    success_count: int
    failure_count: int
    is_available: bool


class ProxyListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_count: int
    active_count: int
    items: list[ProxyItemResponse]


class ProxyAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proxies: list[str] = Field(min_length=1, max_length=500)


class SessionItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    proxy: str | None
    status: str
    success_count: int
    failure_count: int
    is_available: bool


class SessionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_count: int
    available_count: int
    items: list[SessionItemResponse]


class SessionImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    cookies: list[dict[str, JsonValue]] | str
    proxy: str | None = None



class StatsOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_users: int
    users_with_phone: int
    total_jobs: int
    active_proxies: int
    total_proxies: int
    available_sessions: int
    total_sessions: int


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    username: str | None = None
    phone_1: str | None = None
    phone_2: str | None = None
    address: str | None = None
    gender: str | None = None
    birth_date: str | None = None


class SessionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proxy: str | None = None
    status: str | None = None


class ProxyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_url: str
    new_url: str | None = None
    status: str | None = None


class ProxyDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_url: str


class FBNumberScansSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=10000)
    filter: str = Field(default="", max_length=256)
    api_token: str | None = Field(default=None, max_length=2048)
    api_url: str | None = Field(default=None, max_length=1024)
    preview: bool = Field(default=False)


class FBNumberScansItemResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: int | None = None
    uid: str | None = None
    username: str | None = None
    name: str | None = None
    profile_url: str | None = None
    phone_1: str | None = None
    phone_2: str | None = None
    address: str | None = None
    gender: str | None = None
    birthday: str | None = None
    scan_at: str | None = None


class FBNumberScansSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    total_count: int
    fetched_count: int
    imported_count: int
    skipped_count: int
    preview: bool = False
    message: str
    items: list[FBNumberScansItemResponse] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
