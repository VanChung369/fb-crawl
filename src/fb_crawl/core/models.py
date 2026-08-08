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
    BATCH = "batch"


class ProfileField(StrEnum):
    PHONE = "phone"
    WEBSITE = "website"
    ADDRESS = "address"
    CURRENT_CITY = "current_city"
    HOMETOWN = "hometown"
    BIRTH_DATE = "birth_date"


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
    phone_numbers: tuple[str, ...] = ()
    phone_sources: tuple[str, ...] = ()
    website: str | None = None
    address: str | None = None
    current_city: str | None = None
    hometown: str | None = None
    birth_date: str | None = None
    birth_year: int | None = None


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
