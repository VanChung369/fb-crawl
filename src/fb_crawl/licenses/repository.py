from __future__ import annotations

from datetime import datetime
from typing import Protocol

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.entitlements.models import Entitlements
from fb_crawl.licenses.models import LicenseGrant, LicenseKey, Subscription


class LicenseError(ValidationError):
    code = "license_error"


class InvalidLicenseKey(LicenseError):
    code = "invalid_license_key"


class LicenseAlreadyRedeemed(LicenseError):
    code = "license_already_redeemed"


class LicenseScheduleError(LicenseError):
    code = "license_schedule_error"


class LicenseRepository(Protocol):
    def create_key(
        self,
        *,
        key_digest: str,
        key_version: int,
        masked_key: str,
        grant: LicenseGrant,
        created_by_account_id: int | None,
        now: datetime,
    ) -> LicenseKey: ...

    def redeem(
        self, account_id: int, key_digest: str, now: datetime
    ) -> Subscription: ...

    def effective_entitlements(
        self, account_id: int, now: datetime
    ) -> Entitlements: ...

    def list_subscriptions(self, account_id: int) -> tuple[Subscription, ...]: ...
