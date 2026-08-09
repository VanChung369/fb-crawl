from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Sequence

from fb_crawl.core.exceptions import ConfigurationError
from fb_crawl.exporters.atomic import atomic_text_writer
from fb_crawl.exporters.phone_evidence import PHONE_EVIDENCE_FIELDS
from fb_crawl.services.phone_evidence_merge import (
    PhoneEvidenceMergeResult,
    SourcePhoneEvidence,
)


PHONE_EVIDENCE_MASTER_FIELDS = (
    "user_id",
    "profile_url",
    "phone_number",
    "normalized_phone",
    "sources",
    "source_urls",
    "first_captured_at",
    "last_captured_at",
    "confidence",
    "evidence_count",
    "quality_status",
)


@dataclass(frozen=True, slots=True)
class LoadedPhoneEvidence:
    rows: tuple[SourcePhoneEvidence, ...]
    input_files: int
    skipped_files: tuple[str, ...]


def read_phone_evidence(paths: Sequence[Path]) -> LoadedPhoneEvidence:
    rows: list[SourcePhoneEvidence] = []
    input_files = 0
    skipped: list[str] = []
    required = frozenset(PHONE_EVIDENCE_FIELDS)

    for path in paths:
        try:
            file = path.open(encoding="utf-8-sig", newline="")
        except OSError as error:
            raise ConfigurationError(
                f"Cannot read phone evidence input {path}.",
                target=str(path),
            ) from error

        with file:
            reader = csv.DictReader(file)
            fields = frozenset(reader.fieldnames or ())

            if not required.issubset(fields):
                skipped.append(str(path))
                continue

            input_files += 1

            for line_number, raw in enumerate(reader, start=2):
                rows.append(
                    SourcePhoneEvidence(
                        source_file=str(path),
                        line_number=line_number,
                        user_id=str(raw.get("user_id") or ""),
                        profile_url=str(raw.get("profile_url") or ""),
                        phone_number=str(raw.get("phone_number") or ""),
                        source=str(raw.get("source") or ""),
                        source_url=str(raw.get("source_url") or ""),
                        captured_at=str(raw.get("captured_at") or ""),
                        confidence=str(raw.get("confidence") or ""),
                    )
                )

    return LoadedPhoneEvidence(
        rows=tuple(rows),
        input_files=input_files,
        skipped_files=tuple(skipped),
    )


def write_phone_evidence_master(
    result: PhoneEvidenceMergeResult,
    path: Path,
) -> None:
    with atomic_text_writer(
        path,
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=PHONE_EVIDENCE_MASTER_FIELDS,
        )
        writer.writeheader()
        writer.writerows(
            {
                "user_id": row.user_id,
                "profile_url": row.profile_url,
                "phone_number": row.phone_number,
                "normalized_phone": row.normalized_phone,
                "sources": "; ".join(row.sources),
                "source_urls": "; ".join(row.source_urls),
                "first_captured_at": row.first_captured_at,
                "last_captured_at": row.last_captured_at,
                "confidence": row.confidence,
                "evidence_count": str(row.evidence_count),
                "quality_status": "; ".join(row.quality_status),
            }
            for row in result.rows
        )


def write_phone_evidence_report(
    result: PhoneEvidenceMergeResult,
    path: Path,
) -> None:
    payload = asdict(result.report)
    payload["issue_details"] = [asdict(issue) for issue in result.issues]

    with atomic_text_writer(path, encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
