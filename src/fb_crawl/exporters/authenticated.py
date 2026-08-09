from __future__ import annotations

from pathlib import Path

from fb_crawl.core.models import (
    AuthenticatedBatchResult,
    InspectRecord,
    MessageRecord,
    ScrapeResult,
)
from fb_crawl.exporters.messages import write_messages
from fb_crawl.exporters.inspect import write_inspect
from fb_crawl.exporters.phone_evidence import write_phone_evidence
from fb_crawl.exporters.users import ensure_user_format_available, write_users


def ensure_authenticated_format_available(format_name: str) -> None:
    ensure_user_format_available(format_name)


def write_authenticated(
    result: ScrapeResult | AuthenticatedBatchResult,
    path: Path,
    format_name: str,
) -> bool:
    evidence_path = path.with_name(f"{path.stem}-phone-evidence.csv")

    if isinstance(result, AuthenticatedBatchResult):
        user_content = bool(
            result.user_result.records or result.user_result.issues
        )
        message_content = bool(
            result.message_result.records or result.message_result.issues
        )
        inspect_content = bool(
            result.inspect_result.records or result.inspect_result.issues
        )
        written = False

        if user_content:
            written = write_users(
                result.user_result, path, format_name
            ) or written
            written = write_phone_evidence(
                result.user_result,
                evidence_path,
            ) or written

        if message_content:
            message_path = (
                path.with_name(f"{path.stem}-messages{path.suffix}")
                if user_content
                else path
            )
            written = write_messages(
                result.message_result,
                message_path,
                format_name,
            ) or written

        if inspect_content:
            inspect_path = (
                path.with_name(f"{path.stem}-inspect{path.suffix}")
                if user_content or message_content
                else path
            )
            written = write_inspect(
                result.inspect_result,
                inspect_path,
                format_name,
            ) or written

        return written

    is_inspect = (
        bool(result.records) and isinstance(result.records[0], InspectRecord)
    ) or any(issue.action == "inspect" for issue in result.issues)

    if is_inspect:
        return write_inspect(result, path, format_name)

    is_messages = (
        bool(result.records) and isinstance(result.records[0], MessageRecord)
    ) or any(issue.action == "messages" for issue in result.issues)

    if is_messages:
        return write_messages(result, path, format_name)

    written = write_users(result, path, format_name)
    return write_phone_evidence(result, evidence_path) or written
