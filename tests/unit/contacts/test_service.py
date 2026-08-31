from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from fb_crawl.accounts.models import (
    Account,
    AccountRole,
    AccountStatus,
    Device,
    DeviceStatus,
)
from fb_crawl.contacts.models import (
    CachedContact,
    ContactIdentity,
    EnrichmentLease,
    LookupEvent,
    LookupOutcome,
    LookupSource,
    LookupState,
)
from fb_crawl.contacts.service import (
    ContactLookupRequest,
    ContactLookupService,
)
from fb_crawl.entitlements.models import Entitlements
from fb_crawl.entitlements.quota import QuotaPrecheck, RevealDecision
from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    ProviderResult,
    ProviderStatus,
    UserBundle,
)
from fb_data_pipeline.services.pipeline import EnrichedUser
from fb_data_pipeline.repositories.errors import DatabaseIdentityConflict


NOW = datetime(2026, 8, 31, 3, tzinfo=UTC)
IDENTITY = FacebookIdentity(uid="100123", username="sample.user")
CONTACT = ContactIdentity(41, IDENTITY)
ACCOUNT = Account(
    id=5,
    normalized_email="owner@example.test",
    display_email="owner@example.test",
    password_hash="hash",
    role=AccountRole.USER,
    status=AccountStatus.ACTIVE,
    email_verified_at=NOW,
    created_at=NOW,
    updated_at=NOW,
)
DEVICE = Device(
    id=7,
    account_id=ACCOUNT.id,
    installation_id=UUID("12345678-1234-5678-1234-567812345678"),
    display_name="Chrome",
    status=DeviceStatus.ACTIVE,
    first_seen_at=NOW,
    last_seen_at=NOW,
)
REQUEST = ContactLookupRequest(
    facebook_uid="100123",
    username="sample.user",
)


def state(status: ProviderStatus, refresh_after: datetime) -> LookupState:
    return LookupState(
        facebook_user_id=CONTACT.id,
        provider="fbnumber",
        field="phone",
        latest_status=status,
        checked_at=NOW - timedelta(minutes=1),
        refresh_after=refresh_after,
        latest_attempt_id=91,
        updated_at=NOW - timedelta(minutes=1),
    )


def event() -> LookupEvent:
    return LookupEvent(
        id=71,
        account_id=ACCOUNT.id,
        device_id=DEVICE.id,
        facebook_user_id=CONTACT.id,
        requested_uid=IDENTITY.uid,
        requested_username=IDENTITY.username,
        requested_profile_url="",
        outcome=LookupOutcome.PROCESSING,
        result_source=LookupSource.NONE,
        provider_called=False,
        quota_charged=False,
        safe_error_code="",
        created_at=NOW,
        completed_at=None,
    )


class FakeEntitlements:
    def for_account(self, account_id: int, now: datetime) -> Entitlements:
        assert (account_id, now) == (ACCOUNT.id, NOW)
        return Entitlements(100, 1, False, False)


class FakeQuota:
    def __init__(self) -> None:
        self.precheck_result = QuotaPrecheck(True, False, 11, 100)
        self.reserve_result = RevealDecision(True, True, 81, 12, 100)
        self.reserve_calls: list[tuple[object, ...]] = []

    def precheck(self, account_id: int, user_id: int, now: datetime):
        return self.precheck_result

    def reserve(
        self,
        account_id: int,
        user_id: int,
        phone_id: int,
        event_id: int | None,
        now: datetime,
    ):
        self.reserve_calls.append(
            (account_id, user_id, phone_id, event_id, now)
        )
        return self.reserve_result


