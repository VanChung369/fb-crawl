from __future__ import annotations

from pathlib import Path

from fb_crawl.core.models import MessageRecord, ScrapeResult
from fb_crawl.exporters.messages import write_messages
from fb_crawl.exporters.users import ensure_user_format_available, write_users


def ensure_authenticated_format_available(format_name: str) -> None:
    ensure_user_format_available(format_name)


def write_authenticated(
    result: ScrapeResult,
    path: Path,
    format_name: str,
) -> bool:
    is_messages = (
        bool(result.records) and isinstance(result.records[0], MessageRecord)
    ) or any(issue.action == "messages" for issue in result.issues)

    if is_messages:
        return write_messages(result, path, format_name)

    return write_users(result, path, format_name)
