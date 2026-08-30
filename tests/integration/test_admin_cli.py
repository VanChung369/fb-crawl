from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from fb_crawl.accounts.models import Account, AccountRole, AccountStatus
from fb_crawl.accounts.repository import AdminAlreadyExists
from fb_crawl.auth.passwords import PasswordHasher
from fb_crawl.cli.admin import AdminCommand


NOW = datetime(2026, 8, 30, 8, tzinfo=UTC)


class AdminRepositoryFake:
    def __init__(self) -> None:
        self.admin: Account | None = None
        self.deleted_at = {
            11: NOW - timedelta(days=31),
            12: NOW - timedelta(days=29),
        }
        self.cutoffs: list[datetime] = []

    def bootstrap_admin(self, normalized_email, display_email, password_hash, now):
        if self.admin is not None:
            raise AdminAlreadyExists("An administrator already exists.")
        self.admin = Account(
            id=1,
            normalized_email=normalized_email,
            display_email=display_email,
            password_hash=password_hash,
            role=AccountRole.ADMIN,
            status=AccountStatus.ACTIVE,
            email_verified_at=now,
            created_at=now,
            updated_at=now,
        )
        return self.admin

    def purge_due_deleted_accounts(self, cutoff: datetime):
        self.cutoffs.append(cutoff)
        purged = tuple(
            account_id
            for account_id, deleted_at in self.deleted_at.items()
            if deleted_at <= cutoff
        )
        for account_id in purged:
            del self.deleted_at[account_id]
        return purged


def test_second_bootstrap_is_rejected_and_password_is_hashed() -> None:
    repository = AdminRepositoryFake()
    command = AdminCommand(repository, PasswordHasher())

    admin = command.bootstrap(
        " Admin@EXAMPLE.com ",
        "a strong admin password",
        NOW,
    )

    assert admin.role is AccountRole.ADMIN
    assert admin.normalized_email == "admin@example.com"
    assert admin.password_hash != "a strong admin password"
    with pytest.raises(AdminAlreadyExists):
        command.bootstrap(
            "other@example.com",
            "another strong password",
            NOW,
        )


def test_purge_deletes_only_accounts_past_recovery_window() -> None:
    repository = AdminRepositoryFake()
    command = AdminCommand(repository, PasswordHasher())

    count = command.purge_deleted(now=NOW, recovery_days=30)

    assert count == 1
    assert repository.deleted_at == {12: NOW - timedelta(days=29)}
    assert repository.cutoffs == [NOW - timedelta(days=30)]


@pytest.mark.parametrize("days", [0, -1, 3661])
def test_purge_recovery_window_is_bounded(days: int) -> None:
    command = AdminCommand(AdminRepositoryFake(), PasswordHasher())

    with pytest.raises(ValueError, match="recovery_days"):
        command.purge_deleted(now=NOW, recovery_days=days)