class FakeContacts:
    def __init__(self, cached: CachedContact) -> None:
        self.cached = cached
        self.created = event()
        self.lease_acquired = True
        self.wait_result: LookupState | None = None
        self.wait_cache: CachedContact | None = None
        self.final_state: LookupState | None = None
        self.final_cache: CachedContact | None = None
        self.release_calls: list[tuple[object, ...]] = []
        self.finalized_enriched: EnrichedUser | None = None
        self.finalized_refresh_after: datetime | None = None
        self.finalize_error: Exception | None = None
        self.canonical = CONTACT
        self.complete_calls: list[tuple[object, ...]] = []

    def resolve_identity(self, identity: FacebookIdentity) -> ContactIdentity:
        assert identity == IDENTITY
        return CONTACT

    def create_lookup_event(self, *_args: object) -> LookupEvent:
        return self.created

    def get_cached_contact(self, *_args: object) -> CachedContact:
        return self.cached

    def get_identity(self, *_args: object) -> ContactIdentity:
        return self.canonical

    def get_lookup_event(
        self, account_id: int, event_id: int
    ) -> LookupEvent | None:
        if (account_id, event_id) != (self.created.account_id, self.created.id):
            return None
        return self.created

    def claim_lease(
        self,
        user_id: int,
        provider: str,
        field: str,
        owner: str,
        now: datetime,
        expires: datetime,
    ) -> EnrichmentLease:
        return EnrichmentLease(
            user_id, provider, field, owner, expires, self.lease_acquired
        )

    def wait_for_state(self, *_args: object, **_kwargs: object):
        if self.wait_result is not None and self.wait_cache is not None:
            self.cached = self.wait_cache
        return self.wait_result

    def complete_lookup_event(
        self,
        account_id: int,
        event_id: int,
        outcome: LookupOutcome,
        source: LookupSource,
        **kwargs: object,
    ) -> LookupEvent:
        self.complete_calls.append((account_id, event_id, outcome, source, kwargs))
        completed = replace(
            self.created,
            outcome=outcome,
            result_source=source,
            provider_called=bool(kwargs["provider_called"]),
            quota_charged=bool(kwargs["quota_charged"]),
            safe_error_code=str(kwargs["safe_error_code"]),
            completed_at=kwargs["now"],
            revealed_phone_number_id=kwargs.get("revealed_phone_number_id"),
            revealed_phone=(
                self.cached.phone
                if kwargs.get("revealed_phone_number_id")
                == self.cached.phone_number_id
                else ""
            ),
            revealed_observed_at=kwargs.get("revealed_observed_at"),
        )
        self.created = completed
        return completed

    def finalize_enrichment(self, *args: object) -> LookupState | None:
        if self.finalize_error is not None:
            raise self.finalize_error
        self.finalized_enriched = args[4]  # type: ignore[assignment]
        self.finalized_refresh_after = args[5]  # type: ignore[assignment]
        if self.final_state is not None and self.final_cache is not None:
            self.cached = self.final_cache
        return self.final_state

    def release_lease(self, *args: object) -> bool:
        self.release_calls.append(args)
        return True


class FakePipeline:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.error: Exception | None = None
        self.users_override: tuple[EnrichedUser, ...] | None = None
        self.provider_result = ProviderResult(
            provider="fbnumber",
            status=ProviderStatus.NOT_FOUND,
            checked_at=NOW,
        )

    def run_bundles(self, bundles: object):
        self.calls.append(bundles)
        if self.error is not None:
            raise self.error
        users = (
                EnrichedUser(
                    bundle=UserBundle(
                        identity=IDENTITY,
                        evidence=self.provider_result.evidence,
                    ),
                    provider_result=self.provider_result,
                ),
            )
        return SimpleNamespace(
            users=(self.users_override if self.users_override is not None else users)
        )


def service(cached: CachedContact):
    contacts = FakeContacts(cached)
    quota = FakeQuota()
    pipeline = FakePipeline()
    return (
        ContactLookupService(
            FakeEntitlements(),
            quota,
            contacts,
            pipeline,
            clock=lambda: NOW,
        ),
        contacts,
        quota,
        pipeline,
    )


def test_fresh_cache_never_calls_provider() -> None:
    cached = CachedContact(
        CONTACT.id,
        501,
        "+84981234567",
        NOW - timedelta(minutes=1),
        state(ProviderStatus.FOUND, NOW + timedelta(days=30)),
    )
    lookup, _contacts, quota, pipeline = service(cached)

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.FOUND
    assert result.source is LookupSource.CACHE
    assert result.phone == "+84981234567"
    assert result.quota_charged is True
    assert len(quota.reserve_calls) == 1
    assert pipeline.calls == []


