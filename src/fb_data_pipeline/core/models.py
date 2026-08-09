from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import parse_qs, unquote, urlparse


class ProviderStatus(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    FAILED = "failed"


class PhoneSlot(StrEnum):
    PHONE_1 = "phone_1"
    PHONE_2 = "phone_2"


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def canonical_profile_url(value: str | None) -> str:
    raw = _clean(value)
    if not raw:
        return ""

    parsed = urlparse(raw)
    host = parsed.netloc.casefold().removeprefix("www.")
    if host not in {"facebook.com", "m.facebook.com", "mbasic.facebook.com"}:
        return raw

    path = "/" + "/".join(
        unquote(part) for part in parsed.path.split("/") if part
    )
    if path.casefold() == "/profile.php":
        uid = _clean(parse_qs(parsed.query).get("id", [""])[0])
        return f"https://www.facebook.com/profile.php?id={uid}" if uid else ""

    return f"https://www.facebook.com{path.rstrip('/')}" if path != "/" else ""


@dataclass(frozen=True, slots=True)
class FacebookIdentity:
    uid: str = ""
    username: str = ""
    name: str = ""
    profile_url: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "uid", _clean(self.uid))
        object.__setattr__(self, "username", _clean(self.username))
        object.__setattr__(self, "name", _clean(self.name))
        object.__setattr__(
            self,
            "profile_url",
            canonical_profile_url(self.profile_url),
        )

    @property
    def is_usable(self) -> bool:
        return bool(self.uid or self.username or self.profile_url)

    @property
    def aliases(self) -> tuple[str, ...]:
        aliases: list[str] = []
        if self.uid:
            aliases.append(f"uid:{self.uid}")
        if self.profile_url:
            aliases.append(f"profile:{self.profile_url.casefold()}")
        if self.username:
            aliases.append(f"username:{self.username.casefold()}")
        return tuple(aliases)


@dataclass(frozen=True, slots=True)
class ProfileData:
    address: str = ""
    birth_date: str = ""
    gender: str = ""
    source_url: str = ""
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "address", _clean(self.address))
        object.__setattr__(self, "birth_date", _clean(self.birth_date))
        object.__setattr__(self, "gender", _clean(self.gender))
        object.__setattr__(self, "source_url", _clean(self.source_url))

    @property
    def is_empty(self) -> bool:
        return not (self.address or self.birth_date or self.gender)


@dataclass(frozen=True, slots=True)
class PhoneEvidence:
    phone_number: str
    normalized_phone: str
    source: str
    source_url: str = ""
    captured_at: datetime | None = None
    confidence: str = "unknown"
    provider: str = ""
    correlation_id: str = ""

    @property
    def slot(self) -> PhoneSlot:
        provider = self.provider.casefold()
        source = self.source.casefold()
        if provider == "fbnumber" or source.startswith("external:fbnumber"):
            return PhoneSlot.PHONE_1
        return PhoneSlot.PHONE_2

    @property
    def dedupe_key(self) -> tuple[str, str, str, str]:
        return (
            self.normalized_phone,
            self.source,
            self.source_url,
            self.provider,
        )


@dataclass(frozen=True, slots=True)
class UserBundle:
    identity: FacebookIdentity
    evidence: tuple[PhoneEvidence, ...] = ()
    profile: ProfileData = field(default_factory=ProfileData)

    def _first_phone(self, slot: PhoneSlot) -> str | None:
        return next(
            (
                item.normalized_phone
                for item in self.evidence
                if item.slot is slot
            ),
            None,
        )

    @property
    def phone_1(self) -> str | None:
        """First normalized number returned by FBNumber."""
        return self._first_phone(PhoneSlot.PHONE_1)

    @property
    def phone_2(self) -> str | None:
        """First normalized number observed directly by fb-crawl."""
        return self._first_phone(PhoneSlot.PHONE_2)


@dataclass(frozen=True, slots=True)
class RetryCandidate:
    user_id: int
    identity: FacebookIdentity
    status: ProviderStatus
    checked_at: datetime
    error_code: str = ""

    def __post_init__(self) -> None:
        if self.user_id <= 0:
            raise ValueError("retry candidate user_id must be positive")
        if self.status not in {
            ProviderStatus.FAILED,
            ProviderStatus.RATE_LIMITED,
        }:
            raise ValueError("retry candidate status must be retryable")
        object.__setattr__(self, "error_code", _clean(self.error_code))

    def to_bundle(self) -> UserBundle:
        return UserBundle(identity=self.identity)


@dataclass(frozen=True, slots=True)
class ProviderResult:
    provider: str
    status: ProviderStatus
    evidence: tuple[PhoneEvidence, ...] = ()
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str = ""
    error_code: str = ""
