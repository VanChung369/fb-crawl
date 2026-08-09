from __future__ import annotations

import re
import unicodedata


SOCIAL_CONTEXT_LABEL = re.compile(
    r"^(?:\d[\d\s.,]*[kmbt]?)\s+(?:"
    r"friends?|followers?|following|mutual friends?|"
    r"ban be|nguoi ban|nguoi theo doi|ban chung"
    r")$",
    re.IGNORECASE,
)

GENERIC_PROFILE_LABELS = frozenset(
    {
        "facebook user",
        "friends",
        "followers",
        "profile",
        "ban be",
        "nguoi theo doi",
    }
)


def ascii_fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )


def is_social_context_label(value: str | None) -> bool:
    if value is None:
        return False

    folded = " ".join(ascii_fold(value).split())
    return bool(SOCIAL_CONTEXT_LABEL.fullmatch(folded))


def is_suspicious_profile_name(value: str | None) -> bool:
    if value is None or not value.strip():
        return True

    folded = " ".join(ascii_fold(value).split())
    return (
        folded in GENERIC_PROFILE_LABELS
        or bool(SOCIAL_CONTEXT_LABEL.fullmatch(folded))
    )
