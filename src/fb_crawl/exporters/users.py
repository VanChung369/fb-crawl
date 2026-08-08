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
from fb_crawl.exporters.schema import (
    UNIFIED_FIELDS,
    issue_row,
    user_record_row,
)

USER_FIELDS = UNIFIED_FIELDS

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
    rows = [user_record_row(record) for record in result.records]
    rows.extend(issue_row(issue) for issue in result.issues)

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
            if record.name:
                file.write(f"Name: {record.name}\n")
            if record.phone_numbers:
                file.write(f"Phone Numbers: {'; '.join(record.phone_numbers)}\n")
            if record.website:
                file.write(f"Website: {record.website}\n")
            if record.address:
                file.write(f"Address: {record.address}\n")
            if record.current_city:
                file.write(f"Current City: {record.current_city}\n")
            if record.hometown:
                file.write(f"Hometown: {record.hometown}\n")
            if record.birth_date:
                file.write(f"Birth Date: {record.birth_date}\n")
            if record.birth_year is not None:
                file.write(f"Birth Year: {record.birth_year}\n")

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
