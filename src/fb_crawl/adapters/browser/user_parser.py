from __future__ import annotations

import re
from urllib.parse import (
    parse_qs,
    urljoin,
    urlparse,
)

from bs4 import BeautifulSoup

from fb_crawl.core.models import UserRecord
from fb_crawl.core.urls import FACEBOOK_HOSTS

FACEBOOK_BASE = "https://www.facebook.com"
USER_ID = re.compile(r"[A-Za-z0-9._-]+")


def _name(anchor) -> str | None:
    visible = " ".join(anchor.stripped_strings).strip()

    if visible:
        return visible

    aria_label = str(anchor.get("aria-label") or "").strip()

    return aria_label or None


def _identity(
    anchor,
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
        seen: set[str] = set()

        for anchor in soup.find_all(
            "a",
            href=True,
        ):
            identity = _identity(anchor)

            if identity is None:
                continue

            user_id, profile_url = identity

            if user_id in seen:
                continue

            seen.add(user_id)

            records.append(
                UserRecord(
                    user_id=user_id,
                    name=_name(anchor),
                    profile_url=profile_url,
                    source=source,
                    source_url=source_url,
                )
            )

        return tuple(records)
