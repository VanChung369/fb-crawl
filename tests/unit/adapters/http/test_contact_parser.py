from fb_crawl.adapters.http.contact_parser import (
    ContactEnricher,
    extract_phone_numbers,
    extract_raw_phone_numbers,
    extract_uid,
)

from fb_crawl.core.models import (
    ContactKind,
    PageRecord,
)


class MappingClient:
    def __init__(
        self,
        pages: dict[str, str],
    ) -> None:
        self.pages = pages

    def get_text(
        self,
        url: str,
        *,
        headers=None,
    ) -> str:
        return self.pages[url]


def test_phone_and_uid_helpers_preserve_regression_behavior() -> None:
    assert extract_phone_numbers(
        ("Hotline: 0123 456 789; " "WhatsApp: +84 987-654-321"),
        require_context=True,
    ) == [
        "0123 456 789",
        "+84 987-654-321",
    ]

    assert extract_raw_phone_numbers(
        r'{"formatted_phone_number":"\u002b84 912 345 678"}'
    ) == [
        "+84 912 345 678",
    ]

    assert extract_uid('{"pageID":"1156899667774877"}') == "1156899667774877"


def test_enricher_merges_sources_without_duplicate_phone_values() -> None:
    record = PageRecord(
        canonical_url=("https://www.facebook.com/examplespa"),
        website="example.com",
    )

    client = MappingClient(
        {
            (
                "https://mbasic.facebook.com/" "examplespa/about"
            ): "Hotline: +84 912 345 678",
            "https://example.com": ('<a href="tel:+84912345678">' "Call" "</a>"),
        }
    )

    enriched, issues = ContactEnricher(client).enrich(
        record,
        '{"pageID":"1156899667774877"}',
    )

    phones = [item for item in enriched.contacts if item.kind is ContactKind.PHONE]

    assert enriched.uid == "1156899667774877"
    assert len(phones) == 1

    assert set(phones[0].sources) == {
        "facebook:mbasic_about_text",
        "website:tel_or_whatsapp",
    }

    assert issues == ()
