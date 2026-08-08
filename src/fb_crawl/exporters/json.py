from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from fb_crawl.core.models import (
    ScrapeResult,
)
from fb_crawl.exporters.atomic import (
    atomic_text_writer,
)
from fb_crawl.exporters.schema import issue_row, record_row


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}

    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]

    return value


def write_json(
    result: ScrapeResult,
    path: Path,
) -> bool:
    if not result.records and not result.issues:
        return False

    payload = {
        "records": [record_row(record) for record in result.records],
        "issues": [issue_row(issue) for issue in result.issues],
        "stats": _jsonable(result.stats),
        "enrichment": _jsonable(result.enrichment),
    }

    with atomic_text_writer(
        Path(path),
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")

    return True