def test_non_owner_returns_processing_after_bounded_wait() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, quota, pipeline = service(cached)
    contacts.lease_acquired = False
    contacts.wait_result = None

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.PROCESSING
    assert result.event_id == 71
    assert result.phone == ""
    assert quota.reserve_calls == []
    assert pipeline.calls == []


def test_quota_precheck_blocks_provider_before_cache_or_lease() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, quota, pipeline = service(cached)
    quota.precheck_result = QuotaPrecheck(False, False, 100, 100)

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.QUOTA_EXCEEDED
    assert result.safe_error_code == "contact_quota_exhausted"
    assert result.monthly_used == 100
    assert pipeline.calls == []
    assert contacts.complete_calls[0][2] is LookupOutcome.QUOTA_EXCEEDED


def test_fresh_negative_cache_never_calls_provider_or_quota_reserve() -> None:
    cached = CachedContact(
        CONTACT.id,
        None,
        "",
        None,
        state(ProviderStatus.NOT_FOUND, NOW + timedelta(days=7)),
    )
    lookup, _contacts, quota, pipeline = service(cached)

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.NOT_FOUND
    assert result.source is LookupSource.NEGATIVE_CACHE
    assert result.quota_charged is False
    assert quota.reserve_calls == []
    assert pipeline.calls == []


def test_lease_owner_finalizes_provider_phone_before_revealing_it() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, quota, pipeline = service(cached)
    evidence = PhoneEvidence(
        phone_number="0981234567",
        normalized_phone="+84981234567",
        source="external:fbnumber",
        captured_at=NOW,
        provider="fbnumber",
    )
    pipeline.provider_result = ProviderResult(
        provider="fbnumber",
        status=ProviderStatus.FOUND,
        evidence=(evidence,),
        checked_at=NOW,
    )
    contacts.final_state = state(
        ProviderStatus.FOUND, NOW + timedelta(days=30)
    )
    contacts.final_cache = CachedContact(
        CONTACT.id,
        501,
        "+84981234567",
        NOW,
        contacts.final_state,
    )

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.FOUND
    assert result.source is LookupSource.PROVIDER
    assert result.provider_called is True
    assert result.phone == "+84981234567"
    assert len(pipeline.calls) == 1
    assert len(quota.reserve_calls) == 1


def test_owner_that_loses_lease_after_provider_call_returns_processing() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, quota, pipeline = service(cached)
    contacts.final_state = None

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.PROCESSING
    assert result.provider_called is True
    assert result.phone == ""
    assert quota.reserve_calls == []
    assert len(pipeline.calls) == 1


def test_manual_refresh_inside_minimum_interval_reuses_cache() -> None:
    cached_state = replace(
        state(ProviderStatus.FOUND, NOW + timedelta(days=30)),
        checked_at=NOW - timedelta(hours=1),
    )
    cached = CachedContact(
        CONTACT.id,
        501,
        "+84981234567",
        NOW - timedelta(hours=1),
        cached_state,
    )
    lookup, _contacts, _quota, pipeline = service(cached)

    result = lookup.lookup(
        ACCOUNT, DEVICE, REQUEST, NOW, force_refresh=True
    )

    assert result.state is LookupOutcome.FOUND
    assert result.source is LookupSource.CACHE
    assert pipeline.calls == []


def test_provider_failure_returns_only_safe_error_code_and_releases_lease() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, _quota, pipeline = service(cached)
    pipeline.provider_result = ProviderResult(
        provider="fbnumber",
        status=ProviderStatus.FAILED,
        checked_at=NOW,
        error_code="provider_transport_error",
    )
    contacts.final_state = state(
        ProviderStatus.FAILED, NOW + timedelta(minutes=15)
    )

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.FAILED
    assert result.safe_error_code == "provider_transport_error"
    assert result.phone == ""
    assert len(contacts.release_calls) == 1


