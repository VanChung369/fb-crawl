from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest

from fb_crawl.accounts.models import (
    Account,
    AccountRole,
    AccountStatus,
    AuthSession,
    Device,
    DeviceStatus,
)
from fb_crawl.accounts.repository import InvalidAccountToken
from fb_crawl.auth.passwords import PasswordHasher
from fb_crawl.auth.rate_limit import AuthRateLimited
from fb_crawl.auth.service import (
    AccountAuthService,
    DeviceBindingMismatch,
    EmailVerificationRequired,
    InvalidCredentials,
)
from fb_crawl.auth.tokens import TokenService


NOW = datetime(2026, 8, 30, 8, tzinfo=UTC)
EMAIL = "person@example.com"
PASSWORD = "correct horse battery staple"
INSTALLATION_ID = UUID("12345678-1234-5678-1234-567812345678")


class FakeRepository:
    def __init__(self, hasher: PasswordHasher) -> None:
        self.hasher = hasher
        self.account: Account | None = None
        self.tokens: dict[str, tuple[str, int, datetime, bool]] = {}
        self.devices: dict[int, Device] = {}
        self.sessions: dict[UUID, tuple[AuthSession, str]] = {}
        self.revoked_accounts: list[int] = []
        self.password_updates: list[str] = []

    def add_account(self, *, verified: bool, status: AccountStatus | None = None) -> Account:
        account_status = status or (
            AccountStatus.ACTIVE if verified else AccountStatus.PENDING
        )
        self.account = Account(
            id=7,
            normalized_email=EMAIL,
            display_email=EMAIL,
            password_hash=self.hasher.hash(PASSWORD),
            role=AccountRole.USER,
            status=account_status,
            email_verified_at=NOW if verified else None,
            created_at=NOW,
            updated_at=NOW,
        )
        return self.account

    def create_account(self, normalized_email: str, display_email: str, password_hash: str) -> Account:
        assert self.account is None
        self.account = Account(
            id=7,
            normalized_email=normalized_email,
            display_email=display_email,
            password_hash=password_hash,
            role=AccountRole.USER,
            status=AccountStatus.PENDING,
            email_verified_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        return self.account

    def find_account_by_email(self, normalized_email: str) -> Account | None:
        if self.account and self.account.normalized_email == normalized_email:
            return self.account
        return None

    def get_account(self, account_id: int) -> Account | None:
        if self.account and self.account.id == account_id:
            return self.account
        return None

    def create_account_token(self, account_id, purpose, token_digest, expires_at, now) -> None:
        self.tokens[token_digest] = (purpose, account_id, expires_at, False)

    def verify_email_token(self, token_digest: str, now: datetime) -> Account:
        purpose, account_id, expires_at, consumed = self.tokens[token_digest]
        if purpose != "email_verify" or consumed or expires_at <= now:
            raise InvalidAccountToken("invalid")
        self.tokens[token_digest] = (purpose, account_id, expires_at, True)
        assert self.account is not None
        self.account = replace(
            self.account,
            status=AccountStatus.ACTIVE,
            email_verified_at=now,
            updated_at=now,
        )
        return self.account

    def consume_password_reset_token(self, token_digest: str, now: datetime) -> Account:
        purpose, account_id, expires_at, consumed = self.tokens[token_digest]
        if purpose != "password_reset" or consumed or expires_at <= now:
            raise InvalidAccountToken("invalid")
        self.tokens[token_digest] = (purpose, account_id, expires_at, True)
        assert self.account is not None
        return self.account

    def update_password(self, account_id: int, password_hash: str, now: datetime) -> Account:
        assert self.account is not None and self.account.id == account_id
        self.password_updates.append(password_hash)
        self.account = replace(self.account, password_hash=password_hash, updated_at=now)
        return self.account

    def create_device(self, account_id, installation_id, display_name, now) -> Device:
        existing = next(
            (item for item in self.devices.values() if item.installation_id == installation_id),
            None,
        )
        if existing:
            return existing
        device = Device(
            id=len(self.devices) + 9,
            account_id=account_id,
            installation_id=installation_id,
            display_name=display_name,
            status=DeviceStatus.ACTIVE,
            first_seen_at=now,
            last_seen_at=now,
        )
        self.devices[device.id] = device
        return device

    def get_device(self, account_id: int, device_id: int) -> Device | None:
        device = self.devices.get(device_id)
        return device if device and device.account_id == account_id else None

    def create_session(self, account_id, device_id, refresh_digest, expires_at, now) -> AuthSession:
        session = AuthSession(
            id=uuid4(),
            account_id=account_id,
            device_id=device_id,
            expires_at=expires_at,
            rotated_from_id=None,
            revoked_at=None,
            created_at=now,
            last_used_at=now,
            authenticated_at=now,
        )
        self.sessions[session.id] = (session, refresh_digest)
        return session

    def rotate_session(self, old_digest, new_digest, now, expires_at) -> AuthSession:
        old = next(
            (pair for pair in self.sessions.values() if pair[1] == old_digest),
            None,
        )
        if old is None:
            raise InvalidAccountToken("invalid refresh")
        old_session, _ = old
        self.sessions[old_session.id] = (replace(old_session, revoked_at=now), old_digest)
        session = AuthSession(
            id=uuid4(),
            account_id=old_session.account_id,
            device_id=old_session.device_id,
            expires_at=expires_at,
            rotated_from_id=old_session.id,
            revoked_at=None,
            created_at=now,
            last_used_at=now,
            authenticated_at=old_session.authenticated_at,
        )
        self.sessions[session.id] = (session, new_digest)
        return session

    def revoke_session(self, session_id: UUID, now: datetime) -> None:
        session, digest = self.sessions[session_id]
        self.sessions[session_id] = (replace(session, revoked_at=now), digest)

    def mark_session_reauthenticated(self, session_id: UUID, now: datetime) -> AuthSession:
        session, digest = self.sessions[session_id]
        updated = replace(session, authenticated_at=now, last_used_at=now)
        self.sessions[session_id] = (updated, digest)
        return updated

    def revoke_account_sessions(self, account_id: int, now: datetime) -> None:
        self.revoked_accounts.append(account_id)
        for session_id, (session, digest) in tuple(self.sessions.items()):
            if session.account_id == account_id:
                self.sessions[session_id] = (replace(session, revoked_at=now), digest)


class FakeEmail:
    def __init__(self) -> None:
        self.verifications: list[tuple[str, str]] = []
        self.resets: list[tuple[str, str]] = []

    def send_verification(self, email: str, url: str) -> None:
        self.verifications.append((email, url))

    def send_password_reset(self, email: str, url: str) -> None:
        self.resets.append((email, url))


class FakeLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.blocked: set[str] = set()

    def check(self, action, account_key, device_key, ip_address, now) -> None:
        self.calls.append((action, account_key, device_key, ip_address, now))
        if action in self.blocked:
            raise AuthRateLimited("Too many requests.")


@pytest.fixture
def service_parts():
    hasher = PasswordHasher()
    repository = FakeRepository(hasher)
    email = FakeEmail()
    limiter = FakeLimiter()
    tokens = TokenService(jwt_secret="j" * 32, token_hmac_secret="h" * 32)
    service = AccountAuthService(
        repository=repository,
        password_hasher=hasher,
        token_service=tokens,
        email_delivery=email,
        rate_limiter=limiter,
        public_base_url="https://leads.example.com",
    )
    return service, repository, email, limiter, tokens


def _token_from_url(url: str) -> str:
    return parse_qs(urlsplit(url).query)["token"][0]


def test_register_normalizes_email_hashes_password_and_sends_verification(service_parts) -> None:
    service, repository, email, limiter, _tokens = service_parts

    result = service.register(
        "  Person@EXAMPLE.com ", PASSWORD, NOW, ip_address="203.0.113.4"
    )

    assert result.account_id == 7
    assert result.verification_required is True
    assert repository.account is not None
    assert repository.account.normalized_email == EMAIL
    assert repository.account.password_hash != PASSWORD
    assert email.verifications[0][0] == "Person@example.com"
    assert _token_from_url(email.verifications[0][1]) not in repository.tokens
    assert limiter.calls[0][:2] == ("register", EMAIL)


def test_login_requires_verified_email(service_parts) -> None:
    service, repository, _email, _limiter, _tokens = service_parts
    repository.add_account(verified=False)

    with pytest.raises(EmailVerificationRequired):
        service.login(
            EMAIL,
            PASSWORD,
            INSTALLATION_ID,
            "Chrome",
            NOW,
            ip_address="203.0.113.4",
        )

    assert repository.sessions == {}


def test_login_and_refresh_are_device_bound(service_parts) -> None:
    service, repository, _email, _limiter, tokens = service_parts
    repository.add_account(verified=True)
    authenticated = service.login(
        EMAIL,
        PASSWORD,
        INSTALLATION_ID,
        "Chrome",
        NOW,
        ip_address="203.0.113.4",
    )

    claims = tokens.decode_access(authenticated.access_token, NOW)
    assert claims.account_id == 7
    assert authenticated.refresh_token not in repr(repository.sessions)

    refreshed = service.refresh(
        authenticated.refresh_token,
        INSTALLATION_ID,
        NOW + timedelta(minutes=5),
        ip_address="203.0.113.4",
    )
    assert refreshed.refresh_token != authenticated.refresh_token

    with pytest.raises(DeviceBindingMismatch):
        service.refresh(
            refreshed.refresh_token,
            UUID("99999999-9999-4999-8999-999999999999"),
            NOW + timedelta(minutes=6),
            ip_address="203.0.113.4",
        )


def test_refresh_preserves_password_auth_time_until_explicit_reauthentication(service_parts) -> None:
    service, repository, _email, _limiter, _tokens = service_parts
    repository.add_account(verified=True)
    authenticated = service.login(
        EMAIL,
        PASSWORD,
        INSTALLATION_ID,
        "Chrome",
        NOW,
        ip_address="203.0.113.4",
    )

    service.refresh(
        authenticated.refresh_token,
        INSTALLATION_ID,
        NOW + timedelta(minutes=20),
        ip_address="203.0.113.4",
    )
    active_session = next(
        session for session, _digest in repository.sessions.values()
        if session.revoked_at is None
    )
    assert active_session.authenticated_at == NOW

    service.reauthenticate(
        7,
        active_session.id,
        PASSWORD,
        NOW + timedelta(minutes=21),
        ip_address="203.0.113.4",
    )
    updated_session, _digest = repository.sessions[active_session.id]
    assert updated_session.authenticated_at == NOW + timedelta(minutes=21)


def test_password_reset_revokes_all_sessions(service_parts) -> None:
    service, repository, email, _limiter, _tokens = service_parts
    repository.add_account(verified=True)
    service.login(
        EMAIL,
        PASSWORD,
        INSTALLATION_ID,
        "Chrome",
        NOW,
        ip_address="203.0.113.4",
    )
    service.forgot_password(EMAIL, NOW, ip_address="203.0.113.4")
    raw_token = _token_from_url(email.resets[-1][1])

    service.reset_password(
        raw_token,
        "a new secure password",
        NOW + timedelta(minutes=1),
        ip_address="203.0.113.4",
    )

    assert repository.revoked_accounts == [7]
    assert repository.password_updates
    assert service.login(
        EMAIL,
        "a new secure password",
        INSTALLATION_ID,
        "Chrome",
        NOW + timedelta(minutes=2),
        ip_address="203.0.113.4",
    )


def test_login_rate_limit_runs_before_password_verification(service_parts) -> None:
    service, repository, _email, limiter, _tokens = service_parts
    repository.add_account(verified=True)
    limiter.blocked.add("login")

    with pytest.raises(AuthRateLimited):
        service.login(
            EMAIL,
            "wrong password",
            INSTALLATION_ID,
            "Chrome",
            NOW,
            ip_address="203.0.113.4",
        )


def test_unknown_email_and_wrong_password_share_invalid_credentials(service_parts) -> None:
    service, repository, _email, _limiter, _tokens = service_parts

    with pytest.raises(InvalidCredentials) as unknown:
        service.login(
            "unknown@example.com",
            PASSWORD,
            INSTALLATION_ID,
            "Chrome",
            NOW,
            ip_address="203.0.113.4",
        )
    repository.add_account(verified=True)
    with pytest.raises(InvalidCredentials) as wrong:
        service.login(
            EMAIL,
            "wrong password",
            INSTALLATION_ID,
            "Chrome",
            NOW,
            ip_address="203.0.113.4",
        )

    assert unknown.value.safe_message == wrong.value.safe_message
