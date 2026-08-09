from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from fb_crawl.core.exceptions import ConfigurationError, ExportError
from fb_crawl.core.models import InspectRecord, ScrapeIssue, ScrapeResult
from fb_crawl.exporters.atomic import atomic_output_path, atomic_text_writer


INSPECT_FIELDS = (
    "target_url",
    "target_action",
    "session_valid",
    "document_ready",
    "main_found",
    "dialog_count",
    "visible_profile_links",
    "message_rows",
    "profile_field_labels",
    "parser_version",
    "error_code",
    "error_message",
)


def inspect_record_row(record: InspectRecord) -> dict[str, str]:
    return {
        "target_url": record.target_url,
        "target_action": record.target_action,
        "session_valid": str(record.session_valid).lower(),
        "document_ready": str(record.document_ready).lower(),
        "main_found": str(record.main_found).lower(),
        "dialog_count": str(record.dialog_count),
        "visible_profile_links": str(record.visible_profile_links),
        "message_rows": str(record.message_rows),
        "profile_field_labels": str(record.profile_field_labels),
        "parser_version": record.parser_version,
        "error_code": "",
        "error_message": "",
    }


def inspect_issue_row(issue: ScrapeIssue) -> dict[str, str]:
    return {
        "target_url": issue.target or "",
        "target_action": issue.action,
        "session_valid": "",
        "document_ready": "",
        "main_found": "",
        "dialog_count": "",
        "visible_profile_links": "",
        "message_rows": "",
        "profile_field_labels": "",
        "parser_version": "",
        "error_code": issue.code,
        "error_message": issue.message,
    }


def _rows(result: ScrapeResult[InspectRecord]) -> list[dict[str, str]]:
    rows = [inspect_record_row(record) for record in result.records]
    rows.extend(inspect_issue_row(issue) for issue in result.issues)
    return rows


def _workbook_type():
    try:
        from openpyxl import Workbook
    except ModuleNotFoundError as error:
        raise ConfigurationError(
            "XLSX output requires: " 'python -m pip install -e ".[xlsx]"'
        ) from error
    return Workbook


def write_inspect(
    result: ScrapeResult[InspectRecord],
    path: Path,
    format_name: str,
) -> bool:
    if not result.records and not result.issues:
        return False

    rows = _rows(result)
    destination = Path(path)

    if format_name == "csv":
        with atomic_text_writer(
            destination, encoding="utf-8-sig", newline=""
        ) as file:
            writer = csv.DictWriter(file, fieldnames=INSPECT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    elif format_name == "json":
        payload = {
            "records": [
                inspect_record_row(record) for record in result.records
            ],
            "issues": [inspect_issue_row(issue) for issue in result.issues],
            "stats": asdict(result.stats),
            "enrichment": None,
            "retry": asdict(result.retry) if result.retry is not None else None,
        }
        with atomic_text_writer(destination, encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
    elif format_name == "txt":
        with atomic_text_writer(destination, encoding="utf-8") as file:
            for row in rows:
                file.write(" ".join(f"{key}={value}" for key, value in row.items()))
                file.write("\n")
    elif format_name == "xlsx":
        workbook = _workbook_type()()
        sheet = workbook.active
        sheet.title = "inspect"
        sheet.append(INSPECT_FIELDS)
        for row in rows:
            sheet.append(tuple(row[field] for field in INSPECT_FIELDS))
        try:
            with atomic_output_path(
                destination, temporary_suffix=".tmp.xlsx"
            ) as temporary:
                workbook.save(temporary)
        except (ConfigurationError, ExportError):
            raise
        except Exception as error:
            raise ExportError(
                f"Cannot write output file {destination}.",
                target=str(destination),
            ) from error
    else:
        raise ConfigurationError(
            f"Unsupported authenticated output format: {format_name}."
        )

    return True