def test_final_quota_race_withholds_provider_phone() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, quota, pipeline = service(cached)
    evidence = PhoneEvidence(
        phone_number="0981234567",
        normalized_phone="+84981234567",
        source="external:fbnumber",
        captured_at=NOW,
        provider="fbnumber",
    )
    pipeline.provider_result = ProviderResult(
        provider="fbnumber",
        status=ProviderStatus.FOUND,
        evidence=(evidence,),
        checked_at=NOW,
    )
    contacts.final_state = state(
        ProviderStatus.FOUND, NOW + timedelta(days=30)
    )
    contacts.final_cache = CachedContact(
        CONTACT.id,
        501,
        "+84981234567",
        NOW,
        contacts.final_state,
    )
    quota.reserve_result = RevealDecision(False, False, None, 100, 100)

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.QUOTA_EXCEEDED
    assert result.phone == ""
    assert result.provider_called is True
    assert result.monthly_used == 100


def test_polling_processing_event_returns_current_processing_state() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, _contacts, _quota, pipeline = service(cached)

    result = lookup.get_event(ACCOUNT.id, 71)

    assert result is not None
    assert result.state is LookupOutcome.PROCESSING
    assert result.event_id == 71
    assert pipeline.calls == []


def test_polling_event_is_not_visible_to_another_account() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, _contacts, _quota, _pipeline = service(cached)

    assert lookup.get_event(999, 71) is None


def test_polling_completes_event_when_other_owner_published_phone() -> None:
    published_state = replace(
        state(ProviderStatus.FOUND, NOW + timedelta(days=30)),
        checked_at=NOW + timedelta(seconds=1),
        updated_at=NOW + timedelta(seconds=1),
    )
    cached = CachedContact(
        CONTACT.id,
        501,
        "+84981234567",
        NOW + timedelta(seconds=1),
        published_state,
    )
    lookup, contacts, quota, _pipeline = service(cached)

    result = lookup.get_event(ACCOUNT.id, 71)

    assert result is not None
    assert result.state is LookupOutcome.FOUND
    assert result.source is LookupSource.CACHE
    assert result.phone == "+84981234567"
    assert len(quota.reserve_calls) == 1
    assert contacts.complete_calls[0][2] is LookupOutcome.FOUND


def test_unexpected_pipeline_failure_closes_event_and_releases_lease() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, _quota, pipeline = service(cached)
    pipeline.error = RuntimeError("secret internal failure")

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.FAILED
    assert result.safe_error_code == "contact_enrichment_failed"
    assert "secret" not in result.safe_error_code
    assert contacts.complete_calls[0][2] is LookupOutcome.FAILED
    assert len(contacts.release_calls) == 1


def test_force_refresh_does_not_bypass_current_provider_rate_limit() -> None:
    limited_state = replace(
        state(ProviderStatus.RATE_LIMITED, NOW + timedelta(minutes=15)),
        checked_at=NOW - timedelta(minutes=1),
    )
    cached = CachedContact(CONTACT.id, None, "", None, limited_state)
    lookup, _contacts, _quota, pipeline = service(cached)

    result = lookup.lookup(
        ACCOUNT, DEVICE, REQUEST, NOW, force_refresh=True
    )

    assert result.state is LookupOutcome.FAILED
    assert result.safe_error_code == "provider_rate_limited"
    assert pipeline.calls == []


def test_provider_identity_without_phone_is_negative_for_phone_field() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, quota, pipeline = service(cached)
    pipeline.provider_result = ProviderResult(
        provider="fbnumber",
        status=ProviderStatus.FOUND,
        checked_at=NOW,
        resolved_identity=IDENTITY,
        name="Sample User",
    )
    contacts.final_state = state(
        ProviderStatus.NOT_FOUND, NOW + timedelta(days=7)
    )
    contacts.final_cache = CachedContact(
        CONTACT.id, None, "", None, contacts.final_state
    )

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.NOT_FOUND
    assert result.source is LookupSource.PROVIDER
    assert quota.reserve_calls == []
    assert contacts.finalized_enriched is not None
    assert (
        contacts.finalized_enriched.provider_result.status
        is ProviderStatus.NOT_FOUND
    )


