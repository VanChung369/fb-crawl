from __future__ import annotations

import hashlib

from bs4 import BeautifulSoup

from fb_crawl.core.models import MessageRecord
from fb_crawl.core.urls import normalize_facebook_url


MESSAGE_SELECTORS = (
    "[data-message-id]",
    "[data-testid='message-container']",
    "[role='row'][data-sender-name]",
    "[role='row']",
)


def _visible_text(node) -> str:
    return " ".join(node.stripped_strings).strip()


def _message_text(container) -> str:
    explicit = str(container.get("data-message-text") or "").strip()

    if explicit:
        return explicit

    text_node = container.select_one("[data-message-text]")
    if text_node is not None:
        value = str(text_node.get("data-message-text") or "").strip()
        return value or _visible_text(text_node)

    candidates = []
    for node in container.select("[dir='auto']"):
        if node.find_parent(("button", "time")) is not None:
            continue
        value = _visible_text(node)
        if value:
            candidates.append(value)

    if candidates:
        return "\n".join(dict.fromkeys(candidates))

    media = container.select_one(
        "[data-testid*='attachment'] img[alt],"
        "[data-testid*='attachment'] video[aria-label]"
    )
    if media is not None:
        label = str(media.get("alt") or media.get("aria-label") or "").strip()
        if label:
            return f"[Attachment: {label}]"

    return ""


def _sender(container) -> tuple[str | None, str | None]:
    name = str(container.get("data-sender-name") or "").strip() or None
    named = container.select_one("[data-sender-name]")

    if name is None and named is not None:
        name = (
            str(named.get("data-sender-name") or "").strip()
            or _visible_text(named)
            or None
        )

    if name is None:
        heading = container.find(("h3", "h4", "h5"))
        name = _visible_text(heading) if heading is not None else None

    profile_url: str | None = None
    for anchor in container.find_all("a", href=True):
        profile_url = normalize_facebook_url(
            str(anchor.get("href") or ""),
            base_url="https://www.facebook.com",
        )
        if profile_url is not None:
            if name is None:
                name = _visible_text(anchor) or None
            break

    return name, profile_url


def _sent_at(container) -> str | None:
    time_node = container.find("time")
    if time_node is not None:
        return (
            str(time_node.get("datetime") or "").strip()
            or _visible_text(time_node)
            or None
        )

    abbreviation = container.find("abbr")
    if abbreviation is not None:
        return (
            str(
                abbreviation.get("data-tooltip-content")
                or abbreviation.get("title")
                or ""
            ).strip()
            or _visible_text(abbreviation)
            or None
        )

    tooltip = container.find(attrs={"data-tooltip-content": True})
    if tooltip is not None:
        value = str(tooltip.get("data-tooltip-content") or "").strip()
        if value:
            return value

    return str(container.get("data-time") or "").strip() or None


class MessageParser:
    def parse(
        self,
        html: str,
        *,
        source_url: str,
    ) -> tuple[MessageRecord, ...]:
        soup = BeautifulSoup(html, "html.parser")
        containers = soup.select(",".join(MESSAGE_SELECTORS))
        containers.sort(
            key=lambda item: (
                0
                if item.get("data-message-id")
                else 1
                if item.get("data-testid") == "message-container"
                else 2
            )
        )
        records: list[MessageRecord] = []
        seen: set[str] = set()
        accepted_nodes: set[int] = set()

        for index, container in enumerate(containers):
            if any(id(parent) in accepted_nodes for parent in container.parents):
                continue

            if any(
                id(descendant) in accepted_nodes
                for descendant in container.find_all(True)
            ):
                continue

            text = _message_text(container)
            if not text:
                continue

            sender_name, sender_profile_url = _sender(container)
            sent_at = _sent_at(container)
            message_id = str(container.get("data-message-id") or "").strip()

            if not message_id:
                digest = hashlib.sha256(
                    f"{source_url}\0{sender_name}\0{sent_at}\0{text}\0{index}".encode(
                        "utf-8"
                    )
                ).hexdigest()
                message_id = f"visible-{digest[:20]}"

            if message_id in seen:
                continue

            seen.add(message_id)
            accepted_nodes.add(id(container))
            records.append(
                MessageRecord(
                    message_id=message_id,
                    sender_name=sender_name,
                    sender_profile_url=sender_profile_url,
                    text=text,
                    sent_at=sent_at,
                    thread_url=source_url,
                )
            )

        return tuple(records)
