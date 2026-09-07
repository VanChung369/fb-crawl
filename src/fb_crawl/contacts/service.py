from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Callable, Protocol
from uuid import uuid4

from fb_crawl.accounts.models import (
    Account,
    AccountStatus,
    Device,
    DeviceStatus,
)
from fb_crawl.contacts.models import (
    CachedContact,
    ContactIdentity,
    LookupEvent,
    LookupOutcome,
    LookupScanContext,
    LookupSource,
)
from fb_data_pipeline.core.models import (
    FacebookIdentity,
    ProviderResult,
    ProviderStatus,
    UserBundle,
)
from fb_data_pipeline.repositories.errors import DatabaseIdentityConflict


@dataclass(frozen=True, slots=True)
class ContactLookupRequest:
    facebook_uid: str = ""
    username: str = ""
    name: str = ""
    profile_url: str = ""

    @property
    def identity(self) -> FacebookIdentity:
        identity = FacebookIdentity(
            uid=self.facebook_uid,
            username=self.username,
            name=self.name,
            profile_url=self.profile_url,
        )
        if not identity.is_usable:
            raise ValueError("contact lookup requires a Facebook identity")
        return identity


@dataclass(frozen=True, slots=True)
class ContactLookupResult:
    event_id: int
    state: LookupOutcome
    source: LookupSource
    user: FacebookIdentity
    phone: str = ""
    observed_at: datetime | None = None
    provider_called: bool = False
    quota_charged: bool = False
    monthly_used: int = 0
    monthly_limit: int = 0
    safe_error_code: str = ""


def _profile_source_url(
    canonical: FacebookIdentity,
    requested: FacebookIdentity,
) -> str:
    if canonical.profile_url or requested.profile_url:
        return canonical.profile_url or requested.profile_url
    uid = canonical.uid or requested.uid
    if uid:
        return f"https://www.facebook.com/{uid}"
    username = canonical.username or requested.username
    return f"https://www.facebook.com/{username}" if username else ""


class EntitlementsPort(Protocol):
    def for_account(self, account_id: int, now: datetime): ...


class QuotaPort(Protocol):
    def precheck(self, account_id: int, user_id: int, now: datetime): ...

    def reserve(
        self,
        account_id: int,
        user_id: int,
        phone_id: int,
        event_id: int | None,
        now: datetime,
    ): ...


