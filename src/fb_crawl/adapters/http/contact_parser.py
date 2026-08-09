from __future__ import annotations

import html as html_module
import json
import re
from collections.abc import Callable
from dataclasses import replace
from urllib.parse import parse_qs, urlparse


from selectolax.parser import HTMLParser


from fb_crawl.adapters.http.client import HttpClient
from fb_crawl.core.exceptions import FetchError
from fb_crawl.core.models import (
    ContactKind,
    ContactRecord,
    PageRecord,
    PublicAction,
    ScrapeIssue,
    ScrapeMode,
)

PHONE_CONTEXT_WORDS = (
    "phone",
    "mobile",
    "tel",
    "telephone",
    "hotline",
    "call",
    "contact",
    "whatsapp",
    "zalo",
    "viber",
    "sdt",
    "sđt",
    "so dien thoai",
    "số điện thoại",
    "dien thoai",
    "điện thoại",
    "lien he",
    "số điện thoại",
    "điện thoại",
    "liên hệ",
    "liên hệ",
)


PHONE_PATTERN = re.compile(
    r"(?<![\w])"
    r"(?:\+?\d{1,4}[\s().\-/]*)?"
    r"(?:\(?\d{2,5}\)?[\s().\-/]*)?"
    r"\d{3,4}[\s().\-/]*\d{3,4}"
    r"(?:[\s().\-/]*\d{2,4})?"
    r"(?![\w])"
)

VIETNAM_MOBILE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?84|0)[\s().-]?[35789](?:[\s().-]?\d){8}(?!\d)"
)


UID_PATTERNS = (
    re.compile(r'"pageID"\s*:\s*"(\d{5,})"'),
    re.compile(r'"profile_id"\s*:\s*"(\d{5,})"'),
    re.compile(r'"userID"\s*:\s*"(\d{5,})"'),
    re.compile(r"(?:owner_id|ownerID)=(\d{5,})"),
    re.compile(r"profile\.php\?id=(\d{5,})"),
)


RAW_PHONE_PATTERNS = (
    re.compile(r'"formatted_phone_number"\s*:\s*"([^"]+)"'),
    re.compile(r'"phone_number"\s*:\s*"([^"]+)"'),
    re.compile(r'"phoneNumber"\s*:\s*"([^"]+)"'),
    re.compile(r'"mobile_phone"\s*:\s*"([^"]+)"'),
)


def _decode(value: str) -> str:
    decoded = html_module.unescape(value)

    try:
        return html_module.unescape(json.loads(f'"{decoded}"'))
    except json.JSONDecodeError:
        return decoded.replace("\\/", "/")


def _phone_key(
    value: str,
) -> str | None:
    digits = re.sub(r"\D", "", value)

    if not 8 <= len(digits) <= 15:
        return None

    if len(set(digits)) == 1:
        return None

    return digits


def extract_phone_numbers(
    text: str | None,
    *,
    require_context: bool = False,
) -> list[str]:
    if not text:
        return []

    found: dict[str, str] = {}

    for chunk in re.split(
        r"[\n\r|•;]+",
        text,
    ):
        if require_context and not any(
            word in chunk.lower() for word in PHONE_CONTEXT_WORDS
        ):
            continue

        for match in PHONE_PATTERN.finditer(chunk):
            value = re.sub(
                r"\s+",
                " ",
                match.group(0),
            ).strip(" .,-/()")

            key = _phone_key(value)

            if key and key not in found:
                found[key] = value

    return list(found.values())


def extract_visible_phone_numbers(text: str | None) -> list[str]:
    """Extract phone-shaped values from user-visible free text.

    Free text is noisier than a dedicated contact field. Plain values are
    therefore accepted only when they look like a Vietnamese mobile number,
    carry an international prefix, contain a conventional phone separator,
    or appear next to an explicit contact word.
    """
    if not text:
        return []

    found: dict[str, str] = {}

    for chunk in re.split(r"[\n\r|â€¢;]+", text):
        folded = chunk.casefold()
        has_context = any(word in folded for word in PHONE_CONTEXT_WORDS)

        for match in VIETNAM_MOBILE_PATTERN.finditer(chunk):
            value = re.sub(r"\s+", " ", match.group(0)).strip()
            key = _phone_key(value)

            if key:
                found.setdefault(key, value)

        for value in extract_phone_numbers(chunk):
            key = _phone_key(value)

            if key is None:
                continue

            compact = re.sub(r"\s", "", value)

            if re.fullmatch(
                r"\d{1,4}[./-]\d{1,2}[./-]\d{2,4}", compact
            ):
                continue

            vietnam_mobile = bool(
                re.fullmatch(r"(?:0|84)[35789]\d{8}", key)
            )
            malformed_vietnam_mobile = bool(
                re.match(r"(?:0|84)[35789]", key)
            ) and not vietnam_mobile
            international = value.lstrip().startswith("+") and len(key) >= 9
            separated = len(key) >= 9 and bool(
                re.search(r"[\s().-]", value)
            )

            if malformed_vietnam_mobile:
                continue

            if has_context or vietnam_mobile or international or separated:
                found.setdefault(key, value)

    return list(found.values())


