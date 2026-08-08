from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

from fb_crawl.core.exceptions import (
    ConfigurationError,
    ExportError,
)
from fb_crawl.core.models import (
    ScrapeResult,
    UserRecord,
)
from fb_crawl.exporters.atomic import (
    atomic_output_path,
    atomic_text_writer,
)
from fb_crawl.exporters.json import write_json

USER_FIELDS = (
    "user_id",
    "name",
    "profile_url",
    "source",
    "source_url",
    "error_code",
    "error_message",
)

USER_FORMATS = frozenset(
    {
        "csv",
        "json",
        "txt",
        "xlsx",
    }
)


def _deduplicated_result(
    result: ScrapeResult[UserRecord],
) -> ScrapeResult[UserRecord]:
    records: dict[str, UserRecord] = {}

    for record in result.records:
        existing = records.get(record.user_id)

        if existing is None:
            records[record.user_id] = record
        else:
            records[record.user_id] = replace(
                existing,
                name=existing.name or record.name,
                profile_url=(existing.profile_url or record.profile_url),
            )

    return replace(
        result,
        records=tuple(records.values()),
    )


def _rows(
    result: ScrapeResult[UserRecord],
) -> list[dict[str, str]]:
    rows = [
        {
            "user_id": record.user_id,
            "name": record.name or "",
            "profile_url": record.profile_url,
            "source": record.source,
            "source_url": record.source_url,
            "error_code": "",
            "error_message": "",
        }
        for record in result.records
    ]

    rows.extend(
        {
            "user_id": "",
            "name": "",
            "profile_url": "",
            "source": issue.action,
            "source_url": issue.target or "",
            "error_code": issue.code,
            "error_message": issue.message,
        }
        for issue in result.issues
    )

    return rows


def _write_csv(
    result: ScrapeResult[UserRecord],
    path: Path,
) -> None:
    with atomic_text_writer(
        path,
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=USER_FIELDS,
        )
        writer.writeheader()
        writer.writerows(_rows(result))


def _write_txt(
    result: ScrapeResult[UserRecord],
    path: Path,
) -> None:
    with atomic_text_writer(
        path,
        encoding="utf-8",
    ) as file:
        for record in result.records:
            file.write(f"User ID: {record.user_id}\n")

        for issue in result.issues:
            file.write(
                f"Error: [{issue.code}] "
                f"{issue.target or '-'} - "
                f"{issue.message}\n"
            )


def _workbook_type():
    try:
        from openpyxl import Workbook
    except ModuleNotFoundError as error:
        raise ConfigurationError(
            "XLSX output requires: " 'python -m pip install -e ".[xlsx]"'
        ) from error

    return Workbook


def ensure_user_format_available(
    format_name: str,
) -> None:
    if format_name not in USER_FORMATS:
        raise ConfigurationError(
            "Unsupported authenticated output format: " f"{format_name}."
        )

    if format_name == "xlsx":
        _workbook_type()


def _write_xlsx(
    result: ScrapeResult[UserRecord],
    path: Path,
) -> None:
    workbook_type = _workbook_type()
    workbook = workbook_type()
    sheet = workbook.active

    sheet.title = "users"
    sheet.append(USER_FIELDS)

    for row in _rows(result):
        sheet.append(tuple(row[field] for field in USER_FIELDS))

    try:
        with atomic_output_path(
            path,
            temporary_suffix=".tmp.xlsx",
        ) as temporary:
            workbook.save(temporary)

    except (ConfigurationError, ExportError):
        raise

    except Exception as error:
        raise ExportError(
            f"Cannot write output file {path}.",
            target=str(path),
        ) from error


def write_users(
    result: ScrapeResult[UserRecord],
    path: Path,
    format_name: str,
) -> bool:
    ensure_user_format_available(format_name)

    if not result.records and not result.issues:
        return False

    normalized = _deduplicated_result(result)
    destination = Path(path)

    if format_name == "csv":
        _write_csv(normalized, destination)
    elif format_name == "json":
        write_json(normalized, destination)
    elif format_name == "txt":
        _write_txt(normalized, destination)
    else:
        _write_xlsx(normalized, destination)

    return True
