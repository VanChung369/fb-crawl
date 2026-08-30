from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from fb_crawl.accounts.models import Account, AuthSession, Device
from fb_crawl.core.exceptions import ValidationError


TokenPurpose = Literal["email_verify", "password_reset"]


class AccountRepositoryError(ValidationError):
    code = "account_repository_error"


class AccountNotFound(AccountRepositoryError):
    code = "account_not_found"


class DeviceNotFound(AccountRepositoryError):
    code = "device_not_found"


class InvalidAccountToken(AccountRepositoryError):
    code = "account_token_invalid"


class SessionUnavailable(AccountRepositoryError):
    code = "auth_session_unavailable"


class SessionReuseDetected(SessionUnavailable):
    code = "refresh_token_reuse_detected"


class AccountRepository(Protocol):
    def create_account(
        self,
        normalized_email: str,
        display_email: str,
        password_hash: str,
    ) -> Account: ...

    def find_account_by_email(self, normalized_email: str) -> Account | None: ...

    def get_account(self, account_id: int) -> Account | None: ...

    def create_account_token(
        self,
        account_id: int,
        purpose: TokenPurpose,
        token_digest: str,
        expires_at: datetime,
        now: datetime,
    ) -> None: ...

    def verify_email_token(self, token_digest: str, now: datetime) -> Account: ...

    def create_device(
        self,
        account_id: int,
        installation_id: UUID,
        display_name: str,
        now: datetime,
    ) -> Device: ...

    def list_devices(self, account_id: int) -> tuple[Device, ...]: ...

    def revoke_device(
        self, account_id: int, device_id: int, now: datetime
    ) -> Device: ...

    def create_session(
        self,
        account_id: int,
        device_id: int,
        refresh_digest: str,
        expires_at: datetime,
        now: datetime,
    ) -> AuthSession: ...

    def rotate_session(
        self,
        old_digest: str,
        new_digest: str,
        now: datetime,
        expires_at: datetime,
    ) -> AuthSession: ...

    def revoke_session(self, session_id: UUID, now: datetime) -> None: ...

    def revoke_account_sessions(self, account_id: int, now: datetime) -> None: ...

    def request_account_deletion(self, account_id: int, now: datetime) -> Account: ...

    def purge_due_deleted_accounts(self, cutoff: datetime) -> tuple[int, ...]: ...

    def record_rate_limit_hit(
        self,
        bucket_hash: str,
        action: str,
        window_start: datetime,
        expires_at: datetime,
    ) -> int: ...
