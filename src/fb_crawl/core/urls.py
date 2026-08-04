from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import parse_qs, urljoin, urlparse

from fb_crawl.core.models import TargetKind

FACEBOOK_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "mbasic.facebook.com",
    "web.facebook.com",
}


FACEBOOK_INTERNAL_PATHS = {
    "about",
    "business",
    "careers",
    "events",
    "friends",
    "gaming",
    "groups",
    "help",
    "home.php",
    "legal",
    "lite",
    "login",
    "marketplace",
    "messages",
    "notifications",
    "pages",
    "people",
    "photo",
    "photo.php",
    "photos",
    "places",
    "plugins",
    "policies",
    "privacy",
    "public",
    "qr_code_login",
    "recover",
    "reel",
    "reels",
    "search",
    "security",
    "settings",
    "share",
    "sharer",
    "stories",
    "story.php",
    "watch",
}

FACEBOOK_ASSET_EXTENSIONS = (
    ".avif",
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".m4s",
    ".map",
    ".mp3",
    ".mp4",
    ".mpd",
    ".png",
    ".svg",
    ".txt",
    ".wasm",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".xml",
)


def _absolute_candidate(
    value: str,
    base_url: str | None,
) -> str:
    candidate = value.strip().replace("\\/", "/")

    if candidate.startswith("//"):
        return f"https:{candidate}"

    if candidate.startswith("/"):
        return urljoin(
            base_url or "https://www.facebook.com",
            candidate,
        )

    return candidate


def normalize_facebook_url(
    value: str | None,
    *,
    base_url: str | None = None,
) -> str | None:
    if not value:
        return None

    parsed = urlparse(_absolute_candidate(value, base_url))
    host = parsed.netloc.lower().split(":")[0]

    if host not in FACEBOOK_HOSTS:
        return None

    parts = [part for part in parsed.path.split("/") if part]

    if not parts:
        return None

    first = parts[0]
    lowered = first.lower()

    if lowered == "profile.php":
        profile_id = parse_qs(parsed.query).get(
            "id",
            [""],
        )[0]

        if profile_id.isdigit():
            return "https://www.facebook.com/" f"profile.php?id={profile_id}"

        return None

    if lowered == "people":
        profile_id = parts[-1]

        if profile_id.isdigit():
            return "https://www.facebook.com/" f"profile.php?id={profile_id}"

        return None

    if lowered in FACEBOOK_INTERNAL_PATHS:
        return None

    if lowered.endswith(FACEBOOK_ASSET_EXTENSIONS):
        return None

    if re.fullmatch(r"[A-Za-z0-9._-]+", first) is None:
        return None

    return f"https://www.facebook.com/{first}"


def normalize_group_url(
    value: str | None,
    *,
    base_url: str | None = None,
) -> str | None:
    if not value:
        return None

    parsed = urlparse(_absolute_candidate(value, base_url))
    host = parsed.netloc.lower().split(":")[0]

    if host not in FACEBOOK_HOSTS:
        return None

    parts = [part for part in parsed.path.split("/") if part]

    if len(parts) < 2:
        return None

    if parts[0].lower() != "groups":
        return None

    group_id = parts[1]

    if re.fullmatch(r"[A-Za-z0-9._-]+", group_id) is None:
        return None

    return f"https://www.facebook.com/groups/{group_id}"


def facebook_url_kind(
    value: str | None,
) -> TargetKind | None:
    normalized = normalize_facebook_url(value)

    if normalized is None:
        return None

    if "/profile.php?id=" in normalized:
        return TargetKind.PEOPLE

    return TargetKind.PAGES


def url_matches_target(
    value: str | None,
    target: TargetKind,
) -> bool:
    kind = facebook_url_kind(value)

    if target is TargetKind.ALL:
        return kind is not None

    return kind is target


def canonicalize_targets(
    values: Iterable[str],
    *,
    target: TargetKind,
    limit: int,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for value in values:
        normalized = normalize_facebook_url(value)

        if normalized is None:
            continue

        if not url_matches_target(normalized, target):
            continue

        if normalized in seen:
            continue

        seen.add(normalized)
        result.append(normalized)

        if len(result) >= limit:
            break

    return result
