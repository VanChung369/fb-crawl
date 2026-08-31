from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Callable

from fb_crawl.core.jobs import Page
from fb_crawl.entitlements.quota import ContactQuotaService
from fb_crawl.history.models import AccountHistoryQuery, HistoryItem
from fb_crawl.history.repository import HistoryRepository
from fb_data_pipeline.repositories.errors import DatabaseError


class HistoryService:
    def __init__(
        self,
        repository: HistoryRepository,
        quota: ContactQuotaService,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repository = repository
        self.quota = quota
        self.clock = clock

    def list(self, query: AccountHistoryQuery) -> Page[HistoryItem]:
        page = self.repository.list(query)
        now = self.clock()
        return Page(
            tuple(
                self._authorize_phone(query.account_id, item, now)
                for item in page.items
            ),
            page.next_cursor,
        )

    def get(self, account_id: int, event_id: int) -> HistoryItem | None:
        item = self.repository.get(account_id, event_id)
        if item is None:
            return None
        return self._authorize_phone(account_id, item, self.clock())

    def _authorize_phone(
        self,
        account_id: int,
        item: HistoryItem,
        now: datetime,
    ) -> HistoryItem:
        if item.account_id != account_id:
            raise DatabaseError("Database history ownership mismatch.")
        if not item.phone or item.phone_number_id is None:
            return item
        decision = self.quota.reserve(
            account_id,
            item.facebook_user_id,
            item.phone_number_id,
            item.id,
            now,
        )
        if decision.allowed:
            return item
        return replace(item, phone_number_id=None, phone="")