def test_poll_accepts_state_published_after_event_even_if_provider_started_before() -> None:
    published_state = replace(
        state(ProviderStatus.FOUND, NOW + timedelta(days=30)),
        checked_at=NOW - timedelta(seconds=5),
        updated_at=NOW + timedelta(seconds=1),
    )
    cached = CachedContact(
        CONTACT.id,
        501,
        "+84981234567",
        NOW + timedelta(seconds=1),
        published_state,
    )
    lookup, _contacts, _quota, _pipeline = service(cached)

    result = lookup.get_event(ACCOUNT.id, 71)

    assert result is not None
    assert result.state is LookupOutcome.FOUND
    assert result.phone == "+84981234567"


def test_non_owner_reuses_published_rate_limit_instead_of_processing() -> None:
    limited = replace(
        state(ProviderStatus.RATE_LIMITED, NOW + timedelta(minutes=15)),
        checked_at=NOW - timedelta(seconds=5),
        updated_at=NOW + timedelta(seconds=1),
    )
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, _quota, pipeline = service(cached)
    contacts.lease_acquired = False
    contacts.wait_result = limited
    contacts.wait_cache = CachedContact(CONTACT.id, None, "", None, limited)

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.FAILED
    assert result.safe_error_code == "provider_rate_limited"
    assert pipeline.calls == []


def test_malformed_pipeline_result_terminalizes_event_safely() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, _quota, pipeline = service(cached)
    pipeline.users_override = ()

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.FAILED
    assert result.safe_error_code == "contact_enrichment_failed"
    assert contacts.created.outcome is LookupOutcome.FAILED
    assert len(contacts.release_calls) == 1


def test_recent_phone_evidence_survives_a_later_provider_failure() -> None:
    failed_state = replace(
        state(ProviderStatus.FAILED, NOW + timedelta(minutes=15)),
        checked_at=NOW - timedelta(minutes=1),
        updated_at=NOW - timedelta(minutes=1),
    )
    cached = CachedContact(
        CONTACT.id,
        501,
        "+84981234567",
        NOW - timedelta(days=1),
        failed_state,
    )
    lookup, _contacts, _quota, pipeline = service(cached)

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.FOUND
    assert result.source is LookupSource.CACHE
    assert result.phone == "+84981234567"
    assert pipeline.calls == []


def test_definitive_negative_invalidates_recent_phone_evidence() -> None:
    negative_state = replace(
        state(ProviderStatus.NOT_FOUND, NOW + timedelta(days=7)),
        checked_at=NOW - timedelta(minutes=1),
        updated_at=NOW - timedelta(minutes=1),
    )
    cached = CachedContact(
        CONTACT.id,
        501,
        "+84981234567",
        NOW - timedelta(days=1),
        negative_state,
    )
    lookup, _contacts, quota, pipeline = service(cached)

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.NOT_FOUND
    assert result.source is LookupSource.NEGATIVE_CACHE
    assert quota.reserve_calls == []
    assert pipeline.calls == []


def test_terminal_found_poll_uses_event_phone_and_current_month_quota() -> None:
    cached = CachedContact(
        CONTACT.id,
        999,
        "+84999999999",
        NOW,
        state(ProviderStatus.FOUND, NOW + timedelta(days=30)),
    )
    lookup, contacts, quota, _pipeline = service(cached)
    contacts.created = replace(
        event(),
        outcome=LookupOutcome.FOUND,
        result_source=LookupSource.PROVIDER,
        completed_at=NOW - timedelta(days=31),
        revealed_phone_number_id=501,
        revealed_phone="+84981234567",
        revealed_observed_at=NOW - timedelta(days=31),
    )

    result = lookup.get_event(ACCOUNT.id, 71)

    assert result is not None
    assert result.phone == "+84981234567"
    assert result.monthly_used == 12
    assert result.monthly_limit == 100
    assert quota.reserve_calls == [(ACCOUNT.id, CONTACT.id, 501, 71, NOW)]
    assert contacts.complete_calls == []


