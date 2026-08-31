from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from fb_crawl.contacts.models import LookupOutcome, LookupSource
from fb_crawl.core.jobs import Page
from fb_crawl.entitlements.quota import RevealDecision
from fb_crawl.history.models import AccountHistoryQuery, HistoryItem
from fb_crawl.history.service import HistoryService


NOW = datetime(2026, 9, 1, 3, tzinfo=UTC)


def item(*, event_id: int = 71, user_id: int = 41) -> HistoryItem:
    return HistoryItem(
        id=event_id,
        account_id=7,
        device_id=9,
        facebook_user_id=user_id,
        facebook_uid="100123",
        username="sample.user",
        name="Sample User",
        profile_url="https://www.facebook.com/sample.user",
        phone_number_id=81,
        phone="+84981234567",
        outcome=LookupOutcome.FOUND,
        source=LookupSource.CACHE,
        provider_called=False,
        quota_charged=True,
        safe_error_code="",
        created_at=NOW,
        completed_at=NOW,
    )


class Repository:
    def __init__(self, values: tuple[HistoryItem, ...]) -> None:
        self.values = values

    def list(self, _query):
        return Page(self.values, "next")

    def get(self, account_id, event_id):
        return next(
            (
                value
                for value in self.values
                if (value.account_id, value.id) == (account_id, event_id)
            ),
            None,
        )


class Quota:
    def __init__(self, decisions: list[RevealDecision]) -> None:
        self.decisions = decisions
        self.calls: list[tuple[object, ...]] = []

    def reserve(self, account_id, user_id, phone_id, event_id, now):
        self.calls.append((account_id, user_id, phone_id, event_id, now))
        return self.decisions.pop(0)


def test_history_read_reserves_current_month_reveal_before_showing_phone() -> None:
    quota = Quota([RevealDecision(True, True, 91, 1, 100)])
    service = HistoryService(Repository((item(),)), quota, clock=lambda: NOW)

    page = service.list(AccountHistoryQuery(account_id=7))

    assert page.items[0].phone == "+84981234567"
    assert quota.calls == [(7, 41, 81, 71, NOW)]


def test_history_read_withholds_phone_when_current_month_quota_is_exhausted() -> None:
    quota = Quota([RevealDecision(False, False, None, 100, 100)])
    service = HistoryService(Repository((item(),)), quota, clock=lambda: NOW)

    value = service.get(7, 71)

    assert value is not None
    assert value.phone == ""
    assert value.phone_number_id is None


def test_history_rows_without_a_revealed_phone_do_not_touch_quota() -> None:
    quota = Quota([])
    service = HistoryService(
        Repository((replace(item(), phone="", phone_number_id=None),)),
        quota,
        clock=lambda: NOW,
    )

    page = service.list(AccountHistoryQuery(account_id=7))

    assert page.items[0].phone == ""
    assert quota.calls == []
