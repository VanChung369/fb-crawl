from __future__ import annotations

from typing import Protocol

from fb_crawl.core.jobs import Page
from fb_crawl.history.models import AccountHistoryQuery, HistoryItem


class HistoryRepository(Protocol):
    def list(self, query: AccountHistoryQuery) -> Page[HistoryItem]: ...

    def get(self, account_id: int, event_id: int) -> HistoryItem | None: ...

    def delete_one(self, account_id: int, event_id: int) -> bool: ...

    def delete_filtered(self, query: AccountHistoryQuery) -> int: ...
