from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from fb_crawl.core.exceptions import ConfigurationError, ExportError
from fb_crawl.core.models import MessageRecord, ScrapeIssue, ScrapeResult
from fb_crawl.exporters.atomic import atomic_output_path, atomic_text_writer


MESSAGE_FIELDS = (
    "message_id",
    "sender_name",
    "sender_profile_url",
    "text",
    "sent_at",
    "thread_url",
    "source",
    "first_seen",
    "last_seen",
    "error_code",
    "error_message",
)

MESSAGE_FORMATS = frozenset({"csv", "json", "txt", "xlsx"})


def message_record_row(record: MessageRecord) -> dict[str, str]:
    return {
        "message_id": record.message_id,
        "sender_name": record.sender_name or "",
        "sender_profile_url": record.sender_profile_url or "",
        "text": record.text,
        "sent_at": record.sent_at or "",
        "thread_url": record.thread_url,
        "source": record.source,
        "first_seen": record.first_seen or "",
        "last_seen": record.last_seen or "",
        "error_code": "",
        "error_message": "",
    }


def message_issue_row(issue: ScrapeIssue) -> dict[str, str]:
    return {
        "message_id": "",
        "sender_name": "",
        "sender_profile_url": "",
        "text": "",
        "sent_at": "",
        "thread_url": issue.target or "",
        "source": issue.action,
        "first_seen": "",
        "last_seen": "",
        "error_code": issue.code,
        "error_message": issue.message,
    }


def _rows(result: ScrapeResult[MessageRecord]) -> list[dict[str, str]]:
    rows = [message_record_row(record) for record in result.records]
    rows.extend(message_issue_row(issue) for issue in result.issues)
    return rows


def _workbook_type():
    try:
        from openpyxl import Workbook
    except ModuleNotFoundError as error:
        raise ConfigurationError(
            "XLSX output requires: " 'python -m pip install -e ".[xlsx]"'
        ) from error
    return Workbook


def ensure_message_format_available(format_name: str) -> None:
    if format_name not in MESSAGE_FORMATS:
        raise ConfigurationError(
            f"Unsupported authenticated output format: {format_name}."
        )
    if format_name == "xlsx":
        _workbook_type()


def _write_xlsx(
    result: ScrapeResult[MessageRecord],
    path: Path,
) -> None:
    workbook = _workbook_type()()
    sheet = workbook.active
    sheet.title = "messages"
    sheet.append(MESSAGE_FIELDS)
    for row in _rows(result):
        sheet.append(tuple(row[field] for field in MESSAGE_FIELDS))

    try:
        with atomic_output_path(path, temporary_suffix=".tmp.xlsx") as temporary:
            workbook.save(temporary)
    except (ConfigurationError, ExportError):
        raise
    except Exception as error:
        raise ExportError(
            f"Cannot write output file {path}.", target=str(path)
        ) from error


def write_messages(
    result: ScrapeResult[MessageRecord],
    path: Path,
    format_name: str,
) -> bool:
    ensure_message_format_available(format_name)

    if not result.records and not result.issues:
        return False

    destination = Path(path)
    rows = _rows(result)

    if format_name == "csv":
        with atomic_text_writer(
            destination, encoding="utf-8-sig", newline=""
        ) as file:
            writer = csv.DictWriter(file, fieldnames=MESSAGE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    elif format_name == "json":
        payload = {
            "records": [message_record_row(record) for record in result.records],
            "issues": [message_issue_row(issue) for issue in result.issues],
            "stats": asdict(result.stats),
            "enrichment": None,
            "retry": asdict(result.retry) if result.retry is not None else None,
        }
        with atomic_text_writer(destination, encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
    elif format_name == "txt":
        with atomic_text_writer(destination, encoding="utf-8") as file:
            for record in result.records:
                file.write(f"Message ID: {record.message_id}\n")
                if record.sender_name:
                    file.write(f"Sender: {record.sender_name}\n")
                if record.sent_at:
                    file.write(f"Sent At: {record.sent_at}\n")
                file.write(f"Text: {record.text}\n")
                file.write(f"Thread: {record.thread_url}\n\n")
            for issue in result.issues:
                file.write(
                    f"Error: [{issue.code}] {issue.target or '-'} - "
                    f"{issue.message}\n"
                )
    else:
        _write_xlsx(result, destination)

    return True
