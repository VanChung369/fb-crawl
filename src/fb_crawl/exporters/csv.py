from __future__ import annotations

import csv
from pathlib import Path

from fb_crawl.core.models import (
    PageRecord,
    ScrapeResult,
)
from fb_crawl.exporters.atomic import (
    atomic_text_writer,
)
from fb_crawl.exporters.schema import (
    UNIFIED_FIELDS,
    issue_row,
    page_record_row,
)

FIELDS = UNIFIED_FIELDS


def _record_row(
    record: PageRecord,
) -> dict[str, str]:
    return page_record_row(record)


def write_csv(
    result: ScrapeResult[PageRecord],
    path: Path,
) -> bool:
    if not result.records and not result.issues:
        return False

    rows = [_record_row(record) for record in result.records]

    rows.extend(issue_row(issue) for issue in result.issues)

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
