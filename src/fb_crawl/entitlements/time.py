from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fb_crawl.licenses.models import LicenseDuration


def add_duration(start: datetime, duration: LicenseDuration) -> datetime:
    if start.tzinfo is None:
        raise ValueError("subscription start must be timezone-aware")
    if duration.unit == "day":
        return start + timedelta(days=duration.value)
    month_index = start.month - 1 + duration.value
    year_offset, month_zero = divmod(month_index, 12)
    year = start.year + year_offset
    month = month_zero + 1
    day = min(start.day, monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def quota_period(now: datetime, timezone: ZoneInfo) -> date:
    if now.tzinfo is None:
        raise ValueError("quota time must be timezone-aware")
    local = now.astimezone(timezone)
    return date(local.year, local.month, 1)
