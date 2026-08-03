from __future__ import annotations

import html as html_module
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import (
    parse_qs,
    quote,
    unquote,
    urlparse,
)

from selectolax.parser import HTMLParser

from fb_crawl.adapters.http.client import HttpClient
from fb_crawl.core.models import TargetKind
from fb_crawl.core.urls import (
    canonicalize_targets,
    normalize_facebook_url,
)

FACEBOOK_URL_PATTERN = re.compile(
    (r"https?://(?:www\.|m\.|mbasic\.|web\.)?" r"facebook\.com/[^\s\"'<>\\]+"),
    re.IGNORECASE,
)


def _decode(value: str) -> str:
    decoded = html_module.unescape(value)

    try:
        return html_module.unescape(json.loads(f'"{decoded}"'))
    except json.JSONDecodeError:
        return decoded.replace("\\/", "/")


def duckduckgo_query_url(query: str) -> str:
    return "https://html.duckduckgo.com/html/" f"?q={quote(query)}"


def bing_rss_query_url(query: str) -> str:
    return "https://www.bing.com/search" f"?format=rss&q={quote(query)}"


def facebook_public_search_url(
    keyword: str,
) -> str:
    slug = quote(
        re.sub(
            r"\s+",
            "-",
            keyword.strip(),
        ).strip("-")
    )

    return f"https://www.facebook.com/public/{slug}"


def keyword_queries(
    keyword: str,
    target: TargetKind,
) -> list[str]:
    cleaned = keyword.strip()

    if target is TargetKind.PEOPLE:
        return [
            ("site:facebook.com/profile.php?id= " f"{cleaned}"),
            f"site:facebook.com/people {cleaned}",
        ]

    if target is TargetKind.PAGES:
        return [
            (
                f"site:facebook.com {cleaned} "
                "-site:facebook.com/profile.php "
                "-site:facebook.com/people "
                "-site:facebook.com/groups"
            )
        ]

    return [
        f"site:facebook.com {cleaned}",
    ]


def extract_facebook_urls(
    text: str | None,
    *,
    base_url: str | None,
    target: TargetKind,
    limit: int,
) -> list[str]:
    if not text or limit <= 0:
        return []

    decoded = _decode(text)

    candidates = [
        node.attributes["href"]
        for node in HTMLParser(decoded).css("a[href]")
        if node.attributes.get("href")
    ]

    candidates.extend(
        match.group(0) for match in FACEBOOK_URL_PATTERN.finditer(decoded)
    )

    normalized = [
        value
        for candidate in candidates
        if (
            value := normalize_facebook_url(
                candidate.rstrip(".,);]}'\""),
                base_url=base_url,
            )
        )
    ]

    return canonicalize_targets(
        normalized,
        target=target,
        limit=limit,
    )


def extract_duckduckgo_urls(
    text: str,
    target: TargetKind,
    limit: int,
) -> list[str]:
    candidates: list[str] = []

    for node in HTMLParser(text).css("a[href]"):
        href = node.attributes.get("href", "")

        parsed = urlparse(f"https:{href}" if href.startswith("//") else href)

        redirect = parse_qs(parsed.query).get("uddg", [""])[0]

        candidate = unquote(redirect) if redirect else href

        if normalize_facebook_url(candidate):
            candidates.append(candidate)

    return canonicalize_targets(
        candidates,
        target=target,
        limit=limit,
    )


def extract_bing_urls(
    text: str,
    target: TargetKind,
    limit: int,
) -> list[str]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    candidates = [item.findtext("link") or "" for item in root.findall(".//item")]

    return canonicalize_targets(
        candidates,
        target=target,
        limit=limit,
    )


class PublicDiscovery:
    def __init__(
        self,
        client: HttpClient,
    ) -> None:
        self._client = client

    def query_urls(
        self,
        keyword: str,
        target: TargetKind,
    ) -> list[tuple[str, str]]:
        return [
            (
                duckduckgo_query_url(query),
                bing_rss_query_url(query),
            )
            for query in keyword_queries(
                keyword,
                target,
            )
        ]

    def from_html(
        self,
        html: str,
        *,
        base_url: str,
        target: TargetKind,
        limit: int,
    ) -> list[str]:
        return extract_facebook_urls(
            html,
            base_url=base_url,
            target=target,
            limit=limit,
        )

    def search(
        self,
        keyword: str,
        target: TargetKind,
        limit: int,
    ) -> list[str]:
        found: list[str] = []

        for ddg_url, bing_url in self.query_urls(
            keyword,
            target,
        ):
            remaining = limit - len(found)

            if remaining <= 0:
                break

            ddg_html = self._client.get_text(ddg_url)

            batch = extract_duckduckgo_urls(
                ddg_html,
                target,
                remaining,
            )

            if not batch:
                bing_xml = self._client.get_text(bing_url)

                batch = extract_bing_urls(
                    bing_xml,
                    target,
                    remaining,
                )

            found = canonicalize_targets(
                [
                    *found,
                    *batch,
                ],
                target=target,
                limit=limit,
            )

        if (
            target
            in {
                TargetKind.PEOPLE,
                TargetKind.ALL,
            }
            and len(found) < limit
        ):
            directory_url = facebook_public_search_url(keyword)

            directory_html = self._client.get_text(directory_url)

            directory_results = self.from_html(
                directory_html,
                base_url=directory_url,
                target=target,
                limit=limit - len(found),
            )

            found = canonicalize_targets(
                [
                    *found,
                    *directory_results,
                ],
                target=target,
                limit=limit,
            )

        return found
