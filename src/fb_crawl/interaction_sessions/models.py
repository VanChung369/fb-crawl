from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.urls import FACEBOOK_HOSTS, FACEBOOK_INTERNAL_PATHS, normalize_comments_url


class SessionError(ValidationError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


def require_time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Session timestamps must be timezone-aware.")


def require_text(value: str, maximum: int, *, blank: bool = True) -> None:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value or (not blank and not value.strip()):
        raise ValidationError("Invalid session text.")


def require_uuid(value: UUID) -> None:
    if not isinstance(value, UUID):
        raise ValidationError("Invalid session identifier.")


def friends_url(value: str) -> str | None:
    try:
        parsed = urlparse(value)
        query = parse_qs(parsed.query)
        if parsed.scheme != 'https' or parsed.hostname not in FACEBOOK_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        if any(key in query for key in ('next', 'redirect', 'redirect_uri', 'u', 'url')):
            return None
        parts = parsed.path.strip('/').split('/')
        if parts == ['profile.php'] and query.get('sk') == ['friends']:
            uid = query.get('id', [''])[0]
            if re.fullmatch(r'[1-9][0-9]{4,19}', uid):
                return f'https://www.facebook.com/profile.php?id={uid}&sk=friends'
        if len(parts) == 2 and parts[1] == 'friends' and parts[0].lower() not in FACEBOOK_INTERNAL_PATHS and re.fullmatch(r'[a-zA-Z0-9.]{1,100}', parts[0]):
            if parts[0].isdigit():
                return f'https://www.facebook.com/profile.php?id={parts[0]}&sk=friends' if re.fullmatch(r'[1-9][0-9]{4,19}', parts[0]) else None
            return f'https://www.facebook.com/{parts[0].lower()}/friends'
    except ValueError:
        pass
    return None


def source_url(value: str) -> str:
    require_text(value, 2048, blank=False)
    try:
        parsed = urlparse(value)
        valid = parsed.scheme == "https" and parsed.hostname in FACEBOOK_HOSTS and not parsed.username and not parsed.password and parsed.port in (None, 443)
    except ValueError:
        valid = False
    normalized = (friends_url(value) or normalize_comments_url(value)) if valid else None
    if not normalized:
        raise ValidationError("A supported Facebook post URL is required.")
    return normalized


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    facebook_uid: str = ""
    username: str = ""
    name: str = ""
    profile_url: str = ""

    def __post_init__(self):
        for name, length in (("facebook_uid", 20), ("username", 100), ("name", 512), ("profile_url", 2048)):
            value = getattr(self, name)
            require_text(value, length)
            object.__setattr__(self, name, value.strip())
        object.__setattr__(self, "username", self.username.lower())
        if self.facebook_uid and not re.fullmatch(r"[1-9][0-9]{4,19}", self.facebook_uid):
            raise ValidationError("Invalid Facebook UID.")
        if self.username and not re.fullmatch(r"[a-z0-9.]{1,100}", self.username):
            raise ValidationError("Invalid Facebook username.")
        if not self.facebook_uid and not self.username:
            raise ValidationError("A Facebook UID or username is required.")
        if self.profile_url:
            try:
                parsed = urlparse(self.profile_url)
                valid = parsed.scheme == "https" and parsed.hostname in FACEBOOK_HOSTS and not parsed.username and not parsed.password and parsed.port in (None, 443)
            except ValueError:
                valid = False
            if not valid:
                raise ValidationError("Invalid Facebook profile URL.")
            path = parsed.path.strip("/")
            uid = parse_qs(parsed.query).get("id", [""])[0] if path == "profile.php" else path if path.isdigit() else ""
            if uid:
                if not self.facebook_uid or uid != self.facebook_uid:
                    raise ValidationError("Conflicting Facebook UID.")
            elif not self.username or path.lower() != self.username:
                raise ValidationError("Conflicting Facebook username.")

    @property
    def key(self) -> str:
        return f"uid:{self.facebook_uid}" if self.facebook_uid else f"username:{self.username}"


@dataclass(frozen=True, slots=True)
class SessionCreate:
    client_session_id: UUID
    source_url: str
    kind: str

    def __post_init__(self):
        require_uuid(self.client_session_id)
        object.__setattr__(self, "source_url", source_url(self.source_url))
        if self.kind not in ("comments", "reactions", "friends"):
            raise ValidationError("Invalid session kind.")
        if (self.kind == 'friends') != bool(friends_url(self.source_url)):
            raise ValidationError('Session kind does not match the source URL.')


@dataclass(frozen=True, slots=True)
class SessionRowInput:
    client_row_id: UUID
    row_revision: int
    interaction_id: str
    synthetic: bool
    parent_id: str
    kind: str
    identity: SessionIdentity
    text: str
    observed_at: datetime

    def __post_init__(self):
        require_uuid(self.client_row_id)
        if type(self.row_revision) is not int or not 1 <= self.row_revision <= 2147483647:
            raise ValidationError("Invalid interaction revision.")
        if type(self.synthetic) is not bool or self.kind not in ("comment", "reply", "reaction", "friend") or not isinstance(self.identity, SessionIdentity):
            raise ValidationError("Invalid interaction.")
        require_text(self.interaction_id, 512, blank=False)
        require_text(self.parent_id, 512)
        require_text(self.text, 10000)
        require_time(self.observed_at)


@dataclass(frozen=True, slots=True)
class SessionCounters:
    interactions: int = 0
    people: int = 0
    processed: int = 0
    found: int = 0
    failures: int = 0


@dataclass(frozen=True, slots=True)
class SessionSummary:
    id: UUID
    client_session_id: UUID
    source_url: str
    kind: str
    revision: int
    status: str
    created_at: datetime
    updated_at: datetime
    counters: SessionCounters


@dataclass(frozen=True, slots=True)
class AcceptedRow:
    client_row_id: UUID
    row_revision: int
    person_id: UUID


@dataclass(frozen=True, slots=True)
class BatchAck:
    revision: int
    accepted: tuple[AcceptedRow, ...]


@dataclass(frozen=True, slots=True)
class SessionRow(SessionRowInput):
    person_id: UUID
    lookup_event_id: int | None = None
    contact: object | None = None


@dataclass(frozen=True, slots=True)
class SessionFilters:
    source_url: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    status: str | None = None

    def __post_init__(self):
        if self.source_url is not None:
            object.__setattr__(self, "source_url", source_url(self.source_url))
        if self.status is not None and self.status not in ("running", "stopped"):
            raise ValidationError("Invalid session status.")
        for value in (self.created_from, self.created_to):
            if value is not None:
                require_time(value)
        if self.created_from and self.created_to and self.created_from > self.created_to:
            raise ValidationError("Invalid session date range.")


@dataclass(frozen=True, slots=True)
class RowFilters:
    author: str | None = None
    text: str | None = None
    kind: str | None = None
    outcome: str | None = None

    def __post_init__(self):
        for value in (self.author, self.text):
            if value is not None:
                require_text(value, 512)
        if self.kind is not None and self.kind not in ("comment", "reply", "reaction", "friend"):
            raise ValidationError("Invalid interaction kind filter.")
        if self.outcome is not None and self.outcome not in ("not_looked_up", "processing", "found", "not_found", "failed", "quota_exceeded", "unavailable"):
            raise ValidationError("Invalid lookup outcome filter.")


def _fingerprint(scope, filters) -> str:
    return sha256(json.dumps([scope, asdict(filters)], sort_keys=True, default=str).encode()).hexdigest()


def encode_page_cursor(time: datetime, identifier: UUID, scope: str, filters) -> str:
    return base64.urlsafe_b64encode(json.dumps([time.isoformat(), str(identifier), _fingerprint(scope, filters)]).encode()).decode()


def decode_page_cursor(token: str, scope: str, filters) -> tuple[datetime, UUID]:
    try:
        if not isinstance(token, str) or len(token) > 1024:
            raise ValueError()
        time, identifier, fingerprint = json.loads(base64.b64decode(token, altchars=b"-_", validate=True))
        if fingerprint != _fingerprint(scope, filters):
            raise ValueError()
        parsed = datetime.fromisoformat(time)
        require_time(parsed)
        return parsed, UUID(identifier)
    except (ValueError, TypeError, binascii.Error) as error:
        raise ValidationError("Invalid session cursor.") from error
