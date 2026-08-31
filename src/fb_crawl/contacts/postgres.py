from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime

import psycopg

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
from fb_data_pipeline.repositories.errors import DatabaseError
from fb_data_pipeline.repositories.errors import DatabaseIdentityConflict
from fb_data_pipeline.repositories.postgres import PostgresRepository
from fb_data_pipeline.services.pipeline import EnrichedUser


class PostgresContactRepository:
    def __init__(
        self,
        database_url: str,
        *,
        statement_timeout_seconds: float = 5.0,
        connect_factory=psycopg.connect,
        identity_repository: PostgresRepository | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.database_url = database_url
        self.statement_timeout_ms = max(
            1, round(statement_timeout_seconds * 1000)
        )
        self.connect_factory = connect_factory
        self.identity_repository = identity_repository or PostgresRepository(
            database_url,
            statement_timeout_seconds=statement_timeout_seconds,
            connect_factory=connect_factory,
        )
        self.sleeper = sleeper
        self.monotonic = monotonic

    def _set_timeout(self, cursor) -> None:
        cursor.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (f"{self.statement_timeout_ms}ms",),
        )

    def resolve_identity(self, identity: FacebookIdentity) -> ContactIdentity:
        user_id = self.identity_repository.upsert_identity(identity)
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    self._set_timeout(cursor)
                    cursor.execute(
                        """
                        SELECT facebook_uid, facebook_username, display_name,
                               profile_url
                        FROM facebook_users
                        WHERE id = %s
                        """,
                        (user_id,),
                    )
                    row = cursor.fetchone()
        except DatabaseError:
            raise
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error
        if row is None:
            raise DatabaseError("Database identity resolution failed.")
        return ContactIdentity(
            id=user_id,
            identity=FacebookIdentity(
                uid=row[0],
                username=row[1],
                name=row[2],
                profile_url=row[3],
            ),
        )

    @staticmethod
    def _state_from_row(
        facebook_user_id: int,
        provider: str,
        field: str,
        row: tuple[object, ...],
        offset: int = 0,
    ) -> LookupState | None:
        if row[offset] is None:
            return None
        return LookupState(
            facebook_user_id=facebook_user_id,
            provider=provider,
            field=field,
            latest_status=ProviderStatus(str(row[offset])),
            checked_at=row[offset + 1],  # type: ignore[arg-type]
            refresh_after=row[offset + 2],  # type: ignore[arg-type]
            latest_attempt_id=(
                int(row[offset + 3]) if row[offset + 3] is not None else None
            ),
            updated_at=row[offset + 4],  # type: ignore[arg-type]
        )

    def get_cached_contact(
        self,
        facebook_user_id: int,
        provider: str = "fbnumber",
        field: str = "phone",
    ) -> CachedContact | None:
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    self._set_timeout(cursor)
                    cursor.execute(
                        """
                        SELECT users.id,
                               cached.phone_number_id,
                               cached.normalized_phone,
                               cached.observed_at,
                               state.latest_status,
                               state.checked_at,
                               state.refresh_after,
                               state.latest_attempt_id,
                               state.updated_at
                        FROM facebook_users AS users
                        LEFT JOIN LATERAL (
                            SELECT evidence.phone_number_id,
                                   numbers.normalized_phone,
                                   evidence.last_captured_at AS observed_at
                            FROM user_phone_evidence AS evidence
                            JOIN phone_numbers AS numbers
                              ON numbers.id = evidence.phone_number_id
                            WHERE evidence.facebook_user_id = users.id
                              AND evidence.origin = 'fbnumber'
                            ORDER BY evidence.last_captured_at DESC,
                                     evidence.id DESC
                            LIMIT 1
                        ) AS cached ON true
                        LEFT JOIN provider_lookup_state AS state
                          ON state.facebook_user_id = users.id
                         AND state.provider = %s
                         AND state.field = %s
                        WHERE users.id = %s
                        """,
                        (provider, field, facebook_user_id),
                    )
                    row = cursor.fetchone()
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error
        if row is None:
            return None
        return CachedContact(
            facebook_user_id=int(row[0]),
            phone_number_id=(int(row[1]) if row[1] is not None else None),
            phone=str(row[2] or ""),
            observed_at=row[3],  # type: ignore[arg-type]
            state=self._state_from_row(
                facebook_user_id, provider, field, row, offset=4
            ),
        )

    @staticmethod
    def _event_from_row(row: tuple[object, ...]) -> LookupEvent:
        return LookupEvent(
            id=int(row[0]),
            account_id=int(row[1]),
            device_id=int(row[2]) if row[2] is not None else None,
            facebook_user_id=int(row[3]),
            requested_uid=str(row[4] or ""),
            requested_username=str(row[5] or ""),
            requested_profile_url=str(row[6] or ""),
            outcome=LookupOutcome(str(row[7])),
            result_source=LookupSource(str(row[8])),
            provider_called=bool(row[9]),
            quota_charged=bool(row[10]),
            safe_error_code=str(row[11] or ""),
            created_at=row[12],  # type: ignore[arg-type]
            completed_at=row[13],  # type: ignore[arg-type]
        )

    @staticmethod
    def _event_columns() -> str:
        return """
            id, account_id, device_id, facebook_user_id,
            requested_uid, requested_username, requested_profile_url,
            outcome, result_source, provider_called, quota_charged,
            safe_error_code, created_at, completed_at
        """

    def create_lookup_event(
        self,
        account_id: int,
        device_id: int | None,
        contact: ContactIdentity,
        requested: FacebookIdentity,
        now: datetime,
    ) -> LookupEvent:
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    self._set_timeout(cursor)
                    cursor.execute(
                        f"""
                        INSERT INTO lookup_events (
                            account_id, device_id, facebook_user_id,
                            requested_uid, requested_username,
                            requested_profile_url, created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING {self._event_columns()}
                        """,
                        (
                            account_id,
                            device_id,
                            contact.id,
                            requested.uid or None,
                            requested.username or None,
                            requested.profile_url or None,
                            now,
                        ),
                    )
                    row = cursor.fetchone()
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error
        if row is None:
            raise DatabaseError("Database lookup event creation failed.")
        return self._event_from_row(row)

    def get_lookup_event(
        self,
        account_id: int,
        event_id: int,
    ) -> LookupEvent | None:
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    self._set_timeout(cursor)
                    cursor.execute(
                        f"""
                        SELECT {self._event_columns()}
                        FROM lookup_events
                        WHERE id = %s AND account_id = %s
                        """,
                        (event_id, account_id),
                    )
                    row = cursor.fetchone()
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error
        return self._event_from_row(row) if row is not None else None

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
    ) -> LookupEvent | None:
        if outcome is LookupOutcome.PROCESSING:
            raise ValueError("completed lookup outcome cannot be processing")
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    self._set_timeout(cursor)
                    cursor.execute(
                        f"""
                        UPDATE lookup_events
                        SET outcome = %s,
                            result_source = %s,
                            provider_called = %s,
                            quota_charged = %s,
                            safe_error_code = NULLIF(%s, ''),
                            completed_at = %s
                        WHERE id = %s
                          AND account_id = %s
                          AND outcome = 'processing'
                        RETURNING {self._event_columns()}
                        """,
                        (
                            outcome.value,
                            source.value,
                            provider_called,
                            quota_charged,
                            safe_error_code.strip(),
                            now,
                            event_id,
                            account_id,
                        ),
                    )
                    row = cursor.fetchone()
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error
        return self._event_from_row(row) if row is not None else None

    def claim_lease(
        self,
        facebook_user_id: int,
        provider: str,
        field: str,
        owner_token: str,
        now: datetime,
        leased_until: datetime,
    ) -> EnrichmentLease:
        if leased_until <= now:
            raise ValueError("lease expiry must be after claim time")
        if not owner_token.strip():
            raise ValueError("lease owner token is required")
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    self._set_timeout(cursor)
                    cursor.execute(
                        """
                        INSERT INTO enrichment_leases (
                            facebook_user_id, provider, field,
                            owner_token, leased_until
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (facebook_user_id, provider, field) DO UPDATE
                        SET owner_token = EXCLUDED.owner_token,
                            leased_until = EXCLUDED.leased_until,
                            updated_at = now()
                        WHERE enrichment_leases.leased_until <= %s
                        RETURNING owner_token, leased_until
                        """,
                        (
                            facebook_user_id,
                            provider,
                            field,
                            owner_token,
                            leased_until,
                            now,
                        ),
                    )
                    row = cursor.fetchone()
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error
        return EnrichmentLease(
            facebook_user_id=facebook_user_id,
            provider=provider,
            field=field,
            owner_token=owner_token,
            leased_until=(row[1] if row is not None else leased_until),  # type: ignore[arg-type]
            acquired=bool(row is not None and str(row[0]) == owner_token),
        )

    def release_lease(
        self,
        facebook_user_id: int,
        provider: str,
        field: str,
        owner_token: str,
    ) -> bool:
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    self._set_timeout(cursor)
                    cursor.execute(
                        """
                        DELETE FROM enrichment_leases
                        WHERE facebook_user_id = %s
                          AND provider = %s
                          AND field = %s
                          AND owner_token = %s
                        RETURNING 1
                        """,
                        (facebook_user_id, provider, field, owner_token),
                    )
                    row = cursor.fetchone()
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error
        return row is not None

    def _get_state(
        self,
        facebook_user_id: int,
        provider: str,
        field: str,
    ) -> LookupState | None:
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    self._set_timeout(cursor)
                    cursor.execute(
                        """
                        SELECT latest_status, checked_at, refresh_after,
                               latest_attempt_id, updated_at
                        FROM provider_lookup_state
                        WHERE facebook_user_id = %s
                          AND provider = %s
                          AND field = %s
                        """,
                        (facebook_user_id, provider, field),
                    )
                    row = cursor.fetchone()
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error
        if row is None:
            return None
        return self._state_from_row(facebook_user_id, provider, field, row)

    def wait_for_state(
        self,
        facebook_user_id: int,
        provider: str,
        field: str,
        after: datetime,
        *,
        timeout_seconds: float = 2.0,
        poll_interval_seconds: float = 0.1,
    ) -> LookupState | None:
        if timeout_seconds < 0 or poll_interval_seconds <= 0:
            raise ValueError("invalid state wait timing")
        deadline = self.monotonic() + timeout_seconds
        while True:
            state = self._get_state(facebook_user_id, provider, field)
            if state is not None and state.checked_at > after:
                return state
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                return None
            self.sleeper(min(poll_interval_seconds, remaining))

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
    ) -> LookupState | None:
        if refresh_after < checked_at:
            raise ValueError("provider state refresh cannot precede check")
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    self._set_timeout(cursor)
                    cursor.execute(
                        """
                        SELECT facebook_user_id
                        FROM enrichment_leases AS leases
                        WHERE leases.facebook_user_id = %s
                          AND leases.provider = %s
                          AND leases.field = %s
                          AND leases.owner_token = %s
                          AND leases.leased_until > statement_timestamp()
                        FOR UPDATE
                        """,
                        (
                            facebook_user_id,
                            provider,
                            field,
                            owner_token,
                        ),
                    )
                    if cursor.fetchone() is None:
                        return None
                    cursor.execute(
                        """
                        INSERT INTO provider_lookup_state (
                            facebook_user_id, provider, field, latest_status,
                            checked_at, refresh_after, latest_attempt_id,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (facebook_user_id, provider, field) DO UPDATE
                        SET latest_status = EXCLUDED.latest_status,
                            checked_at = EXCLUDED.checked_at,
                            refresh_after = EXCLUDED.refresh_after,
                            latest_attempt_id = EXCLUDED.latest_attempt_id,
                            updated_at = EXCLUDED.updated_at
                        WHERE provider_lookup_state.checked_at
                              <= EXCLUDED.checked_at
                        RETURNING latest_status, checked_at, refresh_after,
                                  latest_attempt_id, updated_at
                        """,
                        (
                            facebook_user_id,
                            provider,
                            field,
                            status.value,
                            checked_at,
                            refresh_after,
                            latest_attempt_id,
                            checked_at,
                        ),
                    )
                    row = cursor.fetchone()
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error
        if row is None:
            return None
        state = self._state_from_row(facebook_user_id, provider, field, row)
        return state

    def finalize_enrichment(
        self,
        facebook_user_id: int,
        provider: str,
        field: str,
        owner_token: str,
        enriched: EnrichedUser,
        refresh_after: datetime,
    ) -> LookupState | None:
        checked_at = enriched.provider_result.checked_at
        if refresh_after < checked_at:
            raise ValueError("provider state refresh cannot precede check")
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    self._set_timeout(cursor)
                    cursor.execute(
                        """
                        SELECT facebook_user_id
                        FROM enrichment_leases
                        WHERE facebook_user_id = %s
                          AND provider = %s
                          AND field = %s
                          AND owner_token = %s
                          AND leased_until > statement_timestamp()
                        FOR UPDATE
                        """,
                        (
                            facebook_user_id,
                            provider,
                            field,
                            owner_token,
                        ),
                    )
                    if cursor.fetchone() is None:
                        return None

                    persisted_user_id, attempt_id = (
                        self.identity_repository.persist_enriched_user(
                            cursor, enriched
                        )
                    )
                    if persisted_user_id != facebook_user_id:
                        raise DatabaseIdentityConflict(
                            "Enrichment identity changed during finalization."
                        )

                    cursor.execute(
                        """
                        INSERT INTO provider_lookup_state (
                            facebook_user_id, provider, field, latest_status,
                            checked_at, refresh_after, latest_attempt_id,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (facebook_user_id, provider, field) DO UPDATE
                        SET latest_status = EXCLUDED.latest_status,
                            checked_at = EXCLUDED.checked_at,
                            refresh_after = EXCLUDED.refresh_after,
                            latest_attempt_id = EXCLUDED.latest_attempt_id,
                            updated_at = EXCLUDED.updated_at
                        WHERE provider_lookup_state.checked_at
                              <= EXCLUDED.checked_at
                        RETURNING latest_status, checked_at, refresh_after,
                                  latest_attempt_id, updated_at
                        """,
                        (
                            facebook_user_id,
                            provider,
                            field,
                            enriched.provider_result.status.value,
                            checked_at,
                            refresh_after,
                            attempt_id,
                            checked_at,
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise DatabaseError(
                            "Database provider state finalization failed."
                        )
                    cursor.execute(
                        """
                        DELETE FROM enrichment_leases
                        WHERE facebook_user_id = %s
                          AND provider = %s
                          AND field = %s
                          AND owner_token = %s
                        RETURNING 1
                        """,
                        (
                            facebook_user_id,
                            provider,
                            field,
                            owner_token,
                        ),
                    )
                    if cursor.fetchone() is None:
                        raise DatabaseError(
                            "Database enrichment lease release failed."
                        )
        except DatabaseError:
            raise
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error
        return self._state_from_row(
            facebook_user_id, provider, field, row
        )
