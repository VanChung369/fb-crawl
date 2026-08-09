from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from fb_crawl.core.exceptions import ConfigurationError
from fb_crawl.exporters.atomic import atomic_text_writer
from fb_crawl.services.data_plan import DataPlanResult


def read_plan_rows(path: Path) -> tuple[dict[str, str], ...]:
    try:
        file = path.open(encoding="utf-8-sig", newline="")
    except OSError as error:
        raise ConfigurationError(
            f"Cannot read data plan input {path}.", target=str(path)
        ) from error

    with file:
        reader = csv.DictReader(file)
        fields = frozenset(reader.fieldnames or ())

        if "profile_url" not in fields:
            raise ConfigurationError(
                "Data plan input is missing profile_url.", target=str(path)
            )

        return tuple(
            {
                str(key): str(value or "")
                for key, value in row.items()
                if key is not None
            }
            for row in reader
        )


def write_plan_targets(result: DataPlanResult, path: Path) -> None:
    with atomic_text_writer(path, encoding="utf-8") as file:
        for target in result.targets:
            file.write(f"{target}\n")


def write_plan_report(
    result: DataPlanResult,
    path: Path,
    *,
    input_path: Path,
) -> None:
    payload = asdict(result.report)
    payload["input"] = str(input_path)
    payload["targets"] = [
        asdict(candidate) for candidate in result.candidates
    ]

    with atomic_text_writer(path, encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