def extract_raw_phone_numbers(
    text: str | None,
) -> list[str]:
    if not text:
        return []

    values: list[str] = []
    decoded = _decode(text)

    for pattern in RAW_PHONE_PATTERNS:
        for match in pattern.finditer(decoded):
            raw_value = _decode(match.group(1))

            values.extend(extract_phone_numbers(raw_value))

    unique: dict[str, str] = {}

    for value in values:
        key = _phone_key(value)

        if key:
            unique.setdefault(key, value)

    return list(unique.values())


def extract_uid(
    text: str | None,
) -> str | None:
    if not text:
        return None

    for pattern in UID_PATTERNS:
        match = pattern.search(text)

        if match:
            return match.group(1)

    return None


def visible_text(html: str) -> str:
    parser = HTMLParser(html)

    for selector in (
        "script",
        "style",
        "noscript",
    ):
        for node in parser.css(selector):
            node.decompose()

    return parser.text(
        separator="\n",
        strip=True,
    )


def tel_link_text(html: str) -> str:
    parser = HTMLParser(html)
    values: list[str] = []

    for node in parser.css("a[href]"):
        href = node.attributes.get("href", "")

        if not href:
            continue

        lowered = href.lower()

        if lowered.startswith("tel:") or "wa.me/" in lowered or "whatsapp" in lowered:
            values.append(
                href.removeprefix("tel:") if lowered.startswith("tel:") else href
            )

    return "\n".join(values)


def _normalize_website(
    value: str | None,
) -> str | None:
    if not value or not value.strip():
        return None

    candidate = value.strip()

    if not candidate.startswith(("http://", "https://")):
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    return candidate


def _facebook_identifier(
    url: str,
) -> str | None:
    parsed = urlparse(url)

    query_id = parse_qs(parsed.query).get("id", [""])[0]

    if query_id:
        return query_id

    parts = [part for part in parsed.path.split("/") if part]

    if not parts or parts[0].lower() == "profile.php":
        return None

    return parts[0]


def _merge_phone(
    contacts: list[ContactRecord],
    value: str,
    source: str,
) -> None:
    key = _phone_key(value)

    if key is None:
        return

    for index, existing in enumerate(contacts):
        if existing.kind is not ContactKind.PHONE:
            continue

        if _phone_key(existing.value) == key:
            contacts[index] = replace(
                existing,
                sources=tuple(
                    dict.fromkeys(
                        (
                            *existing.sources,
                            source,
                        )
                    )
                ),
            )
            return

    contacts.append(
        ContactRecord(
            kind=ContactKind.PHONE,
            value=value,
            sources=(source,),
        )
    )


def _enrichment_targets(
    record: PageRecord,
) -> list[tuple[str, str, Callable[[str], str]]]:
    targets: list[tuple[str, str, Callable[[str], str]]] = []

    identifier = _facebook_identifier(record.canonical_url)

    if identifier:
        targets.append(
            (
                ("https://mbasic.facebook.com/" f"{identifier}/about"),
                "facebook:mbasic_about_text",
                visible_text,
            )
        )

    website = _normalize_website(record.website)

    if website:
        targets.append(
            (
                website,
                "website:tel_or_whatsapp",
                tel_link_text,
            )
        )

    return targets


class ContactEnricher:
    def __init__(
        self,
        client: HttpClient,
    ) -> None:
        self._client = client

    def enrich(
        self,
        record: PageRecord,
        facebook_html: str,
    ) -> tuple[
        PageRecord,
        tuple[ScrapeIssue, ...],
    ]:
        uid = record.uid or extract_uid(facebook_html)

        contacts = list(record.contacts)
        issues: list[ScrapeIssue] = []

        def add_phones(
            text: str,
            source: str,
            require_context: bool = False,
        ) -> None:
            for phone in extract_phone_numbers(
                text,
                require_context=require_context,
            ):
                _merge_phone(
                    contacts,
                    phone,
                    source,
                )

        raw_phone_text = "\n".join(extract_raw_phone_numbers(facebook_html))

        add_phones(
            raw_phone_text,
            "facebook:raw_phone_field",
        )

        add_phones(
            visible_text(facebook_html),
            "facebook:public_text",
            require_context=True,
        )

        for (
            url,
            source,
            extractor,
        ) in _enrichment_targets(record):
            try:
                body = self._client.get_text(url)

            except FetchError as error:
                issues.append(
                    ScrapeIssue(
                        code=error.code,
                        message=error.safe_message,
                        target=error.target,
                        mode=ScrapeMode.PUBLIC,
                        action=PublicAction.PAGE.value,
                        retryable=True,
                    )
                )
                continue

            if source == "facebook:mbasic_about_text" and uid is None:
                uid = extract_uid(body)

            add_phones(
                extractor(body),
                source,
                require_context=("text" in source),
            )

        return (
            replace(
                record,
                uid=uid,
                contacts=tuple(contacts),
            ),
            tuple(issues),
        )
