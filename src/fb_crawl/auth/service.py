from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import secrets
from urllib.parse import quote
from uuid import UUID

from email_validator import EmailNotValidError, validate_email

from fb_crawl.accounts.models import Account, AccountStatus, DeviceStatus
from fb_crawl.accounts.repository import AccountRepository
from fb_crawl.auth.email import EmailDeliveryPort
from fb_crawl.auth.google import GoogleTokenVerifier, GoogleTokenVerifierPort
from fb_crawl.auth.passwords import PasswordHasher
from fb_crawl.auth.rate_limit import RateLimitService
from fb_crawl.auth.tokens import TokenService
from fb_crawl.core.exceptions import ValidationError


EMAIL_VERIFICATION_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)
REFRESH_SESSION_TTL = timedelta(days=30)


class AccountAuthError(ValidationError):
    code = "account_auth_error"


class InvalidCredentials(AccountAuthError):
    code = "invalid_credentials"


class EmailVerificationRequired(AccountAuthError):
    code = "email_verification_required"


class AccountUnavailable(AccountAuthError):
    code = "account_unavailable"


class AccountAlreadyRegistered(AccountAuthError):
    code = "account_already_registered"


class DeviceBindingMismatch(AccountAuthError):
    code = "device_binding_mismatch"


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    account_id: int
    email: str
    verification_required: bool = True


@dataclass(frozen=True, slots=True)
class GenericRequestResult:
    accepted: bool = True


@dataclass(frozen=True, slots=True)
class AuthTokens:
    access_token: str = ""
    refresh_token: str = ""
    access_expires_at: datetime | None = None


