from pathlib import Path

from fb_crawl.adapters.http.discovery import (
    PublicDiscovery,
    extract_facebook_urls,
)
from fb_crawl.core.models import TargetKind

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "discovery"


class MappingClient:
    def __init__(
        self,
        pages: dict[str, str],
    ) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get_text(
        self,
        url: str,
        *,
        headers=None,
    ) -> str:
        self.calls.append(url)
        return self.pages[url]


def test_extracts_and_filters_canonical_urls_from_source_html() -> None:
    html = (FIXTURES / "source.html").read_text(encoding="utf-8")

    result = extract_facebook_urls(
        html,
        base_url=("https://www.facebook.com/" "search/pages?q=test"),
        target=TargetKind.ALL,
        limit=10,
    )

    assert result == [
        "https://www.facebook.com/alpha.page",
        ("https://www.facebook.com/" "profile.php?id=100013976614656"),
    ]


def test_search_falls_back_to_bing_when_duckduckgo_is_empty() -> None:
    client = MappingClient({})
    discovery = PublicDiscovery(client)

    ddg_url, bing_url = discovery.query_urls(
        "spa",
        TargetKind.PAGES,
    )[0]

    client.pages[ddg_url] = "<html></html>"
    client.pages[bing_url] = (FIXTURES / "bing.xml").read_text(encoding="utf-8")

    result = discovery.search(
        "spa",
        TargetKind.PAGES,
        5,
    )

    assert result == [
        "https://www.facebook.com/fallbackspa",
    ]

    assert client.calls == [
        ddg_url,
        bing_url,
    ]
