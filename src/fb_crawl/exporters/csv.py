from __future__ import annotations

import csv
from pathlib import Path

from fb_crawl.core.models import (
    ContactKind,
    PageRecord,
    ScrapeResult,
)
from fb_crawl.exporters.atomic import (
    atomic_text_writer,
)

FIELDS = (
    "url",
    "page_name",
    "uid",
    "category",
    "website",
    "phone_numbers",
    "phone_sources",
    "depth",
    "source",
    "error_code",
    "error_message",
)


def _record_row(
    record: PageRecord,
) -> dict[str, object]:
    phones = [
        contact for contact in record.contacts if contact.kind is ContactKind.PHONE
    ]

    return {
        "url": record.canonical_url,
        "page_name": record.page_name,
        "uid": record.uid,
        "category": record.category,
        "website": record.website,
        "phone_numbers": "; ".join(contact.value for contact in phones),
        "phone_sources": "; ".join(
            source for contact in phones for source in contact.sources
        ),
        "depth": record.depth,
        "source": record.discovery_source,
        "error_code": "",
        "error_message": "",
    }


def write_csv(
    result: ScrapeResult[PageRecord],
    path: Path,
) -> bool:
    if not result.records and not result.issues:
        return False

    rows = [_record_row(record) for record in result.records]

    rows.extend(
        {
            "url": issue.target or "",
            "page_name": "",
            "uid": "",
            "category": "",
            "website": "",
            "phone_numbers": "",
            "phone_sources": "",
            "depth": "",
            "source": "",
            "error_code": issue.code,
            "error_message": issue.message,
        }
        for issue in result.issues
    )

    with atomic_text_writer(
        Path(path),
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=FIELDS,
        )
        writer.writeheader()
        writer.writerows(rows)

    return True
