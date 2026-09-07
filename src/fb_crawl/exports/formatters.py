from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from fb_crawl.core.atomic import atomic_output_path, atomic_text_writer
from fb_crawl.core.exceptions import ConfigurationError, ExportError
from fb_crawl.history.models import HistoryItem


EXPORT_COLUMNS = (
    "event_id",
    "facebook_uid",
    "username",
    "name",
    "profile_url",
    "phone",
    "outcome",
    "source",
    "scan_mode",
    "source_type",
    "source_url",
    "product_crawl_job_id",
    "provider_called",
    "quota_charged",
    "created_at",
    "completed_at",
)


def safe_cell(value: str) -> str:
    return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value


def _instant(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat().replace("+00:00", "Z")


def history_row(item: HistoryItem) -> dict[str, object]:
    raw: dict[str, object] = {
        "event_id": item.id,
        "facebook_uid": item.facebook_uid,
        "username": item.username,
        "name": item.name,
        "profile_url": item.profile_url,
        "phone": item.phone,
        "outcome": item.outcome.value,
        "source": item.source.value,
        "scan_mode": item.scan_mode.value,
        "source_type": item.source_type.value,
        "source_url": item.source_url,
        "product_crawl_job_id": (
            str(item.product_crawl_job_id) if item.product_crawl_job_id else ""
        ),
        "provider_called": item.provider_called,
        "quota_charged": item.quota_charged,
        "created_at": _instant(item.created_at),
        "completed_at": _instant(item.completed_at),
    }
    return {
        key: safe_cell(value) if isinstance(value, str) else value
        for key, value in raw.items()
    }


def write_history_csv(items: Iterable[HistoryItem], path: Path) -> None:
    with atomic_text_writer(path, encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        for item in items:
            writer.writerow(history_row(item))


def write_history_xlsx(items: Iterable[HistoryItem], path: Path) -> None:
    try:
        from openpyxl import Workbook
    except ModuleNotFoundError as error:
        raise ConfigurationError(
            "XLSX export requires the API export dependencies."
        ) from error

    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("history")
    sheet.append(EXPORT_COLUMNS)
    for item in items:
        row = history_row(item)
        sheet.append(tuple(row[column] for column in EXPORT_COLUMNS))
    try:
        with atomic_output_path(path, temporary_suffix=".tmp.xlsx") as temporary:
            workbook.save(temporary)
    except (ConfigurationError, ExportError):
        raise
    except Exception as error:
        raise ExportError(
            f"Cannot write output file {path}.", target=str(path)
        ) from error
