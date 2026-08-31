from __future__ import annotations

from datetime import datetime
from typing import Protocol

from fb_crawl.contacts.models import (
    CachedContact,
    ContactIdentity,
    EnrichmentLease,
    LookupEvent,
    LookupOutcome,
    LookupSource,
    LookupState,
)
from fb_data_pipeline.core.models import FacebookIdentity, ProviderStatus
from fb_data_pipeline.services.pipeline import EnrichedUser


class ContactRepository(Protocol):
    def resolve_identity(self, identity: FacebookIdentity) -> ContactIdentity: ...

    def get_cached_contact(
        self,
        facebook_user_id: int,
        provider: str = "fbnumber",
        field: str = "phone",
    ) -> CachedContact | None: ...

    def create_lookup_event(
        self,
        account_id: int,
        device_id: int | None,
        contact: ContactIdentity,
        requested: FacebookIdentity,
        now: datetime,
    ) -> LookupEvent: ...

    def get_lookup_event(
        self,
        account_id: int,
        event_id: int,
    ) -> LookupEvent | None: ...

    def complete_lookup_event(
        self,
        account_id: int,
        event_id: int,
        outcome: LookupOutcome,
        source: LookupSource,
        *,
        provider_called: bool,
        quota_charged: bool,
        safe_error_code: str,
        now: datetime,
    ) -> LookupEvent | None: ...

    def claim_lease(
        self,
        facebook_user_id: int,
        provider: str,
        field: str,
        owner_token: str,
        now: datetime,
        leased_until: datetime,
    ) -> EnrichmentLease: ...

    def release_lease(
        self,
        facebook_user_id: int,
        provider: str,
        field: str,
        owner_token: str,
    ) -> bool: ...

    def wait_for_state(
        self,
        facebook_user_id: int,
        provider: str,
        field: str,
        after: datetime,
        *,
        timeout_seconds: float = 2.0,
        poll_interval_seconds: float = 0.1,
    ) -> LookupState | None: ...

    def update_provider_state(
        self,
        facebook_user_id: int,
        provider: str,
        field: str,
        status: ProviderStatus,
        checked_at: datetime,
        refresh_after: datetime,
        latest_attempt_id: int | None = None,
        *,
        owner_token: str,
    ) -> LookupState | None: ...

    def finalize_enrichment(
        self,
        facebook_user_id: int,
        provider: str,
        field: str,
        owner_token: str,
        enriched: EnrichedUser,
        refresh_after: datetime,
    ) -> LookupState | None: ...
