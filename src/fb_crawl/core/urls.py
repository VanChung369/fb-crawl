from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import (
    parse_qs,
    urlencode,
    urljoin,
    urlparse,
)

from fb_crawl.core.models import (
    AuthenticatedAction,
    ProfileField,
    TargetKind,
)

FACEBOOK_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "mbasic.facebook.com",
    "web.facebook.com",
}

MESSENGER_HOSTS = {
    "messenger.com",
    "www.messenger.com",
}

AUTHENTICATED_ID = re.compile(r"[A-Za-z0-9._-]+")


FACEBOOK_INTERNAL_PATHS = {
    "about",
    "business",
    "careers",
    "checkpoint",
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
    "profile.php",
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
    "two_step_verification",
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


def _facebook_parts(
    value: str | None,
) -> tuple[list[str], dict[str, list[str]]] | None:
    if not value:
        return None

    parsed = urlparse(
        _absolute_candidate(
            value,
            None,
        )
    )

    host = parsed.netloc.lower().split(":")[0]

    if host not in FACEBOOK_HOSTS:
        return None

    parts = [part for part in parsed.path.split("/") if part]

    return parts, parse_qs(parsed.query)


def _valid_authenticated_id(value: str) -> bool:
    return AUTHENTICATED_ID.fullmatch(value) is not None


def normalize_members_url(
    value: str | None,
) -> str | None:
    parsed = _facebook_parts(value)

    if parsed is None:
        return None

    parts, _ = parsed

    if len(parts) not in {2, 3}:
        return None

    if parts[0].lower() != "groups":
        return None

    if not _valid_authenticated_id(parts[1]):
        return None

    if len(parts) == 3 and parts[2].lower() != "members":
        return None

    return "https://www.facebook.com/groups/" f"{parts[1]}/members"


def normalize_comments_url(
    value: str | None,
) -> str | None:
    parsed = _facebook_parts(value)

    if parsed is None:
        return None

    parts, query = parsed
    lowered = [part.lower() for part in parts]

    # Group post:
    # /groups/<group-id>/posts/<post-id>
    if (
        len(parts) == 4
        and lowered[0] == "groups"
        and lowered[2] == "posts"
        and all(_valid_authenticated_id(item) for item in (parts[1], parts[3]))
    ):
        return "https://www.facebook.com/groups/" f"{parts[1]}/posts/{parts[3]}"

    # Page post or video:
    # /<page>/posts/<id>
    # /<page>/videos/<id>
    if (
        len(parts) == 3
        and lowered[1] in {"posts", "videos"}
        and lowered[0] not in FACEBOOK_INTERNAL_PATHS
        and all(_valid_authenticated_id(item) for item in (parts[0], parts[2]))
    ):
        return "https://www.facebook.com/" f"{parts[0]}/{lowered[1]}/{parts[2]}"

    # Reel:
    # /reel/<id>
    if len(parts) == 2 and lowered[0] == "reel" and _valid_authenticated_id(parts[1]):
        return "https://www.facebook.com/reel/" f"{parts[1]}"

    # permalink.php?story_fbid=<id>&id=<owner-id>
    if len(parts) == 1 and lowered[0] == "permalink.php":
        story_id = query.get(
            "story_fbid",
            [""],
        )[0]

        owner_id = query.get(
            "id",
            [""],
        )[0]

        if not _valid_authenticated_id(story_id):
            return None

        values = [("story_fbid", story_id)]

        if _valid_authenticated_id(owner_id):
            values.append(("id", owner_id))

        return "https://www.facebook.com/permalink.php?" + urlencode(values)

    # photo.php?fbid=<id>&id=<owner-id>
    if len(parts) == 1 and lowered[0] == "photo.php":
        photo_id = query.get(
            "fbid",
            [""],
        )[0]

        owner_id = query.get(
            "id",
            [""],
        )[0]

        if not _valid_authenticated_id(photo_id):
            return None

        values = [("fbid", photo_id)]

        if _valid_authenticated_id(owner_id):
            values.append(("id", owner_id))

        return "https://www.facebook.com/photo.php?" + urlencode(values)

    return None


def profile_identity_url(
    value: str | None,
) -> tuple[str, str] | None:
    """Return the stable profile identity and normalized profile URL."""
    normalized = normalize_facebook_url(value)

    if normalized is None:
        return None

    parsed = urlparse(normalized)
    parts = [part for part in parsed.path.split("/") if part]

    if len(parts) != 1:
        return None

    if parts[0].casefold() == "profile.php":
        profile_id = parse_qs(parsed.query).get("id", [""])[0]
        return (profile_id, normalized) if profile_id.isdigit() else None

    return parts[0], normalized


def normalize_profile_collection_url(
    value: str | None,
    collection: str,
) -> str | None:
    """Normalize a profile or profile collection URL to friends/followers."""
    if collection not in {"friends", "followers"}:
        raise ValueError("Unsupported profile collection.")

    parsed = _facebook_parts(value)

    if parsed is None:
        return None

    parts, query = parsed

    if len(parts) == 2 and parts[1].casefold() in {"friends", "followers"}:
        if parts[1].casefold() != collection:
            return None

        identity = profile_identity_url(f"https://www.facebook.com/{parts[0]}")
    elif len(parts) == 1 and parts[0].casefold() == "profile.php":
        profile_id = query.get("id", [""])[0]
        section = query.get("sk", [collection])[0].casefold()

        if not profile_id.isdigit() or section != collection:
            return None

        return (
            "https://www.facebook.com/profile.php?"
            + urlencode((("id", profile_id), ("sk", collection)))
        )
    else:
        identity = profile_identity_url(value)

    if identity is None:
        return None

    _, profile_url = identity
    return f"{profile_url}/{collection}"


def normalize_reactions_url(value: str | None) -> str | None:
    """Reactions are opened from the same supported post URLs as comments."""
    return normalize_comments_url(value)


def normalize_messages_url(value: str | None) -> str | None:
    if not value:
        return None

    parsed = urlparse(_absolute_candidate(value, None))
    host = parsed.netloc.casefold().split(":")[0]
    parts = [part for part in parsed.path.split("/") if part]

    if host in FACEBOOK_HOSTS:
        valid_shape = (
            len(parts) == 3
            and parts[0].casefold() == "messages"
            and parts[1] == "t"
        )
    elif host in MESSENGER_HOSTS:
        valid_shape = len(parts) == 2 and parts[0] == "t"
    else:
        return None

    if not valid_shape:
        return None

    thread_id = parts[-1]

    if not _valid_authenticated_id(thread_id):
        return None

    return f"https://www.facebook.com/messages/t/{thread_id}"


def profile_directory_urls(
    profile_url: str | None,
    user_id: str,
) -> tuple[str, ...]:
    parsed = _facebook_parts(profile_url)

    if parsed is None or not _valid_authenticated_id(user_id):
        return ()

    parts, query = parsed

    if len(parts) != 1:
        return ()

    first = parts[0]
    lowered = first.lower()

    if lowered == "profile.php":
        profile_id = query.get("id", [""])[0]

        if not profile_id.isdigit() or user_id != profile_id:
            return ()

        base = "https://www.facebook.com/profile.php"
        return tuple(
            f"{base}?{urlencode((('id', profile_id), ('sk', section)))}"
            for section in (
                "directory_personal_details",
                "directory_links",
            )
        )

    if lowered in FACEBOOK_INTERNAL_PATHS or first.casefold() != user_id.casefold():
        return ()

    if not _valid_authenticated_id(first):
        return ()

    base = f"https://www.facebook.com/{first}"
    return (
        f"{base}/directory_personal_details",
        f"{base}/directory_links",
    )


PROFILE_FIELD_SECTIONS = {
    ProfileField.PHONE: "directory_personal_details",
    ProfileField.ADDRESS: "directory_personal_details",
    ProfileField.CURRENT_CITY: "directory_personal_details",
    ProfileField.HOMETOWN: "directory_personal_details",
    ProfileField.BIRTH_DATE: "directory_personal_details",
    ProfileField.BIO: "directory_personal_details",
    ProfileField.GENDER: "directory_personal_details",
    ProfileField.LANGUAGES: "directory_personal_details",
    ProfileField.RELATIONSHIP_STATUS: "directory_personal_details",
    ProfileField.WORKPLACE: "directory_work",
    ProfileField.EDUCATION: "directory_work",
    ProfileField.WEBSITE: "directory_links",
}


def profile_enrichment_urls(
    profile_url: str | None,
    user_id: str,
    fields: tuple[ProfileField, ...],
) -> tuple[str, ...]:
    parsed = _facebook_parts(profile_url)

    if parsed is None or not _valid_authenticated_id(user_id):
        return ()

    parts, query = parsed
    requested = fields or tuple(ProfileField)
    sections = tuple(
        dict.fromkeys(PROFILE_FIELD_SECTIONS[field] for field in requested)
    )

    if len(parts) != 1:
        return ()

    first = parts[0]
    lowered = first.casefold()

    if lowered == "profile.php":
        profile_id = query.get("id", [""])[0]

        if not profile_id.isdigit() or profile_id != user_id:
            return ()

        base = "https://www.facebook.com/profile.php"
        return tuple(
            f"{base}?{urlencode((('id', profile_id), ('sk', section)))}"
            for section in sections
        )

    if lowered in FACEBOOK_INTERNAL_PATHS or first.casefold() != user_id.casefold():
        return ()

    if not _valid_authenticated_id(first):
        return ()

    base = f"https://www.facebook.com/{first}"
    return tuple(f"{base}/{section}" for section in sections)


def profile_about_urls(
    profile_url: str | None,
    user_id: str,
) -> tuple[str, ...]:
    """Backward-compatible alias for the current profile directory routes."""
    return profile_directory_urls(profile_url, user_id)


def classify_authenticated_url(
    value: str | None,
) -> tuple[AuthenticatedAction, str] | None:
    members = normalize_members_url(value)

    if members is not None:
        return (
            AuthenticatedAction.MEMBERS,
            members,
        )

    comments = normalize_comments_url(value)

    if comments is not None:
        return (
            AuthenticatedAction.COMMENTS,
            comments,
        )

    messages = normalize_messages_url(value)

    if messages is not None:
        return (AuthenticatedAction.MESSAGES, messages)

    parsed = _facebook_parts(value)

    if parsed is not None:
        parts, query = parsed
        section = query.get("sk", [""])[0].casefold()

        if (
            len(parts) == 2
            and parts[1].casefold() in {"friends", "followers"}
        ) or section in {"friends", "followers"}:
            collection = parts[1].casefold() if len(parts) == 2 else section
            normalized = normalize_profile_collection_url(value, collection)
            if normalized is not None:
                return AuthenticatedAction(collection), normalized

    profile = profile_identity_url(value)

    if profile is not None:
        return AuthenticatedAction.PROFILE, profile[1]

    return None


def classify_authenticated_batch_target(
    value: str | None,
) -> tuple[AuthenticatedAction, str] | None:
    if not value:
        return None

    prefix, separator, raw_target = value.partition(":")
    typed_actions = {
        action.value: action
        for action in AuthenticatedAction
        if action is not AuthenticatedAction.BATCH
    }

    if not separator or prefix.casefold() not in typed_actions:
        return classify_authenticated_url(value)

    action = typed_actions[prefix.casefold()]
    target = raw_target.strip()

    if action is AuthenticatedAction.INSPECT:
        classified = classify_authenticated_url(target)
        normalized = classified[1] if classified is not None else None
    elif action is AuthenticatedAction.MEMBERS:
        normalized = normalize_members_url(target)
    elif action is AuthenticatedAction.COMMENTS:
        normalized = normalize_comments_url(target)
    elif action is AuthenticatedAction.PROFILE:
        identity = profile_identity_url(target)
        normalized = identity[1] if identity is not None else None
    elif action in {
        AuthenticatedAction.FRIENDS,
        AuthenticatedAction.FOLLOWERS,
    }:
        normalized = normalize_profile_collection_url(target, action.value)
    elif action in {
        AuthenticatedAction.REACTIONS,
        AuthenticatedAction.ENGAGEMENT,
    }:
        normalized = normalize_reactions_url(target)
    else:
        normalized = normalize_messages_url(target)

    return (action, normalized) if normalized is not None else None


def classify_inspect_target(
    value: str | None,
) -> tuple[AuthenticatedAction, str] | None:
    if not value:
        return None

    typed = classify_authenticated_batch_target(value)
    if typed is not None and typed[0] is AuthenticatedAction.INSPECT:
        classified = classify_authenticated_url(typed[1])
        return classified

    return classify_authenticated_url(value)
