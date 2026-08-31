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
        self.final_state: LookupState | None = None
        self.final_cache: CachedContact | None = None
        self.release_calls: list[tuple[object, ...]] = []
        self.finalized_enriched: EnrichedUser | None = None
        self.complete_calls: list[tuple[object, ...]] = []

    def resolve_identity(self, identity: FacebookIdentity) -> ContactIdentity:
        assert identity == IDENTITY
        return CONTACT

    def create_lookup_event(self, *_args: object) -> LookupEvent:
        return self.created

    def get_cached_contact(self, *_args: object) -> CachedContact:
        return self.cached

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
        return replace(
            self.created,
            outcome=outcome,
            result_source=source,
            provider_called=bool(kwargs["provider_called"]),
            quota_charged=bool(kwargs["quota_charged"]),
            safe_error_code=str(kwargs["safe_error_code"]),
            completed_at=kwargs["now"],
        )

    def finalize_enrichment(self, *args: object) -> LookupState | None:
        self.finalized_enriched = args[4]  # type: ignore[assignment]
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
        self.provider_result = ProviderResult(
            provider="fbnumber",
            status=ProviderStatus.NOT_FOUND,
            checked_at=NOW,
        )

    def run_bundles(self, bundles: object):
        self.calls.append(bundles)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            users=(
                EnrichedUser(
                    bundle=UserBundle(
                        identity=IDENTITY,
                        evidence=self.provider_result.evidence,
                    ),
                    provider_result=self.provider_result,
                ),
            )
        )


def service(cached: CachedContact):
    contacts = FakeContacts(cached)
    quota = FakeQuota()
    pipeline = FakePipeline()
    return (
        ContactLookupService(FakeEntitlements(), quota, contacts, pipeline),
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
