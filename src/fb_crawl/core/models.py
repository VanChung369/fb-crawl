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
    action: PublicAction | str
    targets: tuple[str, ...]
    keyword: str | None = None
    target_kind: TargetKind = TargetKind.PAGES
    limit: int = 20
    depth: int = 0
    max_nodes: int = 20
    delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be greater than 0")

        if self.depth < 0:
            raise ValueError("depth must be greater than or equal to 0")

        if self.max_nodes <= 0:
            raise ValueError("max_nodes must be greater than 0")

        if self.delay_seconds < 0:
            raise ValueError("delay_seconds must be greater than or equal to 0")


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
    contacts: tuple[ContactRecord, ...] = ()
    metadata: Mapping[str, JsonScalar] = field(default_factory=dict)
    depth: int = 0
    discovery_source: str = "seed"

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
class ScrapeResult(Generic[RecordT]):
    records: tuple[RecordT, ...]
    issues: tuple[ScrapeIssue, ...]
    stats: ScrapeStats

    @property
    def has_failures(self) -> bool:
        return self.stats.failed > 0 or bool(self.issues)