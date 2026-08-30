from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from fb_crawl.accounts.models import (
    Account,
    AccountRole,
    AccountStatus,
    AuthSession,
    Device,
    DeviceStatus,
)


NOW = datetime(2026, 8, 30, 8, tzinfo=UTC)


def test_account_model_is_typed_frozen_and_hides_password_hash() -> None:
    account = Account(
        id=7,
        normalized_email="person@example.com",
        display_email="Person@example.com",
        password_hash="$argon2id$private",
        role=AccountRole.USER,
        status=AccountStatus.PENDING,
        email_verified_at=None,
        created_at=NOW,
        updated_at=NOW,
    )

    assert account.role is AccountRole.USER
    assert account.status is AccountStatus.PENDING
    assert "$argon2id$private" not in repr(account)
    with pytest.raises(FrozenInstanceError):
        account.status = AccountStatus.ACTIVE  # type: ignore[misc]


def test_device_and_session_models_keep_only_server_identity_state() -> None:
    installation_id = UUID("12345678-1234-5678-1234-567812345678")
    session_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    device = Device(
        id=9,
        account_id=7,
        installation_id=installation_id,
        display_name="Chrome on Windows",
        status=DeviceStatus.ACTIVE,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    session = AuthSession(
        id=session_id,
        account_id=7,
        device_id=9,
        expires_at=NOW + timedelta(days=30),
        rotated_from_id=None,
        revoked_at=None,
        created_at=NOW,
        last_used_at=NOW,
        authenticated_at=NOW,
    )

    assert device.installation_id == installation_id
    assert session.device_id == device.id
    assert "refresh" not in repr(session).casefold()
