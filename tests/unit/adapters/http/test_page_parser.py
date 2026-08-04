from pathlib import Path

import pytest

from fb_crawl.adapters.http.page_parser import (
    PublicPageParser,
)

from fb_crawl.core.exceptions import ParseError
from fb_crawl.core.models import ContactKind

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "public_page.html"


def test_parser_builds_typed_page_record() -> None:
    record = PublicPageParser().parse(
        FIXTURE.read_text(encoding="utf-8"),
        "https://www.facebook.com/examplespa",
    )

    assert record.page_name == "Example Spa"
    assert record.uid == "1156899667774877"
    assert record.category == "Spa"
    assert record.website == "example.com"
    assert record.metadata["likes_count"] == "1,234"

    assert [(contact.kind, contact.value) for contact in record.contacts] == [
        (
            ContactKind.PHONE,
            "+84 912 345 678",
        ),
        (
            ContactKind.WEBSITE,
            "example.com",
        ),
    ]


def test_parser_raises_safe_error_when_no_page_data_exists() -> None:
    with pytest.raises(
        ParseError,
        match="No public page data found",
    ):
        PublicPageParser().parse(
            "<html></html>",
            "https://www.facebook.com/empty",
        )
