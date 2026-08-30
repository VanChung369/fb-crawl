from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fb_crawl.entitlements.time import add_duration, quota_period
from fb_crawl.licenses.models import LicenseDuration


def test_calendar_month_clamps_end_of_month() -> None:
    start = datetime(2027, 1, 31, 9, tzinfo=UTC)

    assert add_duration(start, LicenseDuration("month", 1)) == datetime(
        2027, 2, 28, 9, tzinfo=UTC
    )


def test_calendar_month_handles_leap_year_and_multiple_years() -> None:
    leap = datetime(2024, 1, 31, 9, tzinfo=UTC)
    assert add_duration(leap, LicenseDuration("month", 1)) == datetime(
        2024, 2, 29, 9, tzinfo=UTC
    )
    assert add_duration(leap, LicenseDuration("month", 13)) == datetime(
        2025, 2, 28, 9, tzinfo=UTC
    )


def test_day_duration_is_exact_elapsed_days() -> None:
    start = datetime(2026, 8, 30, 9, tzinfo=UTC)
    assert add_duration(start, LicenseDuration("day", 7)) == datetime(
        2026, 9, 6, 9, tzinfo=UTC
    )


def test_quota_period_uses_product_timezone_calendar_month() -> None:
    timezone = ZoneInfo("Asia/Ho_Chi_Minh")

    assert quota_period(
        datetime(2026, 8, 31, 16, 59, tzinfo=UTC), timezone
    ) == date(2026, 8, 1)
    assert quota_period(
        datetime(2026, 8, 31, 17, 0, tzinfo=UTC), timezone
    ) == date(2026, 9, 1)
