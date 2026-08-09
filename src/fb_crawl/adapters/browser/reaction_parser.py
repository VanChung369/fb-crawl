from __future__ import annotations

import unicodedata
from dataclasses import replace

from bs4 import BeautifulSoup

from fb_crawl.adapters.browser.user_parser import UserParser, _identity
from fb_crawl.core.models import UserRecord


REACTION_NAMES = {
    "like": "like",
    "thich": "like",
    "love": "love",
    "yeu thich": "love",
    "care": "care",
    "thuong thuong": "care",
    "haha": "haha",
    "wow": "wow",
    "sad": "sad",
    "buon": "sad",
    "angry": "angry",
    "phan no": "angry",
}


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return " ".join(
        "".join(
            character
            for character in normalized
            if not unicodedata.combining(character)
        )
        .casefold()
        .replace("đ", "d")
        .split()
    )


def _reaction_type(anchor) -> str | None:
    current = anchor

    for _ in range(6):
        if current is None:
            break

        if str(current.get("role") or "").casefold() in {"dialog", "list"}:
            break

        candidates = [
            str(current.get(attribute) or "")
            for attribute in ("aria-label", "alt", "title", "data-reaction")
        ]
        for descendant in current.find_all(attrs={"aria-label": True}):
            candidates.append(str(descendant.get("aria-label") or ""))
        for image in current.find_all("img", alt=True):
            candidates.append(str(image.get("alt") or ""))

        for candidate in candidates:
            folded = _fold(candidate)
            for label, normalized in REACTION_NAMES.items():
                if folded == label or folded.startswith(f"{label} "):
                    return normalized

        current = current.parent

    return None


class ReactionParser:
    def __init__(self) -> None:
        self._users = UserParser(allow_plain_profile_links=True)

    def parse(
        self,
        html: str,
        *,
        source: str,
        source_url: str,
    ) -> tuple[UserRecord, ...]:
        base_records = self._users.parse(
            html,
            source=source,
            source_url=source_url,
        )
        soup = BeautifulSoup(html, "html.parser")
        reaction_by_id: dict[str, str] = {}

        for anchor in soup.find_all("a", href=True):
            identity = _identity(anchor, allow_plain_profile_links=True)
            if identity is None:
                continue
            reaction = _reaction_type(anchor)
            if reaction is not None:
                reaction_by_id.setdefault(identity[0], reaction)

        return tuple(
            replace(
                record,
                reacted=True,
                reaction_types=(
                    (reaction_by_id[record.user_id],)
                    if record.user_id in reaction_by_id
                    else ()
                ),
                interaction_count=1,
            )
            for record in base_records
        )
