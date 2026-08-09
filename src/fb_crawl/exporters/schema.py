from __future__ import annotations

from urllib.parse import unquote, urlparse

from fb_crawl.core.models import (
    ContactKind,
    PageRecord,
    ScrapeIssue,
    UserRecord,
)
from fb_crawl.core.urls import FACEBOOK_INTERNAL_PATHS


UNIFIED_FIELDS = (
    "user_id",
    "name",
    "username",
    "page_name",
    "category",
    "website",
    "address",
    "current_city",
    "hometown",
    "birth_date",
    "birth_year",
    "bio",
    "workplace",
    "education",
    "gender",
    "languages",
    "relationship_status",
    "phone_numbers",
    "phone_sources",
    "field_status",
    "field_sources",
    "first_seen",
    "last_seen",
    "last_enriched_at",
    "commented",
    "reacted",
    "reaction_types",
    "interaction_count",
    "profile_url",
    "source",
    "source_url",
    "depth",
    "error_code",
    "error_message",
)


def _username(profile_url: str, user_id: str = "") -> str:
    if user_id and not user_id.isdigit():
        return user_id

    parsed = urlparse(profile_url)
    parts = [unquote(part) for part in parsed.path.split("/") if part]

    if len(parts) != 1:
        return ""

    candidate = parts[0]
    if candidate.casefold() == "profile.php":
        return ""

    if candidate.casefold() in FACEBOOK_INTERNAL_PATHS:
        return ""

    return candidate


def page_record_row(record: PageRecord) -> dict[str, str]:
    phones = [
        contact for contact in record.contacts if contact.kind is ContactKind.PHONE
    ]
    user_id = record.uid or ""
    name = record.page_name or ""

    return {
        "user_id": user_id,
        "name": name,
        "username": _username(record.canonical_url, user_id),
        "page_name": name,
        "category": record.category or "",
        "website": record.website or "",
        "address": record.address or "",
        "current_city": "",
        "hometown": "",
        "birth_date": "",
        "birth_year": "",
        "bio": "",
        "workplace": "",
        "education": "",
        "gender": "",
        "languages": "",
        "relationship_status": "",
        "phone_numbers": "; ".join(contact.value for contact in phones),
        "phone_sources": "; ".join(
            source for contact in phones for source in contact.sources
        ),
        "field_status": "",
        "field_sources": "",
        "first_seen": "",
        "last_seen": "",
        "last_enriched_at": "",
        "commented": "",
        "reacted": "",
        "reaction_types": "",
        "interaction_count": "",
        "profile_url": record.canonical_url,
        "source": record.discovery_source,
        "source_url": record.canonical_url,
        "depth": str(record.depth),
        "error_code": "",
        "error_message": "",
    }


def user_record_row(record: UserRecord) -> dict[str, str]:
    return {
        "user_id": record.user_id if record.user_id.isdigit() else "",
        "name": record.name or "",
        "username": (
            record.username
            or _username(record.profile_url, record.user_id)
        ),
        "page_name": "",
        "category": "",
        "website": record.website or "",
        "address": record.address or "",
        "current_city": record.current_city or "",
        "hometown": record.hometown or "",
        "birth_date": record.birth_date or "",
        "birth_year": "" if record.birth_year is None else str(record.birth_year),
        "bio": record.bio or "",
        "workplace": record.workplace or "",
        "education": record.education or "",
        "gender": record.gender or "",
        "languages": "; ".join(record.languages),
        "relationship_status": record.relationship_status or "",
        "phone_numbers": "; ".join(record.phone_numbers),
        "phone_sources": "; ".join(record.phone_sources),
        "field_status": "; ".join(
            f"{field}={status}" for field, status in record.field_status
        ),
        "field_sources": "; ".join(
            f"{field}={source}" for field, source in record.field_sources
        ),
        "first_seen": record.first_seen or "",
        "last_seen": record.last_seen or "",
        "last_enriched_at": record.last_enriched_at or "",
        "commented": "true" if record.commented else "false",
        "reacted": "true" if record.reacted else "false",
        "reaction_types": "; ".join(record.reaction_types),
        "interaction_count": str(record.interaction_count),
        "profile_url": record.profile_url,
        "source": record.source,
        "source_url": record.source_url,
        "depth": str(record.depth),
        "error_code": "",
        "error_message": "",
    }


def record_row(record: PageRecord | UserRecord) -> dict[str, str]:
    if isinstance(record, PageRecord):
        return page_record_row(record)

    if isinstance(record, UserRecord):
        return user_record_row(record)

    raise TypeError(f"Unsupported output record: {type(record).__name__}.")


def issue_row(issue: ScrapeIssue) -> dict[str, str]:
    return {
        "user_id": "",
        "name": "",
        "username": "",
        "page_name": "",
        "category": "",
        "website": "",
        "address": "",
        "current_city": "",
        "hometown": "",
        "birth_date": "",
        "birth_year": "",
        "bio": "",
        "workplace": "",
        "education": "",
        "gender": "",
        "languages": "",
        "relationship_status": "",
        "phone_numbers": "",
        "phone_sources": "",
        "field_status": "",
        "field_sources": "",
        "first_seen": "",
        "last_seen": "",
        "last_enriched_at": "",
        "commented": "",
        "reacted": "",
        "reaction_types": "",
        "interaction_count": "",
        "profile_url": "",
        "source": issue.action,
        "source_url": issue.target or "",
        "depth": "",
        "error_code": issue.code,
        "error_message": issue.message,
    }
