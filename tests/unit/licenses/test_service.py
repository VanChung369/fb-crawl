from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fb_crawl.licenses.config import LicenseKeyRing
from fb_crawl.licenses.keys import LicenseKeyService
from fb_crawl.licenses.models import (
    LicenseDuration,
    LicenseGrant,
    Subscription,
    SubscriptionStatus,
)
from fb_crawl.licenses.repository import InvalidLicenseKey
from fb_crawl.licenses.service import LicenseService


NOW = datetime(2026, 8, 30, 8, tzinfo=UTC)
GRANT = LicenseGrant(LicenseDuration("day", 7), 500, 2, True, False)


class Repository:
    def __init__(self, accepted_digest: str | None) -> None:
        self.accepted_digest = accepted_digest
        self.attempts: list[str] = []

    def redeem(self, account_id: int, digest: str, now: datetime):
        self.attempts.append(digest)
        if digest != self.accepted_digest:
            raise InvalidLicenseKey("database detail must not escape")
        return Subscription(
            21,
            account_id,
            11,
            GRANT,
            now,
            now + timedelta(days=7),
            SubscriptionStatus.VALID,
            None,
            now,
        )


def _keys() -> LicenseKeyService:
    return LicenseKeyService(
        LicenseKeyRing(2, {1: b"o" * 32, 2: b"n" * 32})
    )


def test_redeem_accepts_digest_from_historical_key_version() -> None:
    keys = _keys()
    plaintext = "LF-ABCD-EFGH-IJKL"
    repository = Repository(keys.candidate_digests(plaintext)[1])

    subscription = LicenseService(repository, keys).redeem(7, plaintext, NOW)

    assert subscription.account_id == 7
    assert repository.attempts == [keys.candidate_digests(plaintext)[1]]


@pytest.mark.parametrize("plaintext", ["bad", "LF-UNKNOWN-KEY-1234"])
def test_redeem_returns_same_generic_error_for_malformed_and_unknown_keys(
    plaintext: str,
) -> None:
    keys = _keys()
    repository = Repository(None)

    with pytest.raises(InvalidLicenseKey, match="License key is invalid"):
        LicenseService(repository, keys).redeem(7, plaintext, NOW)