def test_terminal_found_poll_withholds_event_phone_when_new_month_is_full() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, quota, _pipeline = service(cached)
    contacts.created = replace(
        event(),
        outcome=LookupOutcome.FOUND,
        result_source=LookupSource.PROVIDER,
        completed_at=NOW - timedelta(days=31),
        revealed_phone_number_id=501,
        revealed_phone="+84981234567",
        revealed_observed_at=NOW - timedelta(days=31),
    )
    quota.reserve_result = RevealDecision(False, False, None, 100, 100)

    result = lookup.get_event(ACCOUNT.id, 71)

    assert result is not None
    assert result.state is LookupOutcome.QUOTA_EXCEEDED
    assert result.phone == ""
    assert result.monthly_used == 100
    assert result.safe_error_code == "contact_quota_exhausted"


def test_terminal_failure_poll_reports_current_quota_metadata() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, _quota, _pipeline = service(cached)
    contacts.created = replace(
        event(),
        outcome=LookupOutcome.FAILED,
        safe_error_code="provider_failed",
        completed_at=NOW,
    )

    result = lookup.get_event(ACCOUNT.id, 71)

    assert result is not None
    assert result.monthly_used == 11
    assert result.monthly_limit == 100


def test_provider_auth_failure_is_mapped_and_cached_for_a_day() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, _quota, pipeline = service(cached)
    pipeline.provider_result = ProviderResult(
        provider="fbnumber",
        status=ProviderStatus.FAILED,
        checked_at=NOW,
        error_code="provider_http_401",
    )
    contacts.final_state = state(
        ProviderStatus.FAILED, NOW + timedelta(days=1)
    )

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.safe_error_code == "provider_authentication_failed"
    assert contacts.finalized_refresh_after == NOW + timedelta(days=1)


def test_provider_rate_limit_uses_retry_after_for_cache_expiry() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, _quota, pipeline = service(cached)
    retry_after = NOW + timedelta(hours=2)
    pipeline.provider_result = ProviderResult(
        provider="fbnumber",
        status=ProviderStatus.RATE_LIMITED,
        checked_at=NOW,
        error_code="provider_rate_limited",
        retry_after=retry_after,
    )
    contacts.final_state = state(ProviderStatus.RATE_LIMITED, retry_after)

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.safe_error_code == "provider_rate_limited"
    assert contacts.finalized_refresh_after == retry_after


def test_unknown_provider_error_is_not_exposed_to_client() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, _quota, pipeline = service(cached)
    pipeline.provider_result = ProviderResult(
        provider="fbnumber",
        status=ProviderStatus.FAILED,
        checked_at=NOW,
        error_code="secret_vendor_internal_state",
    )
    contacts.final_state = state(
        ProviderStatus.FAILED, NOW + timedelta(minutes=15)
    )

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.safe_error_code == "provider_failed"
    assert "secret" not in result.safe_error_code


def test_provider_alias_conflict_terminalizes_created_event() -> None:
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, _quota, _pipeline = service(cached)
    contacts.finalize_error = DatabaseIdentityConflict("private conflict")

    result = lookup.lookup(ACCOUNT, DEVICE, REQUEST, NOW)

    assert result.state is LookupOutcome.FAILED
    assert result.safe_error_code == "provider_identity_conflict"
    assert contacts.created.outcome is LookupOutcome.FAILED
    assert len(contacts.release_calls) == 1


def test_poll_returns_canonical_identity_enriched_after_event_creation() -> None:
    canonical = ContactIdentity(
        CONTACT.id,
        FacebookIdentity(
            uid="100123",
            username="sample.user",
            name="Canonical Name",
            profile_url="https://www.facebook.com/sample.user",
        ),
    )
    cached = CachedContact(CONTACT.id, None, "", None, None)
    lookup, contacts, _quota, _pipeline = service(cached)
    contacts.canonical = canonical
    contacts.created = replace(
        event(), outcome=LookupOutcome.NOT_FOUND, completed_at=NOW
    )

    result = lookup.get_event(ACCOUNT.id, 71)

    assert result is not None
    assert result.user == canonical.identity
