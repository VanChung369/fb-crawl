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


def test_create_stores_encrypted_key_and_reveal_audits_without_plaintext() -> None:
    from fb_crawl.licenses.postgres import PostgresLicenseRepository
    from tests.unit.licenses.test_postgres import ScriptedCursor, connect, key_row

    keys = _keys()
    cursor = ScriptedCursor([key_row(11, "unused")])
    repository = PostgresLicenseRepository("postgresql://hidden", connect_factory=connect(cursor))
    service = LicenseService(repository, keys)
    _, plaintext = service.create_key(GRANT, 7, NOW)
    _, params = next(command for command in cursor.commands if "INSERT INTO license_keys" in command[0])
    encrypted = params[-1]
    assert keys.decrypt(encrypted, 2) == plaintext
    assert plaintext not in repr(cursor.commands)

    row = list(key_row(11, keys.digest(plaintext)))
    row[2] = 2
    row[16] = encrypted
    cursor.one.append(tuple(row))
    assert service.reveal_key(11, 7, NOW) == plaintext
    assert any(params and "license_key_revealed" in params for _, params in cursor.commands)
    assert plaintext not in repr(cursor.commands)


@pytest.mark.parametrize("mode", ["legacy", "tampered", "swapped", "missing-version"])
def test_reveal_fails_closed_for_unrecoverable_keys(mode: str) -> None:
    from fb_crawl.licenses.postgres import PostgresLicenseRepository
    from fb_crawl.licenses.repository import LicenseKeyRevealUnavailable
    from tests.unit.licenses.test_postgres import ScriptedCursor, connect, key_row

    keys = _keys()
    generated = keys.generate()
    row = list(key_row(11, generated.digest))
    row[2] = 2
    if mode == "tampered":
        row[16] = "not-valid-ciphertext"
    elif mode == "swapped":
        row[16] = keys.encrypt(keys.generate().plaintext, 2)
    elif mode == "missing-version":
        row[16] = keys.encrypt(generated.plaintext, 2)
        row[2] = 99
    cursor = ScriptedCursor([tuple(row)])
    repository = PostgresLicenseRepository("postgresql://hidden", connect_factory=connect(cursor))
    with pytest.raises(LicenseKeyRevealUnavailable):
        LicenseService(repository, keys).reveal_key(11, 7, NOW)
    assert not any("INSERT INTO admin_audit_events" in sql for sql, _ in cursor.commands)