class ContactLookupService:
    SAFE_ERROR_CODES = frozenset(
        {
            "contact_enrichment_failed",
            "contact_result_unavailable",
            "provider_authentication_failed",
            "provider_failed",
            "provider_identity_conflict",
            "provider_identity_insufficient",
            "provider_invalid_json",
            "provider_rate_limited",
            "provider_transport_error",
        }
    )

    def __init__(
        self,
        entitlements: EntitlementsPort,
        quota: QuotaPort,
        contacts,
        pipeline,
        *,
        lease_ttl: timedelta = timedelta(seconds=30),
        wait_timeout_seconds: float = 2.0,
        manual_refresh_interval: timedelta = timedelta(hours=24),
        found_ttl: timedelta = timedelta(days=30),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.entitlements = entitlements
        self.quota = quota
        self.contacts = contacts
        self.pipeline = pipeline
        self.lease_ttl = lease_ttl
        self.wait_timeout_seconds = wait_timeout_seconds
        self.manual_refresh_interval = manual_refresh_interval
        self.found_ttl = found_ttl
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _require_access(account: Account, device: Device) -> None:
        if (
            account.status is not AccountStatus.ACTIVE
            or account.email_verified_at is None
            or device.account_id != account.id
            or device.status is not DeviceStatus.ACTIVE
        ):
            raise PermissionError("contact lookup access denied")

    def _is_fresh_found(self, cached: CachedContact, now: datetime) -> bool:
        return bool(
            cached.found
            and cached.observed_at is not None
            and (
                cached.state is None
                or cached.state.latest_status is not ProviderStatus.NOT_FOUND
            )
            and now < cached.observed_at + self.found_ttl
        )

    @classmethod
    def _safe_error(cls, error_code: str) -> str:
        cleaned = error_code.strip()
        if cleaned in {"provider_http_401", "provider_http_403"}:
            return "provider_authentication_failed"
        return cleaned if cleaned in cls.SAFE_ERROR_CODES else "provider_failed"

    @staticmethod
    def _refresh_after(result: ProviderResult) -> datetime:
        checked_at = result.checked_at
        if result.status is ProviderStatus.FOUND:
            return checked_at + timedelta(days=30)
        if result.status is ProviderStatus.NOT_FOUND:
            return checked_at + timedelta(days=7)
        if result.status is ProviderStatus.RATE_LIMITED:
            if result.retry_after is not None and result.retry_after >= checked_at:
                return result.retry_after
            return checked_at + timedelta(minutes=15)
        if result.error_code in {"provider_http_401", "provider_http_403"}:
            return checked_at + timedelta(days=1)
        return checked_at + timedelta(minutes=15)

    @staticmethod
    def _is_fresh_negative(cached: CachedContact, now: datetime) -> bool:
        return bool(
            cached.state is not None
            and cached.state.latest_status is ProviderStatus.NOT_FOUND
            and now < cached.state.refresh_after
        )

    @staticmethod
    def _is_fresh_failure(cached: CachedContact, now: datetime) -> bool:
        return bool(
            cached.state is not None
            and cached.state.latest_status
            in {ProviderStatus.FAILED, ProviderStatus.RATE_LIMITED}
            and now < cached.state.refresh_after
        )

    def _finish_not_found(
        self,
        account_id: int,
        event: LookupEvent,
        contact: ContactIdentity,
        now: datetime,
        *,
        source: LookupSource,
        provider_called: bool,
        used: int,
        limit: int,
    ) -> ContactLookupResult:
        self.contacts.complete_lookup_event(
            account_id,
            event.id,
            LookupOutcome.NOT_FOUND,
            source,
            provider_called=provider_called,
            quota_charged=False,
            safe_error_code="",
            now=now,
        )
        return self._result(
            event,
            contact,
            state=LookupOutcome.NOT_FOUND,
            source=source,
            provider_called=provider_called,
            monthly_used=used,
            monthly_limit=limit,
        )

    def _finish_failed(
        self,
        account_id: int,
        event: LookupEvent,
        contact: ContactIdentity,
        now: datetime,
        *,
        error_code: str,
        provider_called: bool,
        used: int,
        limit: int,
    ) -> ContactLookupResult:
        safe_error = self._safe_error(error_code)
        self.contacts.complete_lookup_event(
            account_id,
            event.id,
            LookupOutcome.FAILED,
            LookupSource.NONE,
            provider_called=provider_called,
            quota_charged=False,
            safe_error_code=safe_error,
            now=now,
        )
        return self._result(
            event,
            contact,
            state=LookupOutcome.FAILED,
            source=LookupSource.NONE,
            provider_called=provider_called,
            monthly_used=used,
            monthly_limit=limit,
            safe_error_code=safe_error,
        )

    def _result(
        self,
        event: LookupEvent,
        contact: ContactIdentity,
        *,
        state: LookupOutcome,
        source: LookupSource,
        phone: str = "",
        observed_at: datetime | None = None,
        provider_called: bool = False,
        quota_charged: bool = False,
        monthly_used: int = 0,
        monthly_limit: int = 0,
        safe_error_code: str = "",
    ) -> ContactLookupResult:
        return ContactLookupResult(
            event_id=event.id,
            state=state,
            source=source,
            user=contact.identity,
            phone=phone,
            observed_at=observed_at,
            provider_called=provider_called,
            quota_charged=quota_charged,
            monthly_used=monthly_used,
            monthly_limit=monthly_limit,
            safe_error_code=safe_error_code,
        )

    def _finish_found(
        self,
        account_id: int,
        event: LookupEvent,
        contact: ContactIdentity,
        cached: CachedContact,
        now: datetime,
        *,
        source: LookupSource,
        provider_called: bool,
    ) -> ContactLookupResult:
        if cached.phone_number_id is None:
            raise ValueError("found contact is missing a phone id")
        decision = self.quota.reserve(
            account_id,
            contact.id,
            cached.phone_number_id,
            event.id,
            now,
        )
        if not decision.allowed:
            self.contacts.complete_lookup_event(
                account_id,
                event.id,
                LookupOutcome.QUOTA_EXCEEDED,
                LookupSource.NONE,
                provider_called=provider_called,
                quota_charged=False,
                safe_error_code="contact_quota_exhausted",
                now=now,
            )
            return self._result(
                event,
                contact,
                state=LookupOutcome.QUOTA_EXCEEDED,
                source=LookupSource.NONE,
                provider_called=provider_called,
                monthly_used=decision.used,
                monthly_limit=decision.limit,
                safe_error_code="contact_quota_exhausted",
            )
        self.contacts.complete_lookup_event(
            account_id,
            event.id,
            LookupOutcome.FOUND,
            source,
            provider_called=provider_called,
            quota_charged=decision.charged,
            safe_error_code="",
            now=now,
            revealed_phone_number_id=cached.phone_number_id,
            revealed_observed_at=cached.observed_at,
        )
        return self._result(
            event,
            contact,
            state=LookupOutcome.FOUND,
            source=source,
            phone=cached.phone,
            observed_at=cached.observed_at,
            provider_called=provider_called,
            quota_charged=decision.charged,
            monthly_used=decision.used,
            monthly_limit=decision.limit,
        )

    def lookup(
        self,
        account: Account,
        device: Device,
        request: ContactLookupRequest,
        now: datetime,
        force_refresh: bool = False,
        scan_context: LookupScanContext | None = None,
    ) -> ContactLookupResult:
        self._require_access(account, device)
        self.entitlements.for_account(account.id, now)
        requested = request.identity
        contact = self.contacts.resolve_identity(requested)
        resolved_scan_context = scan_context or LookupScanContext(
            source_url=_profile_source_url(contact.identity, requested)
        )
        event = self.contacts.create_lookup_event(
            account.id,
            device.id,
            contact,
            requested,
            now,
            resolved_scan_context,
        )
        precheck = self.quota.precheck(account.id, contact.id, now)
        if not precheck.allowed:
            self.contacts.complete_lookup_event(
                account.id,
                event.id,
                LookupOutcome.QUOTA_EXCEEDED,
                LookupSource.NONE,
                provider_called=False,
                quota_charged=False,
                safe_error_code="contact_quota_exhausted",
                now=now,
            )
            return self._result(
                event,
                contact,
                state=LookupOutcome.QUOTA_EXCEEDED,
                source=LookupSource.NONE,
                monthly_used=precheck.used,
                monthly_limit=precheck.limit,
                safe_error_code="contact_quota_exhausted",
            )

        cached = self.contacts.get_cached_contact(contact.id)
        refresh_limited = bool(
            force_refresh
            and cached.state is not None
            and now
            < cached.state.checked_at + self.manual_refresh_interval
        )
        use_fresh_cache = not force_refresh or refresh_limited
        if use_fresh_cache and self._is_fresh_found(cached, now):
            return self._finish_found(
                account.id,
                event,
                contact,
                cached,
                now,
                source=LookupSource.CACHE,
                provider_called=False,
            )
        if use_fresh_cache and self._is_fresh_negative(cached, now):
            return self._finish_not_found(
                account.id,
                event,
                contact,
                now,
                source=LookupSource.NEGATIVE_CACHE,
                provider_called=False,
                used=precheck.used,
                limit=precheck.limit,
            )
        if self._is_fresh_failure(cached, now):
            return self._finish_failed(
                account.id,
                event,
                contact,
                now,
                error_code=(
                    "provider_rate_limited"
                    if cached.state.latest_status is ProviderStatus.RATE_LIMITED
                    else "provider_failed"
                ),
                provider_called=False,
                used=precheck.used,
                limit=precheck.limit,
            )

        owner = uuid4().hex
        lease = self.contacts.claim_lease(
            contact.id,
            "fbnumber",
            "phone",
            owner,
            now,
            now + self.lease_ttl,
        )
        if not lease.acquired:
            updated = self.contacts.wait_for_state(
                contact.id,
                "fbnumber",
                "phone",
                event.created_at,
                timeout_seconds=self.wait_timeout_seconds,
            )
            if updated is None:
                return self._result(
                    event,
                    contact,
                    state=LookupOutcome.PROCESSING,
                    source=LookupSource.NONE,
                    monthly_used=precheck.used,
                    monthly_limit=precheck.limit,
                )
            cached = self.contacts.get_cached_contact(contact.id)
            if self._is_fresh_found(cached, now):
                return self._finish_found(
                    account.id,
                    event,
                    contact,
                    cached,
                    now,
                    source=LookupSource.CACHE,
                    provider_called=False,
                )
            if self._is_fresh_negative(cached, now):
                return self._finish_not_found(
                    account.id,
                    event,
                    contact,
                    now,
                    source=LookupSource.NEGATIVE_CACHE,
                    provider_called=False,
                    used=precheck.used,
                    limit=precheck.limit,
                )
            if self._is_fresh_failure(cached, now):
                return self._finish_failed(
                    account.id,
                    event,
                    contact,
                    now,
                    error_code=(
                        "provider_rate_limited"
                        if cached.state.latest_status
                        is ProviderStatus.RATE_LIMITED
                        else "provider_failed"
                    ),
                    provider_called=False,
                    used=precheck.used,
                    limit=precheck.limit,
                )
            return self._result(
                event,
                contact,
                state=LookupOutcome.PROCESSING,
                source=LookupSource.NONE,
                monthly_used=precheck.used,
                monthly_limit=precheck.limit,
            )

        try:
            try:
                run = self.pipeline.run_bundles(
                    (UserBundle(identity=contact.identity),)
                )
            except Exception:
                return self._finish_failed(
                    account.id,
                    event,
                    contact,
                    now,
                    error_code="contact_enrichment_failed",
                    provider_called=True,
                    used=precheck.used,
                    limit=precheck.limit,
                )
            if len(run.users) != 1:
                return self._finish_failed(
                    account.id,
                    event,
                    contact,
                    now,
                    error_code="contact_enrichment_failed",
                    provider_called=True,
                    used=precheck.used,
                    limit=precheck.limit,
                )
            enriched = run.users[0]
            status = enriched.provider_result.status
            if (
                status is ProviderStatus.FOUND
                and enriched.bundle.phone_1 is None
            ):
                status = ProviderStatus.NOT_FOUND
                enriched = replace(
                    enriched,
                    provider_result=replace(
                        enriched.provider_result,
                        status=ProviderStatus.NOT_FOUND,
                    ),
                )
            refresh_after = self._refresh_after(enriched.provider_result)
            finalized = self.contacts.finalize_enrichment(
                contact.id,
                "fbnumber",
                "phone",
                owner,
                enriched,
                refresh_after,
            )
            if finalized is None:
                return self._result(
                    event,
                    contact,
                    state=LookupOutcome.PROCESSING,
                    source=LookupSource.NONE,
                    provider_called=True,
                    monthly_used=precheck.used,
                    monthly_limit=precheck.limit,
                )
            cached = self.contacts.get_cached_contact(contact.id)
            if status is ProviderStatus.FOUND and self._is_fresh_found(
                cached, now
            ):
                return self._finish_found(
                    account.id,
                    event,
                    contact,
                    cached,
                    now,
                    source=LookupSource.PROVIDER,
                    provider_called=True,
                )
            if status is ProviderStatus.NOT_FOUND:
                return self._finish_not_found(
                    account.id,
                    event,
                    contact,
                    now,
                    source=LookupSource.PROVIDER,
                    provider_called=True,
                    used=precheck.used,
                    limit=precheck.limit,
                )
            return self._finish_failed(
                account.id,
                event,
                contact,
                now,
                error_code=self._safe_error(
                    enriched.provider_result.error_code
                ),
                provider_called=True,
                used=precheck.used,
                limit=precheck.limit,
            )
        except DatabaseIdentityConflict:
            return self._finish_failed(
                account.id,
                event,
                contact,
                now,
                error_code="provider_identity_conflict",
                provider_called=True,
                used=precheck.used,
                limit=precheck.limit,
            )
        finally:
            self.contacts.release_lease(
                contact.id, "fbnumber", "phone", owner
            )

    def get_event(
        self,
        account_id: int,
        event_id: int,
    ) -> ContactLookupResult | None:
        event = self.contacts.get_lookup_event(account_id, event_id)
        if event is None:
            return None
        contact = self.contacts.get_identity(event.facebook_user_id)
        now = self.clock()
        quota = self.quota.precheck(
            account_id, event.facebook_user_id, now
        )
        cached = self.contacts.get_cached_contact(event.facebook_user_id)
        if event.outcome is not LookupOutcome.PROCESSING:
            if event.outcome is LookupOutcome.FOUND:
                if (
                    event.revealed_phone_number_id is None
                    or not event.revealed_phone
                ):
                    return self._result(
                        event,
                        contact,
                        state=LookupOutcome.FAILED,
                        source=LookupSource.NONE,
                        provider_called=event.provider_called,
                        monthly_used=quota.used,
                        monthly_limit=quota.limit,
                        safe_error_code="contact_result_unavailable",
                    )
                decision = self.quota.reserve(
                    account_id,
                    event.facebook_user_id,
                    event.revealed_phone_number_id,
                    event.id,
                    now,
                )
                if not decision.allowed:
                    return self._result(
                        event,
                        contact,
                        state=LookupOutcome.QUOTA_EXCEEDED,
                        source=LookupSource.NONE,
                        provider_called=event.provider_called,
                        monthly_used=decision.used,
                        monthly_limit=decision.limit,
                        safe_error_code="contact_quota_exhausted",
                    )
                return self._result(
                    event,
                    contact,
                    state=LookupOutcome.FOUND,
                    source=event.result_source,
                    phone=event.revealed_phone,
                    observed_at=event.revealed_observed_at,
                    provider_called=event.provider_called,
                    quota_charged=decision.charged,
                    monthly_used=decision.used,
                    monthly_limit=decision.limit,
                )
            return self._result(
                event,
                contact,
                state=event.outcome,
                source=event.result_source,
                provider_called=event.provider_called,
                quota_charged=event.quota_charged,
                monthly_used=quota.used,
                monthly_limit=quota.limit,
                safe_error_code=event.safe_error_code,
            )
        if (
            cached.state is None
            or cached.state.updated_at <= event.created_at
        ):
            return self._result(
                event,
                contact,
                state=LookupOutcome.PROCESSING,
                source=LookupSource.NONE,
                monthly_used=quota.used,
                monthly_limit=quota.limit,
            )

        if self._is_fresh_found(cached, now):
            return self._finish_found(
                account_id,
                event,
                contact,
                cached,
                now,
                source=LookupSource.CACHE,
                provider_called=False,
            )
        if self._is_fresh_negative(cached, now):
            return self._finish_not_found(
                account_id,
                event,
                contact,
                now,
                source=LookupSource.NEGATIVE_CACHE,
                provider_called=False,
                used=quota.used,
                limit=quota.limit,
            )
        if cached.state.latest_status in {
            ProviderStatus.FAILED,
            ProviderStatus.RATE_LIMITED,
        }:
            return self._finish_failed(
                account_id,
                event,
                contact,
                now,
                error_code=(
                    "provider_rate_limited"
                    if cached.state.latest_status is ProviderStatus.RATE_LIMITED
                    else "provider_failed"
                ),
                provider_called=False,
                used=quota.used,
                limit=quota.limit,
            )
        return self._result(
            event,
            contact,
            state=LookupOutcome.PROCESSING,
            source=LookupSource.NONE,
            monthly_used=quota.used,
            monthly_limit=quota.limit,
        )
