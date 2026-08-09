from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, Mapping, TypeVar

JsonScalar = str | int | float | bool | None

RecordT = TypeVar("RecordT")


class ScrapeMode(StrEnum):
    PUBLIC = "public"
    AUTHENTICATED = "authenticated"


class PublicAction(StrEnum):
    PAGE = "page"
    SEARCH = "search"
    CRAWL = "crawl"


class AuthenticatedAction(StrEnum):
    MEMBERS = "members"
    COMMENTS = "comments"
    PROFILE = "profile"
    FRIENDS = "friends"
    FOLLOWERS = "followers"
    REACTIONS = "reactions"
    ENGAGEMENT = "engagement"
    MESSAGES = "messages"
    INSPECT = "inspect"
    BATCH = "batch"


class ProfileField(StrEnum):
    PHONE = "phone"
    WEBSITE = "website"
    ADDRESS = "address"
    CURRENT_CITY = "current_city"
    HOMETOWN = "hometown"
    BIRTH_DATE = "birth_date"
    BIO = "bio"
    WORKPLACE = "workplace"
    EDUCATION = "education"
    GENDER = "gender"
    LANGUAGES = "languages"
    RELATIONSHIP_STATUS = "relationship_status"


class FieldStatus(StrEnum):
    FOUND = "found"
    NOT_VISIBLE = "not_visible"
    SECTION_UNAVAILABLE = "section_unavailable"
    NAVIGATION_FAILED = "navigation_failed"
    NOT_REQUESTED = "not_requested"


class TargetKind(StrEnum):
    PAGES = "pages"
    PEOPLE = "people"
    ALL = "all"


class ContactKind(StrEnum):
    PHONE = "phone"
    EMAIL = "email"
    WEBSITE = "website"


@dataclass(frozen=True, slots=True)
class ScrapeRequest:
    mode: ScrapeMode
    action: PublicAction | AuthenticatedAction | str
    targets: tuple[str, ...]
    keyword: str | None = None
    target_kind: TargetKind = TargetKind.PAGES
    limit: int = 20
    depth: int = 0
    max_nodes: int = 20
    delay_seconds: float = 0.0
    steps: int = 5
    enrich_profiles: bool = False
    profile_fields: tuple[ProfileField, ...] = ()
    profile_limit: int = 20
    profile_delay_seconds: float = 3.0
    resume: bool = False
    incremental: bool = False
    checkpoint_path: str | None = None

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be greater than 0")

        if self.steps <= 0:
            raise ValueError("steps must be greater than 0")

        if self.depth < 0:
            raise ValueError("depth must be greater than or equal to 0")

        if self.max_nodes <= 0:
            raise ValueError("max_nodes must be greater than 0")

        if self.delay_seconds < 0:
            raise ValueError("delay_seconds must be greater than or equal to 0")

        if self.profile_limit <= 0:
            raise ValueError("profile_limit must be greater than 0")

        if self.profile_delay_seconds < 0:
            raise ValueError(
                "profile_delay_seconds must be greater than or equal to 0"
            )

        if self.profile_fields and not self.enrich_profiles:
            raise ValueError("profile_fields require enrich_profiles")

        if any(not isinstance(item, ProfileField) for item in self.profile_fields):
            raise ValueError("profile_fields must contain ProfileField values")

        if len(set(self.profile_fields)) != len(self.profile_fields):
            raise ValueError("profile_fields must not contain duplicates")

        if self.resume and self.incremental:
            raise ValueError("resume and incremental cannot be used together")


@dataclass(frozen=True, slots=True)
class ContactRecord:
    kind: ContactKind
    value: str
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PageRecord:
    canonical_url: str
    page_name: str | None = None
    uid: str | None = None
    category: str | None = None
    website: str | None = None
    address: str | None = None
    contacts: tuple[ContactRecord, ...] = ()
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)
    depth: int = 0
    discovery_source: str = "seed"


@dataclass(frozen=True, slots=True)
class ProfileDetails:
    name: str | None = None
    phone_numbers: tuple[str, ...] = ()
    phone_sources: tuple[str, ...] = ()
    website: str | None = None
    address: str | None = None
    current_city: str | None = None
    hometown: str | None = None
    birth_date: str | None = None
    birth_year: int | None = None
    bio: str | None = None
    workplace: str | None = None
    education: str | None = None
    gender: str | None = None
    languages: tuple[str, ...] = ()
    relationship_status: str | None = None
    field_status: tuple[tuple[str, str], ...] = ()
    field_sources: tuple[tuple[str, str], ...] = ()
    canonical_profile_url: str | None = None


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: str
    name: str | None
    profile_url: str
    source: str
    source_url: str
    phone_numbers: tuple[str, ...] = ()
    phone_sources: tuple[str, ...] = ()
    website: str | None = None
    address: str | None = None
    current_city: str | None = None
    hometown: str | None = None
    birth_date: str | None = None
    birth_year: int | None = None
    bio: str | None = None
    workplace: str | None = None
    education: str | None = None
    gender: str | None = None
    languages: tuple[str, ...] = ()
    relationship_status: str | None = None
    field_status: tuple[tuple[str, str], ...] = ()
    field_sources: tuple[tuple[str, str], ...] = ()
    first_seen: str | None = None
    last_seen: str | None = None
    last_enriched_at: str | None = None
    commented: bool = False
    reacted: bool = False
    reaction_types: tuple[str, ...] = ()
    interaction_count: int = 0


@dataclass(frozen=True, slots=True)
class MessageRecord:
    message_id: str
    sender_name: str | None
    sender_profile_url: str | None
    text: str
    sent_at: str | None
    thread_url: str
    source: str = "messages"
    first_seen: str | None = None
    last_seen: str | None = None


@dataclass(frozen=True, slots=True)
class InspectRecord:
    target_url: str
    target_action: str
    session_valid: bool
    document_ready: bool
    main_found: bool
    dialog_count: int
    visible_profile_links: int
    message_rows: int
    profile_field_labels: int
    parser_version: str = "1"


@dataclass(frozen=True, slots=True)
class ScrapeIssue:
    code: str
    message: str
    target: str | None
    mode: ScrapeMode
    action: str
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class ScrapeStats:
    requested: int
    discovered: int
    succeeded: int
    failed: int


@dataclass(frozen=True, slots=True)
class EnrichmentStats:
    selected: int
    attempted: int
    succeeded: int
    failed: int
    phone_found: int
    address_found: int
    current_city_found: int
    hometown_found: int
    birth_year_found: int


@dataclass(frozen=True, slots=True)
class ScrapeResult(Generic[RecordT]):
    records: tuple[RecordT, ...]
    issues: tuple[ScrapeIssue, ...]
    stats: ScrapeStats
    enrichment: EnrichmentStats | None = None

    @property
    def has_failures(self) -> bool:
        return self.stats.failed > 0 or bool(self.issues)


@dataclass(frozen=True, slots=True)
class AuthenticatedBatchResult:
    user_result: ScrapeResult[UserRecord]
    message_result: ScrapeResult[MessageRecord]
    inspect_result: ScrapeResult[InspectRecord]
    stats: ScrapeStats
    issues: tuple[ScrapeIssue, ...]
    enrichment: EnrichmentStats | None = None

    @property
    def records(self) -> tuple[UserRecord | MessageRecord | InspectRecord, ...]:
        return (
            *self.user_result.records,
            *self.message_result.records,
            *self.inspect_result.records,
        )

    @property
    def has_failures(self) -> bool:
        return self.stats.failed > 0 or bool(self.issues)
