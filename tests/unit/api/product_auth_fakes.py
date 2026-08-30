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
        )
        self.deleted = False
        self.revoked_devices: list[int] = []

    def get_account(self, account_id: int):
        return self.account if account_id == self.account.id else None

    def get_device(self, account_id: int, device_id: int):
        if account_id == self.account.id and device_id == self.device.id:
            return self.device
        return None

    def get_session(self, session_id: UUID):
        return self.session if session_id == self.session.id else None

    def list_devices(self, account_id: int):
        return (self.device,) if account_id == self.account.id else ()

    def revoke_device(self, account_id: int, device_id: int, now: datetime):
        self.revoked_devices.append(device_id)
        self.device = replace(self.device, status=DeviceStatus.REVOKED, last_seen_at=now)
        self.session = replace(self.session, revoked_at=now)
        return self.device

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


class ProductAuthServiceFake:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
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
