from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from urllib.parse import (
    parse_qs,
    urljoin,
    urlparse,
)

from bs4 import BeautifulSoup

from fb_crawl.core.models import UserRecord
from fb_crawl.core.urls import (
    FACEBOOK_HOSTS,
    FACEBOOK_INTERNAL_PATHS,
)

FACEBOOK_BASE = "https://www.facebook.com"

USER_ID = re.compile(r"[A-Za-z0-9._-]+")

PROFILE_LINK_CLASS = "_a6hd"

ACTION_LABELS = frozenset(
    item.casefold()
    for item in (
        "Reply",
        "Share",
        "Like",
        "Trả lời",
        "Thích",
        "Chia sẻ",
    )
)


def _name_candidates(anchor) -> tuple[str, ...]:
    candidates = [
        " ".join(anchor.stripped_strings).strip(),
        str(anchor.get("aria-label") or "").strip(),
    ]
    candidates.extend(
        str(image.get("alt") or "").strip()
        for image in anchor.find_all("img")
    )
    return tuple(dict.fromkeys(item for item in candidates if item))


def _name(anchor) -> str | None:
    candidates = _name_candidates(anchor)
    return candidates[0] if candidates else None


def _is_action_label(
    name: str | None,
) -> bool:
    return name is not None and name.casefold() in ACTION_LABELS


def _ascii_fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )


SOCIAL_CONTEXT_LABEL = re.compile(
    r"^(?:\d[\d\s.,]*[kmbt]?)\s+(?:"
    r"friends?|followers?|following|mutual friends?|"
    r"ban be|nguoi ban|nguoi theo doi|ban chung"
    r")$",
    re.IGNORECASE,
)


def _is_social_context_label(name: str | None) -> bool:
    if name is None:
        return False

    folded = " ".join(_ascii_fold(name).split())
    return bool(SOCIAL_CONTEXT_LABEL.fullmatch(folded))


def _profile_name(anchor) -> str | None:
    return next(
        (
            candidate
            for candidate in _name_candidates(anchor)
            if not _is_action_label(candidate)
            and not _is_social_context_label(candidate)
        ),
        None,
    )


def _identity(
    anchor,
    *,
    allow_plain_profile_links: bool = False,
) -> tuple[str, str] | None:
    href = str(anchor.get("href") or "").replace("\\/", "/")

    if not href:
        return None

    absolute = urljoin(
        FACEBOOK_BASE,
        href,
    )

    parsed = urlparse(absolute)
    host = parsed.netloc.lower().split(":")[0]

    if host not in FACEBOOK_HOSTS:
        return None

    parts = [part for part in parsed.path.split("/") if part]

    query = parse_qs(parsed.query)

    if len(parts) >= 4 and parts[0].lower() == "groups" and parts[2].lower() == "user":
        user_id = parts[3]

    elif len(parts) == 1 and parts[0].lower() == "profile.php":
        user_id = query.get(
            "id",
            [""],
        )[0]

    elif len(parts) >= 2 and parts[0].lower() == "user":
        user_id = parts[1]

    elif (
        len(parts) == 1
        and (
            allow_plain_profile_links
            or PROFILE_LINK_CLASS in anchor.get("class", [])
        )
        and parts[0].lower() not in FACEBOOK_INTERNAL_PATHS
    ):
        user_id = parts[0]

    else:
        return None

    if USER_ID.fullmatch(user_id) is None:
        return None

    profile_url = (
        f"{FACEBOOK_BASE}/profile.php?id={user_id}"
        if user_id.isdigit()
        else f"{FACEBOOK_BASE}/{user_id}"
    )

    return user_id, profile_url


class UserParser:
    def __init__(self, *, allow_plain_profile_links: bool = False) -> None:
        self._allow_plain_profile_links = allow_plain_profile_links

    def parse(
        self,
        html: str,
        *,
        source: str,
        source_url: str,
    ) -> tuple[UserRecord, ...]:
        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        records: list[UserRecord] = []
        positions: dict[str, int] = {}

        for anchor in soup.find_all(
            "a",
            href=True,
        ):
            identity = _identity(
                anchor,
                allow_plain_profile_links=self._allow_plain_profile_links,
            )

            raw_name = _name(anchor)

            if identity is None or _is_action_label(raw_name):
                continue

            user_id, profile_url = identity
            name = _profile_name(anchor)

            if user_id in positions:
                index = positions[user_id]

                if records[index].name is None and name is not None:
                    records[index] = replace(records[index], name=name)

                continue

            positions[user_id] = len(records)

            records.append(
                UserRecord(
                    user_id=user_id,
                    name=name,
                    profile_url=profile_url,
                    source=source,
                    source_url=source_url,
                    username=(None if user_id.isdigit() else user_id),
                )
            )

        return tuple(records)
