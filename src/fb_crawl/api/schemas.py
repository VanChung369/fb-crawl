"""Closed HTTP schemas for crawl jobs and crawler account state."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypeAlias
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


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["authenticated"]
    action: SupportedJobAction
    targets: list[str] = Field(min_length=1, max_length=100)
    options: dict[str, JsonValue] = Field(default_factory=dict)


class JobOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: int
    max_duration_seconds: float
    navigation_delay_seconds: float
    max_retries: int
    depth: int
    max_users: int
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


class JobEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    job_id: UUID
    event_type: str
    level: str
    created_at: datetime
    target_id: UUID | None
    safe_message: str
    counters: dict[str, int]


class JobPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[JobResponse]
    next_cursor: str | None


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


class ApiErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
