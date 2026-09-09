from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fb_crawl.accounts.models import (
    Account,
    AccountRole,
    AccountStatus,
    AuthSession,
    Device,
    DeviceStatus,
)
from fb_crawl.auth.service import AuthTokens, GenericRequestResult, RegistrationResult


NOW = datetime(2026, 8, 30, 8, tzinfo=UTC)
INSTALLATION_ID = UUID("12345678-1234-5678-1234-567812345678")
SESSION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class ProductRepositoryFake:
    def __init__(self) -> None:
        self.account = Account(
            id=7,
            normalized_email="person@example.com",
            display_email="Person@example.com",
            password_hash="$argon2id$private",
            role=AccountRole.USER,
            status=AccountStatus.ACTIVE,
            email_verified_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
        self.device = Device(
            id=9,
            account_id=7,
            installation_id=INSTALLATION_ID,
            display_name="Chrome",
            status=DeviceStatus.ACTIVE,
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
        self.session = AuthSession(
            id=SESSION_ID,
            account_id=7,
            device_id=9,
            expires_at=NOW + timedelta(days=30),
            rotated_from_id=None,
            revoked_at=None,
            created_at=NOW,
            last_used_at=NOW,
            authenticated_at=NOW,
        )
        self.deleted = False
        self.revoked_devices: list[int] = []
        self.extra_devices: list[Device] = []
        self.extra_accounts: dict[int, Account] = {}
        self.suspended_accounts: list[int] = []
        self.revoked_account_sessions: list[int] = []
        self.admin_audits: list[str] = []
        self.fail_admin_audit = False
        self.rate_limit_counts: dict[tuple[str, str], int] = {}

    def find_account_by_email(self, normalized_email: str) -> Account | None:
        if self.account.normalized_email == normalized_email:
            return self.account
        for acc in self.extra_accounts.values():
            if acc.normalized_email == normalized_email:
                return acc
        return None

    def create_account(
        self, normalized_email: str, display_email: str, password_hash: str
    ) -> Account:
        new_id = len(self.extra_accounts) + 100
        new_acc = Account(
            id=new_id,
            normalized_email=normalized_email,
            display_email=display_email,
            password_hash=password_hash,
            role=AccountRole.USER,
            status=AccountStatus.PENDING,
            email_verified_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        self.extra_accounts[new_id] = new_acc
        return new_acc

    def create_device(
        self, account_id: int, installation_id: UUID, display_name: str, now: datetime
    ) -> Device:
        device = Device(
            id=len(self.extra_devices) + 10,
            account_id=account_id,
            installation_id=installation_id,
            display_name=display_name,
            status=DeviceStatus.ACTIVE,
            first_seen_at=now,
            last_seen_at=now,
        )
        self.extra_devices.append(device)
        return device

    def create_session(
        self,
        account_id: int,
        device_id: int,
        refresh_digest: str,
        expires_at: datetime,
        now: datetime,
    ) -> AuthSession:
        self.session = AuthSession(
            id=SESSION_ID,
            account_id=account_id,
            device_id=device_id,
            expires_at=expires_at,
            rotated_from_id=None,
            revoked_at=None,
            created_at=now,
            last_used_at=now,
            authenticated_at=now,
        )
        return self.session

    def get_account(self, account_id: int):
        return self.account if account_id == self.account.id else self.extra_accounts.get(account_id)

    def get_device(self, account_id: int, device_id: int):
        if account_id == self.account.id and device_id == self.device.id:
            return self.device
        for device in self.extra_devices:
            if account_id == device.account_id and device_id == device.id:
                return device
        return None

    def get_session(self, session_id: UUID):
        return self.session if session_id == self.session.id else None

    def list_devices(self, account_id: int):
        if account_id != self.account.id:
            return ()
        return tuple(
            sorted(
                (self.device, *self.extra_devices),
                key=lambda item: (item.first_seen_at, item.id),
            )
        )

    def list_accounts(self, *, limit: int = 100, cursor: int | None = None):
        return (self.account, *self.extra_accounts.values())

    def add_user_account(self, account_id: int) -> None:
        self.extra_accounts[account_id] = replace(
            self.account,
            id=account_id,
            role=AccountRole.USER,
            normalized_email=f"user{account_id}@example.com",
            display_email=f"user{account_id}@example.com",
        )

    def suspend_account(self, account_id: int, now: datetime):
        self.suspended_accounts.append(account_id)
        if account_id == self.account.id:
            self.account = replace(
                self.account, status=AccountStatus.SUSPENDED, updated_at=now
            )
            self.session = replace(self.session, revoked_at=now)
            return self.account
        target = replace(
            self.extra_accounts[account_id], status=AccountStatus.SUSPENDED, updated_at=now
        )
        self.extra_accounts[account_id] = target
        return target

    def suspend_account_as_admin(self, account_id: int, actor_account_id: int, now: datetime):
        if account_id == actor_account_id or self.extra_accounts.get(account_id, self.account).role is AccountRole.ADMIN:
            from fb_crawl.accounts.repository import AdminAccountProtected
            raise AdminAccountProtected("Administrator accounts cannot be suspended here.")
        if self.fail_admin_audit:
            raise RuntimeError("audit unavailable")
        account = self.suspend_account(account_id, now)
        self.admin_audits.append("account_suspended")
        return account

    def revoke_account_sessions(self, account_id: int, now: datetime):
        self.revoked_account_sessions.append(account_id)
        self.session = replace(self.session, revoked_at=now)

    def revoke_account_sessions_as_admin(self, account_id: int, actor_account_id: int, now: datetime):
        if self.fail_admin_audit:
            raise RuntimeError("audit unavailable")
        self.revoke_account_sessions(account_id, now)
        self.admin_audits.append("account_sessions_revoked")

    def revoke_device(self, account_id: int, device_id: int, now: datetime):
        self.revoked_devices.append(device_id)
        self.device = replace(self.device, status=DeviceStatus.REVOKED, last_seen_at=now)
        self.session = replace(self.session, revoked_at=now)
        return self.device

    def revoke_device_as_admin(self, account_id: int, device_id: int, actor_account_id: int, now: datetime):
        if self.fail_admin_audit:
            raise RuntimeError("audit unavailable")
        device = self.revoke_device(account_id, device_id, now)
        self.admin_audits.append("account_device_revoked")
        return device

    def request_account_deletion(self, account_id: int, now: datetime):
        self.deleted = True
        self.account = replace(
            self.account,
            status=AccountStatus.DELETED,
            deletion_requested_at=now,
            updated_at=now,
        )
        self.session = replace(self.session, revoked_at=now)
        return self.account

    def verify_account_email_directly(self, account_id: int, now: datetime):
        account = self.get_account(account_id)
        if account is None:
            from fb_crawl.accounts.repository import AccountNotFound
            raise AccountNotFound(f"Account {account_id} not found.")
        verified = replace(
            account,
            email_verified_at=account.email_verified_at or now,
            status=AccountStatus.ACTIVE if account.status == AccountStatus.PENDING else account.status,
            updated_at=now,
        )
        if account.id == self.account.id:
            self.account = verified
        else:
            self.extra_accounts[account.id] = verified
        return verified

    def record_rate_limit_hit(self, bucket_hash, action, window_start, expires_at):
        key = (bucket_hash, action)
        self.rate_limit_counts[key] = self.rate_limit_counts.get(key, 0) + 1
        return self.rate_limit_counts[key]


class ProductAuthServiceFake:
    def __init__(self, access_token: str, repository=None) -> None:
        self.access_token = access_token
        self.repository = repository
        self.calls: list[tuple[str, object]] = []

    def register(self, email, password, now, *, ip_address):
        self.calls.append(("register", email))
        return RegistrationResult(7, email.strip())

    def verify_email(self, token, now, *, ip_address):
        self.calls.append(("verify_email", token))
        return object()

    def resend_verification(self, email, now, *, ip_address):
        self.calls.append(("resend_verification", email))
        return GenericRequestResult()

    def login(self, email, password, installation_id, device_name, now, *, ip_address):
        self.calls.append(("login", email))
        return AuthTokens(
            self.access_token,
            "opaque-refresh-token",
            now + timedelta(minutes=15),
        )

    def refresh(self, refresh_token, installation_id, now, *, ip_address):
        self.calls.append(("refresh", refresh_token))
        return AuthTokens(
            self.access_token,
            "rotated-refresh-token",
            now + timedelta(minutes=15),
        )

    def forgot_password(self, email, now, *, ip_address):
        self.calls.append(("forgot_password", email))
        return GenericRequestResult()

    def reset_password(self, token, new_password, now, *, ip_address):
        self.calls.append(("reset_password", token))
        return GenericRequestResult()

    def logout(self, session_id, now):
        self.calls.append(("logout", session_id))

    def logout_refresh(self, refresh_token, now):
        self.calls.append(("logout_refresh", refresh_token))

    def reauthenticate(self, account_id, session_id, password, now, *, ip_address):
        self.calls.append(("reauthenticate", account_id))
        if self.repository is not None:
            self.repository.session = replace(
                self.repository.session,
                authenticated_at=now,
                last_used_at=now,
            )
        return GenericRequestResult()

    def login_with_google(
        self, id_token, installation_id, device_name, now, *, ip_address
    ):
        self.calls.append(("login_with_google", id_token))
        return AuthTokens(
            self.access_token,
            "opaque-google-refresh-token",
            now + timedelta(minutes=15),
        )
