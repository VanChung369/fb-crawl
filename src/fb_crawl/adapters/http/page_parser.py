from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from typing import Any

from selectolax.parser import HTMLParser

from fb_crawl.core.exceptions import ParseError
from fb_crawl.core.models import (
    ContactKind,
    ContactRecord,
    PageRecord,
)

PROFILE_TYPES = {
    "INTRO_CARD_INFLUENCER_CATEGORY": "category",
    "INTRO_CARD_PROFILE_PHONE": ContactKind.PHONE,
    "INTRO_CARD_PROFILE_EMAIL": ContactKind.EMAIL,
    "INTRO_CARD_WEBSITE": ContactKind.WEBSITE,
}


def _walk_mappings(
    value: Any,
) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value

        for child in value.values():
            yield from _walk_mappings(child)

    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _documents(
    parser: HTMLParser,
) -> list[Mapping[str, Any]]:
    documents: list[Mapping[str, Any]] = []

    for script in parser.css('script[type="application/json"]'):
        try:
            value = json.loads(script.text(strip=True))
        except json.JSONDecodeError:
            continue

        if isinstance(value, Mapping):
            documents.append(value)

    return documents


def _title_text(
    context: Mapping[str, Any],
) -> str | None:
    renderer = context.get("renderer") or {}
    item = renderer.get("context_item") or {}
    title = item.get("title") or {}
    value = title.get("text")

    if not value:
        return None

    return str(value).strip()


class PublicPageParser:
    def parse(
        self,
        html: str,
        canonical_url: str,
    ) -> PageRecord:
        parser = HTMLParser(html)

        mappings = [
            mapping
            for document in _documents(parser)
            for mapping in _walk_mappings(document)
        ]

        user: Mapping[str, Any] | None = None

        for mapping in mappings:
            renderer = mapping.get("profile_header_renderer")

            if isinstance(renderer, Mapping) and isinstance(
                renderer.get("user"),
                Mapping,
            ):
                user = renderer["user"]
                break

        category: str | None = None
        contacts: list[ContactRecord] = []
        website: str | None = None

        for mapping in mappings:
            item_type = mapping.get("timeline_context_list_item_type")
            mapped = PROFILE_TYPES.get(str(item_type))
            value = _title_text(mapping)

            if not mapped or not value:
                continue

            if mapped == "category":
                category = value
                continue

            contacts.append(
                ContactRecord(
                    kind=mapped,
                    value=value,
                    sources=("facebook:profile_card",),
                )
            )

            if mapped is ContactKind.WEBSITE:
                website = value

        metadata: dict[
            str,
            str | bool | None,
        ] = {}

        description_node = parser.css_first('meta[name="description"]')

        description = (
            description_node.attributes.get(
                "content",
                "",
            )
            if description_node is not None
            else ""
        )

        patterns = {
            "likes_count": (r"([\d,]+)\s+likes"),
            "talking_count": (r"([\d,]+)\s+talking about this"),
            "were_here_count": (r"([\d,]+)\s+were here"),
        }

        for key, pattern in patterns.items():
            match = re.search(
                pattern,
                description,
                re.IGNORECASE,
            )

            metadata[key] = match.group(1) if match else None

        delegate = user.get("delegate_page") if user else None

        uid = delegate.get("id") if isinstance(delegate, Mapping) else None

        if isinstance(delegate, Mapping):
            metadata["is_business_page"] = delegate.get("is_business_page_active")

        if user:
            profile_pic = (
                user.get("profilePicLarge") or user.get("profilePicMedium") or {}
            )

            cover = user.get("cover_photo") or {}
            photo = cover.get("photo") or {}
            image = photo.get("image") or {}

            metadata["profile_pic"] = profile_pic.get("uri")
            metadata["cover_photo"] = image.get("uri")

        has_no_metadata = all(value is None for value in metadata.values())

        if user is None and not contacts and has_no_metadata:
            raise ParseError(
                "No public page data found.",
                target=canonical_url,
            )

        return PageRecord(
            canonical_url=canonical_url,
            page_name=(str(user.get("name")) if user and user.get("name") else None),
            uid=str(uid) if uid else None,
            category=category,
            website=website,
            contacts=tuple(contacts),
            metadata=metadata,
        )
