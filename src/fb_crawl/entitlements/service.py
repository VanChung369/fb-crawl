from __future__ import annotations

from datetime import datetime
from typing import Protocol

from fb_crawl.entitlements.models import Entitlements


class EntitlementRepository(Protocol):
    def effective_entitlements(
        self, account_id: int, now: datetime
    ) -> Entitlements: ...


class EntitlementService:
    def __init__(self, repository: EntitlementRepository) -> None:
        self.repository = repository

    def for_account(self, account_id: int, now: datetime) -> Entitlements:
        return self.repository.effective_entitlements(account_id, now)