class AccountAuthService:
    def __init__(
        self,
        *,
        repository: AccountRepository,
        password_hasher: PasswordHasher,
        token_service: TokenService,
        email_delivery: EmailDeliveryPort,
        rate_limiter: RateLimitService,
        public_base_url: str,
        google_verifier: GoogleTokenVerifierPort | None = None,
    ) -> None:
        self._repository = repository
        self._password_hasher = password_hasher
        self._token_service = token_service
        self._email_delivery = email_delivery
        self._rate_limiter = rate_limiter
        self._public_base_url = public_base_url.rstrip("/")
        self._google_verifier = google_verifier or GoogleTokenVerifier()
        self._dummy_password_hash = password_hasher.hash(
            "lead-finder-invalid-account-password"
        )

    def register(
        self,
        email: str,
        password: str,
        now: datetime,
        *,
        ip_address: str,
    ) -> RegistrationResult:
        normalized, display = _normalized_email(email)
        self._rate_limiter.check("register", normalized, None, ip_address, now)
        _validate_password(password)
        if self._repository.find_account_by_email(normalized) is not None:
            raise AccountAlreadyRegistered("An account already exists for this email.")
        account = self._repository.create_account(
            normalized,
            display,
            self._password_hasher.hash(password),
        )
        self._send_verification(account, now)
        return RegistrationResult(account.id, account.display_email)

    def verify_email(
        self, raw_token: str, now: datetime, *, ip_address: str
    ) -> Account:
        self._rate_limiter.check("verify_email", None, None, ip_address, now)
        return self._repository.verify_email_token(
            self._token_service.digest_opaque(raw_token), now
        )

    def resend_verification(
        self, email: str, now: datetime, *, ip_address: str
    ) -> GenericRequestResult:
        normalized, _display = _normalized_email(email)
        self._rate_limiter.check(
            "resend_verification", normalized, None, ip_address, now
        )
        account = self._repository.find_account_by_email(normalized)
        if account is not None and account.email_verified_at is None:
            self._send_verification(account, now)
        return GenericRequestResult()

    def login(
        self,
        email: str,
        password: str,
        installation_id: UUID,
        device_name: str,
        now: datetime,
        *,
        ip_address: str,
    ) -> AuthTokens:
        normalized, _display = _normalized_email(email)
        self._rate_limiter.check(
            "login", normalized, str(installation_id), ip_address, now
        )
        account = self._repository.find_account_by_email(normalized)
        encoded = (
            self._dummy_password_hash if account is None else account.password_hash
        )
        password_matches = self._password_hasher.verify(encoded, password)
        if account is None or not password_matches:
            raise InvalidCredentials("Email or password is invalid.")
        if account.email_verified_at is None or account.status is AccountStatus.PENDING:
            raise EmailVerificationRequired("Email verification is required.")
        if account.status is not AccountStatus.ACTIVE:
            raise AccountUnavailable("This account is unavailable.")
        _validate_installation(installation_id, device_name)
        device = self._repository.create_device(
            account.id, installation_id, device_name.strip(), now
        )
        if device.status is not DeviceStatus.ACTIVE:
            raise AccountUnavailable("This device is unavailable.")
        raw_refresh = self._token_service.new_opaque_token()
        session = self._repository.create_session(
            account.id,
            device.id,
            self._token_service.digest_opaque(raw_refresh),
            now + REFRESH_SESSION_TTL,
            now,
        )
        return self._auth_tokens(account.id, session.id, device.id, raw_refresh, now)

    def login_with_google(
        self,
        id_token: str,
        installation_id: UUID,
        device_name: str,
        now: datetime,
        *,
        ip_address: str,
    ) -> AuthTokens:
        self._rate_limiter.check(
            "login", None, str(installation_id), ip_address, now
        )
        _validate_installation(installation_id, device_name)
        info = self._google_verifier.verify_id_token(id_token)
        if not info.email_verified:
            raise EmailVerificationRequired("Google email verification is required.")
        normalized, display = _normalized_email(info.email)
        account = self._repository.find_account_by_email(normalized)
        if account is None:
            dummy_password = secrets.token_urlsafe(32)
            dummy_hash = self._password_hasher.hash(dummy_password)
            account = self._repository.create_account(
                normalized, display, dummy_hash
            )
            account = self._repository.verify_account_email_directly(
                account.id, now
            )
        else:
            if account.email_verified_at is None or account.status is AccountStatus.PENDING:
                account = self._repository.verify_account_email_directly(
                    account.id, now
                )
        if account.status is not AccountStatus.ACTIVE:
            raise AccountUnavailable("This account is unavailable.")
        device = self._repository.create_device(
            account.id, installation_id, device_name.strip(), now
        )
        if device.status is not DeviceStatus.ACTIVE:
            raise AccountUnavailable("This device is unavailable.")
        raw_refresh = self._token_service.new_opaque_token()
        session = self._repository.create_session(
            account.id,
            device.id,
            self._token_service.digest_opaque(raw_refresh),
            now + REFRESH_SESSION_TTL,
            now,
        )
        return self._auth_tokens(account.id, session.id, device.id, raw_refresh, now)

    def refresh(
        self,
        refresh_token: str,
        installation_id: UUID,
        now: datetime,
        *,
        ip_address: str,
    ) -> AuthTokens:
        self._rate_limiter.check(
            "refresh", None, str(installation_id), ip_address, now
        )
        new_refresh = self._token_service.new_opaque_token()
        session = self._repository.rotate_session(
            self._token_service.digest_opaque(refresh_token),
            self._token_service.digest_opaque(new_refresh),
            now,
            now + REFRESH_SESSION_TTL,
        )
        device = self._repository.get_device(session.account_id, session.device_id)
        account = self._repository.get_account(session.account_id)
        if (
            device is None
            or device.installation_id != installation_id
            or device.status is not DeviceStatus.ACTIVE
            or account is None
            or account.status is not AccountStatus.ACTIVE
        ):
            self._repository.revoke_session(session.id, now)
            raise DeviceBindingMismatch("Refresh session is not valid for this device.")
        return self._auth_tokens(
            account.id, session.id, device.id, new_refresh, now
        )

    def forgot_password(
        self, email: str, now: datetime, *, ip_address: str
    ) -> GenericRequestResult:
        normalized, _display = _normalized_email(email)
        self._rate_limiter.check(
            "forgot_password", normalized, None, ip_address, now
        )
        account = self._repository.find_account_by_email(normalized)
        if account is not None and account.status not in {
            AccountStatus.DELETED,
            AccountStatus.SUSPENDED,
        }:
            raw_token = self._token_service.new_opaque_token()
            self._repository.create_account_token(
                account.id,
                "password_reset",
                self._token_service.digest_opaque(raw_token),
                now + PASSWORD_RESET_TTL,
                now,
            )
            url = f"{self._public_base_url}/reset-password?token={quote(raw_token)}"
            self._email_delivery.send_password_reset(account.display_email, url)
        return GenericRequestResult()

    def reset_password(
        self,
        raw_token: str,
        new_password: str,
        now: datetime,
        *,
        ip_address: str,
    ) -> GenericRequestResult:
        self._rate_limiter.check("reset_password", None, None, ip_address, now)
        _validate_password(new_password)
        account = self._repository.consume_password_reset_token(
            self._token_service.digest_opaque(raw_token), now
        )
        self._repository.update_password(
            account.id, self._password_hasher.hash(new_password), now
        )
        self._repository.revoke_account_sessions(account.id, now)
        return GenericRequestResult()

    def logout(self, session_id: UUID, now: datetime) -> None:
        self._repository.revoke_session(session_id, now)

    def logout_refresh(self, refresh_token: str, now: datetime) -> None:
        self._repository.revoke_session_by_refresh_digest(
            self._token_service.digest_opaque(refresh_token), now
        )

    def reauthenticate(
        self,
        account_id: int,
        session_id: UUID,
        password: str,
        now: datetime,
        *,
        ip_address: str,
    ) -> GenericRequestResult:
        self._rate_limiter.check(
            "reauthenticate", str(account_id), None, ip_address, now
        )
        account = self._repository.get_account(account_id)
        if account is None or not self._password_hasher.verify(
            account.password_hash, password
        ):
            raise InvalidCredentials("Password is invalid.")
        self._repository.mark_session_reauthenticated(session_id, now)
        return GenericRequestResult()

    def _send_verification(self, account: Account, now: datetime) -> None:
        raw_token = self._token_service.new_opaque_token()
        self._repository.create_account_token(
            account.id,
            "email_verify",
            self._token_service.digest_opaque(raw_token),
            now + EMAIL_VERIFICATION_TTL,
            now,
        )
        url = f"{self._public_base_url}/verify-email?token={quote(raw_token)}"
        self._email_delivery.send_verification(account.display_email, url)

    def _auth_tokens(
        self,
        account_id: int,
        session_id: UUID,
        device_id: int,
        raw_refresh: str,
        now: datetime,
    ) -> AuthTokens:
        access = self._token_service.issue_access(
            account_id, session_id, device_id, now
        )
        return AuthTokens(
            access_token=access,
            refresh_token=raw_refresh,
            access_expires_at=now
            + timedelta(seconds=self._token_service.access_ttl_seconds),
        )


def _normalized_email(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValidationError("A valid email address is required.")
    try:
        validated = validate_email(value.strip(), check_deliverability=False)
    except EmailNotValidError as error:
        raise ValidationError("A valid email address is required.") from error
    display = validated.normalized
    return display.casefold(), display


def _validate_password(value: object) -> str:
    if not isinstance(value, str) or not 12 <= len(value) <= 128:
        raise ValidationError("Password must contain 12 to 128 characters.")
    return value


def _validate_installation(installation_id: object, device_name: object) -> None:
    if not isinstance(installation_id, UUID):
        raise ValidationError("A valid installation ID is required.")
    if (
        not isinstance(device_name, str)
        or not device_name.strip()
        or len(device_name.strip()) > 128
    ):
        raise ValidationError("Device name must contain 1 to 128 characters.")
