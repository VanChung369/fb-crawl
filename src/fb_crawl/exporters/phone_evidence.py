from __future__ import annotations

import csv
import re
from pathlib import Path

from fb_crawl.core.models import ScrapeResult, UserRecord
from fb_crawl.exporters.atomic import atomic_text_writer

PHONE_EVIDENCE_FIELDS = (
    "user_id",
    "profile_url",
    "phone_number",
    "source",
    "source_url",
    "captured_at",
    "confidence",
)


def _phone_key(value: str) -> str:
    return re.sub(r"\D", "", value)


def write_phone_evidence(
    result: ScrapeResult[UserRecord],
    path: Path,
) -> bool:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for record in result.records:
        for evidence in record.phone_evidence:
            key = (
                record.user_id,
                _phone_key(evidence.value),
                evidence.source,
                evidence.source_url,
            )
            if key in seen:
                continue

            seen.add(key)
            rows.append(
                {
                    "user_id": record.user_id,
                    "profile_url": record.profile_url,
                    "phone_number": evidence.value,
                    "source": evidence.source,
                    "source_url": evidence.source_url,
                    "captured_at": evidence.captured_at or "",
                    "confidence": evidence.confidence,
                }
            )

    if not rows:
        return False

    with atomic_text_writer(
        Path(path),
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=PHONE_EVIDENCE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    return True
