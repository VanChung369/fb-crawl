"""Typed, transport-independent contracts for authenticated crawl jobs."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Generic, Mapping, TypeVar
from urllib.parse import urlparse
from uuid import UUID

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.models import (
    AuthenticatedAction,
    ProfileField,
    ScrapeMode,
    ScrapeRequest,
)
from fb_crawl.core.urls import (
    FACEBOOK_HOSTS,
    classify_authenticated_url,
    normalize_comments_url,
    normalize_members_url,
    normalize_profile_collection_url,
    normalize_reactions_url,
    profile_identity_url,
)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class TargetStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class AccountStatus(StrEnum):
    READY = "ready"
    COOLDOWN = "cooldown"
    BLOCKED = "blocked"
    MANUAL_REVIEW = "manual_review"


class SafetyCode(StrEnum):
    SESSION_EXPIRED = "session_expired"
    CHECKPOINT = "checkpoint"
    TWO_FACTOR = "two_factor"
    CAPTCHA = "captcha"
    TEMPORARY_BLOCK = "temporary_block"
    ACCOUNT_RESTRICTED = "account_restricted"
    UNUSUAL_ACTIVITY = "unusual_activity"
    ACCOUNT_RECOVERY = "account_recovery"


_JOB_TRANSITIONS = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.CANCELLING,
            JobStatus.SUCCEEDED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.BLOCKED,
        }
    ),
    JobStatus.CANCELLING: frozenset({JobStatus.CANCELLED, JobStatus.BLOCKED}),
}

_TARGET_TRANSITIONS = {
    TargetStatus.PENDING: frozenset(
        {
            TargetStatus.RUNNING,
            TargetStatus.PARTIAL,
            TargetStatus.FAILED,
            TargetStatus.SKIPPED,
            TargetStatus.CANCELLED,
            TargetStatus.BLOCKED,
        }
    ),
    TargetStatus.RUNNING: frozenset(
        {
            TargetStatus.SUCCEEDED,
            TargetStatus.PARTIAL,
            TargetStatus.FAILED,
            TargetStatus.CANCELLED,
            TargetStatus.BLOCKED,
        }
    ),
}

_TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.SUCCEEDED,
        JobStatus.PARTIAL,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.BLOCKED,
    }
)

_TERMINAL_TARGET_STATUSES = frozenset(
    {
        TargetStatus.SUCCEEDED,
        TargetStatus.PARTIAL,
        TargetStatus.FAILED,
        TargetStatus.SKIPPED,
        TargetStatus.CANCELLED,
        TargetStatus.BLOCKED,
    }
)


def can_transition_job(current: JobStatus, next_status: JobStatus) -> bool:
    return next_status in _JOB_TRANSITIONS.get(current, frozenset())


def can_transition_target(current: TargetStatus, next_status: TargetStatus) -> bool:
    return next_status in _TARGET_TRANSITIONS.get(current, frozenset())


def is_terminal_job_status(status: JobStatus) -> bool:
    return status in _TERMINAL_JOB_STATUSES


def is_terminal_target_status(status: TargetStatus) -> bool:
    return status in _TERMINAL_TARGET_STATUSES


class JobNotFound(ValidationError):
    code = "job_not_found"


class JobConflict(ValidationError):
    code = "job_state_conflict"


class IdempotencyConflict(ValidationError):
    code = "job_idempotency_conflict"


_SUPPORTED_JOB_ACTIONS = frozenset(
    {
        AuthenticatedAction.MEMBERS,
        AuthenticatedAction.COMMENTS,
        AuthenticatedAction.PROFILE,
        AuthenticatedAction.FRIENDS,
        AuthenticatedAction.FOLLOWERS,
        AuthenticatedAction.REACTIONS,
        AuthenticatedAction.ENGAGEMENT,
    }
)

_RELATIONSHIP_ACTIONS = frozenset(
    {AuthenticatedAction.FRIENDS, AuthenticatedAction.FOLLOWERS}
)
_MAX_USER_ACTIONS = frozenset(
    {
        AuthenticatedAction.MEMBERS,
        AuthenticatedAction.PROFILE,
        AuthenticatedAction.FRIENDS,
        AuthenticatedAction.FOLLOWERS,
    }
)

_COMMON_OPTION_NAMES = frozenset(
    {
        "steps",
        "max_duration_seconds",
        "navigation_delay_seconds",
        "max_retries",
        "call_fbnumber",
        "enrich_phone",
        "enrich_profiles",
        "profile_fields",
        "profile_limit",
        "phone_post_steps",
        "phone_post_duration_seconds",
    }
)


_RELATIONSHIP_OPTION_NAMES = frozenset({"depth"})
_MAX_USER_OPTION_NAMES = frozenset({"max_users"})


def _integer(value: object, *, option: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{option} must be an integer.")
    if not minimum <= value <= maximum:
        raise ValidationError(f"{option} is outside the permitted range.")
    return value


def _seconds(
    value: object,
    *,
    option: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{option} must be a number.")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValidationError(f"{option} is outside the permitted range.")
    return result


_PROFILE_FIELD_ALIASES = {
    "work": ProfileField.WORKPLACE,
    "workplace": ProfileField.WORKPLACE,
    "location": ProfileField.CURRENT_CITY,
    "address": ProfileField.ADDRESS,
    "city": ProfileField.CURRENT_CITY,
    "current_city": ProfileField.CURRENT_CITY,
    "hometown": ProfileField.HOMETOWN,
    "birthday": ProfileField.BIRTH_DATE,
    "birth_date": ProfileField.BIRTH_DATE,
    "gender": ProfileField.GENDER,
    "sex": ProfileField.GENDER,
    "phone": ProfileField.PHONE,
    "bio": ProfileField.BIO,
    "education": ProfileField.EDUCATION,
    "website": ProfileField.WEBSITE,
    "languages": ProfileField.LANGUAGES,
    "relationship_status": ProfileField.RELATIONSHIP_STATUS,
}


def _profile_fields(value: object) -> tuple[ProfileField, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValidationError("profile_fields must be a list.")
    fields: list[ProfileField] = []
    for item in value:
        key = str(item).lower()
        if key in _PROFILE_FIELD_ALIASES:
            fields.append(_PROFILE_FIELD_ALIASES[key])
        else:
            try:
                fields.append(ProfileField(item))
            except (TypeError, ValueError) as error:
                raise ValidationError("An unsupported profile field was provided.") from error
    return tuple(dict.fromkeys(fields))



@dataclass(frozen=True, slots=True)
class SafeJobOptions:
    steps: int = 10
    max_duration_seconds: float = 300
    navigation_delay_seconds: float = 8
    max_retries: int = 1
    depth: int = 1
    max_users: int = 1000
    call_fbnumber: bool = True
    enrich_profiles: bool = False
    profile_fields: tuple[ProfileField, ...] = ()
    profile_limit: int = 20
    phone_post_steps: int = 0
    phone_post_duration_seconds: float | None = None


    def __post_init__(self) -> None:
        _integer(self.steps, option="steps", minimum=0, maximum=20)
        _seconds(
            self.max_duration_seconds,
            option="max_duration_seconds",
            minimum=1,
            maximum=1800,
        )
        _seconds(
            self.navigation_delay_seconds,
            option="navigation_delay_seconds",
            minimum=8,
            maximum=1800,
        )
        _integer(self.max_retries, option="max_retries", minimum=0, maximum=1)
        _integer(self.depth, option="depth", minimum=1, maximum=2)
        _integer(self.max_users, option="max_users", minimum=0, maximum=1000)
        _integer(self.profile_limit, option="profile_limit", minimum=1, maximum=50)
        _integer(
            self.phone_post_steps,
            option="phone_post_steps",
            minimum=0,
            maximum=20,
        )
        if self.phone_post_duration_seconds is not None:
            _seconds(
                self.phone_post_duration_seconds,
                option="phone_post_duration_seconds",
                minimum=1,
                maximum=300,
            )
        if not isinstance(self.enrich_profiles, bool):
            raise ValidationError("enrich_profiles must be a boolean.")
        if any(not isinstance(item, ProfileField) for item in self.profile_fields):
            raise ValidationError("profile_fields must contain ProfileField values.")
        if len(set(self.profile_fields)) != len(self.profile_fields):
            raise ValidationError("profile_fields must not contain duplicates.")
        if self.profile_fields and not self.enrich_profiles:
            raise ValidationError("profile_fields require enrich_profiles.")
        if self.phone_post_scan and not self.enrich_profiles:
            raise ValidationError("Phone post scanning requires profile enrichment.")
        if (
            self.phone_post_scan
            and self.profile_fields
            and ProfileField.PHONE not in self.profile_fields
        ):
            raise ValidationError("Phone post scanning requires the phone profile field.")

    @property
    def phone_post_scan(self) -> bool:
        return bool(self.phone_post_steps or self.phone_post_duration_seconds)

    @classmethod
    def from_mapping(
        cls,
        action: AuthenticatedAction,
        values: Mapping[str, object],
    ) -> SafeJobOptions:
        if action not in _SUPPORTED_JOB_ACTIONS:
            raise ValidationError("This authenticated action is not available for jobs.")
        if not isinstance(values, Mapping):
            raise ValidationError("Job options must be an object.")

        allowed = _COMMON_OPTION_NAMES
        if action in _RELATIONSHIP_ACTIONS:
            allowed = allowed | _RELATIONSHIP_OPTION_NAMES
        if action in _MAX_USER_ACTIONS:
            allowed = allowed | _MAX_USER_OPTION_NAMES
        unknown = set(values) - allowed
        if unknown:
            raise ValidationError("Unsupported job option.")

        fields = _profile_fields(values["profile_fields"]) if "profile_fields" in values else ()
        duration = values.get("phone_post_duration_seconds")
        return cls(
            steps=_integer(values.get("steps", 10), option="steps", minimum=0, maximum=20),
            max_duration_seconds=_seconds(
                values.get("max_duration_seconds", 300),
                option="max_duration_seconds",
                minimum=1,
                maximum=1800,
            ),
            navigation_delay_seconds=_seconds(
                values.get("navigation_delay_seconds", 8),
                option="navigation_delay_seconds",
                minimum=8,
                maximum=1800,
            ),
            max_retries=_integer(
                values.get("max_retries", 1), option="max_retries", minimum=0, maximum=1
            ),
            depth=_integer(values.get("depth", 1), option="depth", minimum=1, maximum=2),
            max_users=_integer(
                values.get("max_users", 1000), option="max_users", minimum=0, maximum=1000
            ),
            call_fbnumber=bool(values.get("call_fbnumber", values.get("enrich_phone", True))),
            enrich_profiles=values.get("enrich_profiles", False),
            profile_fields=fields,
            profile_limit=_integer(
                values.get("profile_limit", 20), option="profile_limit", minimum=1, maximum=50
            ),
            phone_post_steps=_integer(
                values.get("phone_post_steps", 0),
                option="phone_post_steps",
                minimum=0,
                maximum=20,
            ),
            phone_post_duration_seconds=(
                None
                if duration is None
                else _seconds(
                    duration,
                    option="phone_post_duration_seconds",
                    minimum=1,
                    maximum=300,
                )
            ),
        )

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "call_fbnumber": self.call_fbnumber,
            "depth": self.depth,
            "enrich_profiles": self.enrich_profiles,
            "max_duration_seconds": float(self.max_duration_seconds),
            "max_retries": self.max_retries,
            "max_users": self.max_users,
            "navigation_delay_seconds": float(self.navigation_delay_seconds),
            "phone_post_duration_seconds": (
                None
                if self.phone_post_duration_seconds is None
                else float(self.phone_post_duration_seconds)
            ),
            "phone_post_steps": self.phone_post_steps,
            "profile_fields": [field.value if hasattr(field, "value") else str(field) for field in self.profile_fields],
            "profile_limit": self.profile_limit,
            "steps": self.steps,
        }

    def to_scrape_request(
        self,
        action: AuthenticatedAction,
        targets: tuple[str, ...],
        *,
        checkpoint_path: str,
    ) -> ScrapeRequest:
        if action not in _SUPPORTED_JOB_ACTIONS:
            raise ValidationError("This authenticated action is not available for jobs.")
        if not checkpoint_path:
            raise ValidationError("A job target requires a checkpoint path.")
        return ScrapeRequest(
            mode=ScrapeMode.AUTHENTICATED,
            action=action,
            targets=targets,
            steps=(self.steps or None),
            max_duration_seconds=self.max_duration_seconds,
            depth=self.depth,
            max_nodes=self.max_users,
            delay_seconds=self.navigation_delay_seconds,
            enrich_profiles=self.enrich_profiles,
            profile_fields=self.profile_fields,
            profile_limit=self.profile_limit,
            profile_delay_seconds=self.navigation_delay_seconds,
            phone_post_steps=self.phone_post_steps,
            phone_post_duration_seconds=self.phone_post_duration_seconds,
            resume=True,
            checkpoint_path=checkpoint_path,
            max_retries=self.max_retries,
        )


@dataclass(frozen=True, slots=True)
class CanonicalJobTarget:
    target_key: str
    target_url: str
    target_kind: str


def _safe_facebook_target_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return False
    return (
        parsed.hostname is not None
        and parsed.hostname.casefold() in FACEBOOK_HOSTS
        and parsed.username is None
        and parsed.password is None
    )


def canonical_job_target(
    action: AuthenticatedAction,
    value: str,
) -> CanonicalJobTarget:
    if action not in _SUPPORTED_JOB_ACTIONS or not isinstance(value, str):
        raise ValidationError("Invalid authenticated job target.")
    try:
        if not _safe_facebook_target_url(value):
            raise ValueError
        classified = classify_authenticated_url(value)
        if action is AuthenticatedAction.PROFILE and (
            classified is not None
            and classified[0] is not AuthenticatedAction.PROFILE
        ):
            raise ValueError
        if action in _RELATIONSHIP_ACTIONS and (
            classified is not None
            and classified[0] not in {action, AuthenticatedAction.PROFILE}
        ):
            raise ValueError

        if action is AuthenticatedAction.MEMBERS:
            normalized = normalize_members_url(value)
            kind = "group_members"
        elif action is AuthenticatedAction.COMMENTS:
            normalized = normalize_comments_url(value)
            kind = "post_comments"
        elif action is AuthenticatedAction.PROFILE:
            identity = profile_identity_url(value)
            normalized = identity[1] if identity is not None else None
            kind = "profile"
        elif action in _RELATIONSHIP_ACTIONS:
            normalized = normalize_profile_collection_url(value, action.value)
            kind = f"profile_{action.value}"
        else:
            normalized = normalize_reactions_url(value)
            kind = f"post_{action.value}"
    except ValueError as error:
        raise ValidationError("Invalid authenticated job target.") from error

    if normalized is None:
        raise ValidationError("Invalid authenticated job target.")
    return CanonicalJobTarget(
        target_key=f"{action.value}:{normalized}",
        target_url=normalized,
        target_kind=kind,
    )


@dataclass(frozen=True, slots=True)
class JobCreateCommand:
    action: AuthenticatedAction
    targets: tuple[CanonicalJobTarget, ...]
    options: SafeJobOptions
    account_key: str = "default"
    priority: int = 0

    def __post_init__(self) -> None:
        if not 1 <= len(self.targets) <= 100:
            raise ValidationError("A job requires 1 to 100 targets.")
        unique_targets = tuple(dict.fromkeys(self.targets))
        object.__setattr__(self, "targets", unique_targets)
        if self.account_key != "default":
            raise ValidationError("Only the default account is supported.")
        if self.action not in _SUPPORTED_JOB_ACTIONS:
            raise ValidationError("This authenticated action is not available for jobs.")
        if not isinstance(self.options, SafeJobOptions):
            raise ValidationError("Job options must use the safe job contract.")
        if any(not target.target_key.startswith(f"{self.action.value}:") for target in unique_targets):
            raise ValidationError("All targets must match the job action.")

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "account_key": self.account_key,
            "action": self.action.value,
            "options": self.options.to_canonical_dict(),
            "priority": self.priority,
            "targets": [
                {
                    "target_key": target.target_key,
                    "target_kind": target.target_kind,
                    "target_url": target.target_url,
                }
                for target in self.targets
            ],
        }


def canonical_request_fingerprint(command: JobCreateCommand) -> str:
    payload = json.dumps(
        command.to_canonical_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class CrawlJob:
    id: UUID
    action: AuthenticatedAction
    status: JobStatus
    request_options: SafeJobOptions
    request_fingerprint: str
    created_at: datetime
    updated_at: datetime
    account_key: str = "default"
    mode: ScrapeMode = ScrapeMode.AUTHENTICATED
    idempotency_key: str | None = None
    retry_of_job_id: UUID | None = None
    priority: int = 0
    attempt: int = 0
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    requested_targets: int = 0
    completed_targets: int = 0
    failed_targets: int = 0
    discovered_users: int = 0
    persisted_users: int = 0
    provider_retries_required: int = 0
    current_target_id: UUID | None = None
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class CrawlTarget:
    id: UUID
    job_id: UUID
    target_key: str
    target_url: str
    target_kind: str
    position: int
    status: TargetStatus
    checkpoint_path: str
    created_at: datetime
    updated_at: datetime
    attempt: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    steps_completed: int = 0
    items_discovered: int = 0
    users_persisted: int = 0
    provider_retries_required: int = 0
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class CrawlEvent:
    id: int
    job_id: UUID
    event_type: str
    level: str
    created_at: datetime
    target_id: UUID | None = None
    safe_message: str = ""
    counters: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "counters", MappingProxyType(dict(self.counters)))


@dataclass(frozen=True, slots=True)
class CrawlerAccountState:
    account_key: str
    status: AccountStatus
    created_at: datetime
    updated_at: datetime
    cooldown_until: datetime | None = None
    last_job_id: UUID | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    rate_limit_count_24h: int = 0
    last_rate_limit_at: datetime | None = None
    last_warning_code: str = ""
    last_warning_at: datetime | None = None
    block_reason: str = ""
    acknowledged_at: datetime | None = None


RecordT = TypeVar("RecordT")


@dataclass(frozen=True, slots=True)
class Page(Generic[RecordT]):
    items: tuple[RecordT, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class KeysetCursor:
    sort_at: datetime
    row_id: int

    def __post_init__(self) -> None:
        if self.sort_at.tzinfo is None or self.sort_at.utcoffset() is None:
            raise ValidationError("Invalid pagination cursor.")
        if isinstance(self.row_id, bool) or not isinstance(self.row_id, int) or self.row_id <= 0:
            raise ValidationError("Invalid pagination cursor.")


def encode_cursor(cursor: KeysetCursor) -> str:
    payload = json.dumps(
        {"sort_at": cursor.sort_at.astimezone(UTC).isoformat(), "row_id": cursor.row_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> KeysetCursor:
    try:
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise ValueError
        encoded = value.encode("ascii")
        padding = b"=" * (-len(encoded) % 4)
        payload = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
        if len(payload) > 512:
            raise ValueError
        decoded = json.loads(payload)
        if not isinstance(decoded, dict) or set(decoded) != {"sort_at", "row_id"}:
            raise ValueError
        sort_at = datetime.fromisoformat(decoded["sort_at"])
        return KeysetCursor(sort_at=sort_at, row_id=decoded["row_id"])
    except (
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
        ValidationError,
    ) as error:
        raise ValidationError("Invalid pagination cursor.") from error
