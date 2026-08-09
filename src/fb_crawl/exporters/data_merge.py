from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Sequence

from fb_crawl.core.exceptions import ConfigurationError
from fb_crawl.exporters.atomic import atomic_text_writer
from fb_crawl.exporters.schema import UNIFIED_FIELDS
from fb_crawl.services.data_merge import DataMergeResult, SourceRow


@dataclass(frozen=True, slots=True)
class LoadedMergeRows:
    rows: tuple[SourceRow, ...]
    input_files: int
    skipped_files: tuple[str, ...]


def read_merge_rows(paths: Sequence[Path]) -> LoadedMergeRows:
    rows: list[SourceRow] = []
    input_files = 0
    skipped: list[str] = []

    for path in paths:
        try:
            file = path.open(encoding="utf-8-sig", newline="")
        except OSError as error:
            raise ConfigurationError(
                f"Cannot read merge input {path}.", target=str(path)
            ) from error

        with file:
            reader = csv.DictReader(file)
            fields = frozenset(reader.fieldnames or ())

            if "profile_url" not in fields or not fields.intersection(
                {"user_id", "username", "name", "page_name"}
            ):
                skipped.append(str(path))
                continue

            input_files += 1

            for line_number, raw in enumerate(reader, start=2):
                values = {
                    field: str(raw.get(field) or "")
                    for field in UNIFIED_FIELDS
                }
                rows.append(SourceRow(str(path), line_number, values))

    return LoadedMergeRows(
        rows=tuple(rows),
        input_files=input_files,
        skipped_files=tuple(skipped),
    )


def write_merged_csv(result: DataMergeResult, path: Path) -> None:
    with atomic_text_writer(
        path,
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=UNIFIED_FIELDS)
        writer.writeheader()
        writer.writerows(result.rows)


def write_quality_report(result: DataMergeResult, path: Path) -> None:
    payload = asdict(result.report)
    payload["conflict_details"] = [
        asdict(conflict) for conflict in result.conflicts
    ]

    with atomic_text_writer(path, encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
