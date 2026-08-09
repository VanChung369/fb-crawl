from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import psycopg

from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    PhoneSlot,
    ProfileData,
    ProviderResult,
    ProviderStatus,
    RetryCandidate,
)
from fb_data_pipeline.repositories.errors import (
    DatabaseError,
    DatabaseIdentityConflict,
)

if TYPE_CHECKING:
    from fb_data_pipeline.services.pipeline import EnrichedUser


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _origin(evidence: PhoneEvidence) -> str:
    if evidence.slot is PhoneSlot.PHONE_1:
        return "fbnumber"
    return "fb_crawl"


FB_NUMBER_RETRY_LOCK_NAME = "fb-crawl:fbnumber-durable-retry-worker:v1"


class PostgresRepository:
    def __init__(
        self,
        database_url: str,
        *,
        statement_timeout_seconds: float = 5.0,
        connect_factory=psycopg.connect,
        clock: Callable[[], datetime] = _now_utc,
    ) -> None:
        self.database_url = database_url
        self.statement_timeout_ms = max(
            1,
            round(statement_timeout_seconds * 1000),
        )
        self.connect_factory = connect_factory
        self.clock = clock

    @contextmanager
    def fbnumber_retry_lock(self) -> Iterator[bool]:
        connection = None
        cursor = None
        acquired = False
        try:
            connection = self.connect_factory(self.database_url)
            connection.autocommit = True
            cursor = connection.cursor()
            cursor.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                (FB_NUMBER_RETRY_LOCK_NAME,),
            )
            row = cursor.fetchone()
            if row is None:
                raise DatabaseError("Database retry lock failed.")
            acquired = bool(row[0])
        except DatabaseError:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
            raise
        except (psycopg.Error, OSError) as error:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
            raise DatabaseError("Database operation failed.") from error

        try:
            yield acquired
        except BaseException:
            try:
                if acquired:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                        (FB_NUMBER_RETRY_LOCK_NAME,),
                    )
            except (psycopg.Error, OSError):
                pass
            finally:
                cursor.close()
                connection.close()
            raise
        else:
            try:
                if acquired:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                        (FB_NUMBER_RETRY_LOCK_NAME,),
                    )
            except (psycopg.Error, OSError) as error:
                raise DatabaseError("Database operation failed.") from error
            finally:
                cursor.close()
                connection.close()

    def list_fbnumber_retry_candidates(
        self,
        *,
        eligible_before: datetime | None,
        limit: int,
    ) -> tuple[RetryCandidate, ...]:
        if limit <= 0:
            raise ValueError("retry candidate limit must be positive")

        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{self.statement_timeout_ms}ms",),
                    )
                    cursor.execute(
                        """
                        WITH latest AS (
                            SELECT DISTINCT ON (attempts.facebook_user_id)
                                attempts.id AS attempt_id,
                                attempts.facebook_user_id,
                                attempts.status,
                                attempts.checked_at,
                                attempts.error_code
                            FROM enrichment_attempts AS attempts
                            WHERE attempts.provider = %s
                            ORDER BY
                                attempts.facebook_user_id,
                                attempts.checked_at DESC,
                                attempts.id DESC
                        )
                        SELECT
                            users.id,
                            users.facebook_uid,
                            users.facebook_username,
                            users.display_name,
                            users.profile_url,
                            latest.status,
                            latest.checked_at,
                            latest.error_code
                        FROM latest
                        JOIN facebook_users AS users
                            ON users.id = latest.facebook_user_id
                        WHERE latest.status IN ('failed', 'rate_limited')
                          AND (
                              NULLIF(btrim(users.facebook_uid), '') IS NOT NULL
                              OR NULLIF(
                                  btrim(users.facebook_username),
                                  ''
                              ) IS NOT NULL
                          )
                          AND (
                              CAST(%s AS timestamptz) IS NULL
                              OR latest.checked_at
                                  <= CAST(%s AS timestamptz)
                          )
                        ORDER BY
                            latest.checked_at ASC,
                            latest.attempt_id ASC,
                            users.id ASC
                        LIMIT %s
                        """,
                        (
                            "fbnumber",
                            eligible_before,
                            eligible_before,
                            limit,
                        ),
                    )
                    rows = cursor.fetchall()
        except DatabaseError:
            raise
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error

        return tuple(
            RetryCandidate(
                user_id=int(row[0]),
                identity=FacebookIdentity(
                    uid=row[1],
                    username=row[2],
                    name=row[3],
                    profile_url=row[4],
                ),
                status=ProviderStatus(str(row[5])),
                checked_at=row[6],
                error_code=row[7],
            )
            for row in rows
        )

    def save_enriched_user(self, enriched: EnrichedUser) -> int:
        try:
            return self._save_enriched_user(enriched)
        except DatabaseError:
            raise
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error

    def _save_enriched_user(self, enriched: EnrichedUser) -> int:
        if not enriched.bundle.identity.is_usable:
            raise DatabaseIdentityConflict(
                "Cannot persist a Facebook user without an identity alias."
            )

        with self.connect_factory(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{self.statement_timeout_ms}ms",),
                )
                user_id = self._upsert_user(
                    cursor,
                    enriched.bundle.identity,
                )
                self._upsert_profile(
                    cursor,
                    user_id,
                    enriched.bundle.profile,
                )
                self._upsert_evidence(
                    cursor,
                    user_id,
                    enriched.bundle.evidence,
                )
                self._insert_attempt(
                    cursor,
                    user_id,
                    enriched.provider_result,
                )
        return user_id

    @staticmethod
    def _identity_values(
        identity: FacebookIdentity,
    ) -> tuple[str | None, str | None, str | None, str | None, str | None]:
        return (
            identity.uid or None,
            identity.username or None,
            identity.username.casefold() or None,
            identity.name or None,
            identity.profile_url or None,
        )

    @staticmethod
    def _lock_aliases(cursor, identity: FacebookIdentity) -> None:
        for alias in sorted(set(identity.aliases)):
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (alias,),
            )

    @staticmethod
    def _matching_user_ids(
        cursor,
        values: tuple[
            str | None,
            str | None,
            str | None,
            str | None,
            str | None,
        ],
    ) -> tuple[int, ...]:
        uid, _username, normalized_username, _name, profile_url = values
        cursor.execute(
            """
            SELECT id
            FROM facebook_users
            WHERE facebook_uid = %s
               OR normalized_username = %s
               OR profile_url = %s
            FOR UPDATE
            """,
            (
                uid,
                normalized_username,
                profile_url,
            ),
        )
        return tuple(sorted({int(row[0]) for row in cursor.fetchall()}))

    def _upsert_user(self, cursor, identity: FacebookIdentity) -> int:
        values = self._identity_values(identity)
        self._lock_aliases(cursor, identity)
        matches = self._matching_user_ids(cursor, values)
        if len(matches) > 1:
            raise DatabaseIdentityConflict(
                "Facebook identity aliases match multiple database users."
            )

        uid, username, normalized_username, name, profile_url = values
        if matches:
            cursor.execute(
                """
                UPDATE facebook_users
                SET facebook_uid = COALESCE(%s, facebook_uid),
                    facebook_username = COALESCE(%s, facebook_username),
                    normalized_username = COALESCE(
                        %s,
                        normalized_username
                    ),
                    display_name = COALESCE(%s, display_name),
                    profile_url = COALESCE(%s, profile_url),
                    updated_at = now()
                WHERE id = %s
                RETURNING id
                """,
                (
                    uid,
                    username,
                    normalized_username,
                    name,
                    profile_url,
                    matches[0],
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise DatabaseError("Database user update failed.")
            return int(row[0])

        cursor.execute(
            """
            INSERT INTO facebook_users (
                facebook_uid,
                facebook_username,
                normalized_username,
                display_name,
                profile_url
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (uid, username, normalized_username, name, profile_url),
        )
        row = cursor.fetchone()
        if row is not None:
            return int(row[0])

        matches = self._matching_user_ids(cursor, values)
        if len(matches) != 1:
            raise DatabaseIdentityConflict(
                "Facebook identity could not be resolved safely."
            )
        return matches[0]

    @staticmethod
    def _upsert_profile(
        cursor,
        user_id: int,
        profile: ProfileData,
    ) -> None:
        if profile.is_empty:
            return

        cursor.execute(
            """
            INSERT INTO facebook_user_profiles (
                facebook_user_id,
                address,
                birth_date,
                gender,
                source_url,
                observed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (facebook_user_id) DO UPDATE
            SET address = CASE
                    WHEN EXCLUDED.address IS NULL
                    THEN facebook_user_profiles.address
                    WHEN facebook_user_profiles.address IS NULL
                    THEN EXCLUDED.address
                    WHEN EXCLUDED.observed_at IS NOT NULL
                        AND (
                            facebook_user_profiles.observed_at IS NULL
                            OR EXCLUDED.observed_at >=
                                facebook_user_profiles.observed_at
                        )
                    THEN EXCLUDED.address
                    ELSE facebook_user_profiles.address
                END,
                birth_date = CASE
                    WHEN EXCLUDED.birth_date IS NULL
                    THEN facebook_user_profiles.birth_date
                    WHEN facebook_user_profiles.birth_date IS NULL
                    THEN EXCLUDED.birth_date
                    WHEN EXCLUDED.observed_at IS NOT NULL
                        AND (
                            facebook_user_profiles.observed_at IS NULL
                            OR EXCLUDED.observed_at >=
                                facebook_user_profiles.observed_at
                        )
                    THEN EXCLUDED.birth_date
                    ELSE facebook_user_profiles.birth_date
                END,
                gender = CASE
                    WHEN EXCLUDED.gender IS NULL
                    THEN facebook_user_profiles.gender
                    WHEN facebook_user_profiles.gender IS NULL
                    THEN EXCLUDED.gender
                    WHEN EXCLUDED.observed_at IS NOT NULL
                        AND (
                            facebook_user_profiles.observed_at IS NULL
                            OR EXCLUDED.observed_at >=
                                facebook_user_profiles.observed_at
                        )
                    THEN EXCLUDED.gender
                    ELSE facebook_user_profiles.gender
                END,
                source_url = COALESCE(
                    NULLIF(EXCLUDED.source_url, ''),
                    facebook_user_profiles.source_url
                ),
                observed_at = CASE
                    WHEN facebook_user_profiles.observed_at IS NULL
                    THEN EXCLUDED.observed_at
                    WHEN EXCLUDED.observed_at IS NULL
                    THEN facebook_user_profiles.observed_at
                    ELSE GREATEST(
                        facebook_user_profiles.observed_at,
                        EXCLUDED.observed_at
                    )
                END,
                updated_at = now()
            WHERE (
                EXCLUDED.address IS NOT NULL
                AND (
                    facebook_user_profiles.address IS NULL
                    OR (
                        EXCLUDED.observed_at IS NOT NULL
                        AND (
                            facebook_user_profiles.observed_at IS NULL
                            OR EXCLUDED.observed_at >=
                                facebook_user_profiles.observed_at
                        )
                    )
                )
            ) OR (
                EXCLUDED.birth_date IS NOT NULL
                AND (
                    facebook_user_profiles.birth_date IS NULL
                    OR (
                        EXCLUDED.observed_at IS NOT NULL
                        AND (
                            facebook_user_profiles.observed_at IS NULL
                            OR EXCLUDED.observed_at >=
                                facebook_user_profiles.observed_at
                        )
                    )
                )
            ) OR (
                EXCLUDED.gender IS NOT NULL
                AND (
                    facebook_user_profiles.gender IS NULL
                    OR (
                        EXCLUDED.observed_at IS NOT NULL
                        AND (
                            facebook_user_profiles.observed_at IS NULL
                            OR EXCLUDED.observed_at >=
                                facebook_user_profiles.observed_at
                        )
                    )
                )
            )
            """,
            (
                user_id,
                profile.address or None,
                profile.birth_date or None,
                profile.gender or None,
                profile.source_url,
                profile.observed_at,
            ),
        )

    @staticmethod
    def _upsert_phone(cursor, evidence: PhoneEvidence) -> int:
        cursor.execute(
            """
            INSERT INTO phone_numbers (normalized_phone, display_phone)
            VALUES (%s, %s)
            ON CONFLICT (normalized_phone) DO UPDATE
            SET display_phone = phone_numbers.display_phone
            RETURNING id
            """,
            (evidence.normalized_phone, evidence.phone_number),
        )
        row = cursor.fetchone()
        if row is None:
            raise DatabaseError("Database phone upsert failed.")
        return int(row[0])

    def _upsert_evidence(
        self,
        cursor,
        user_id: int,
        evidence_items: tuple[PhoneEvidence, ...],
    ) -> None:
        ordered = sorted(
            evidence_items,
            key=lambda evidence: (
                evidence.normalized_phone,
                _origin(evidence),
                evidence.source,
                evidence.source_url,
                evidence.provider,
            ),
        )
        for evidence in ordered:
            phone_id = self._upsert_phone(cursor, evidence)
            captured_at = evidence.captured_at or self.clock()
            cursor.execute(
                """
                INSERT INTO user_phone_evidence (
                    facebook_user_id,
                    phone_number_id,
                    origin,
                    source,
                    source_url,
                    provider,
                    correlation_id,
                    confidence,
                    first_captured_at,
                    last_captured_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (
                    facebook_user_id,
                    phone_number_id,
                    origin,
                    source,
                    source_url,
                    provider
                ) DO UPDATE
                SET correlation_id = CASE
                        WHEN EXCLUDED.last_captured_at >=
                            user_phone_evidence.last_captured_at
                        THEN EXCLUDED.correlation_id
                        ELSE user_phone_evidence.correlation_id
                    END,
                    confidence = CASE
                        WHEN user_phone_evidence.origin = 'fb_crawl' THEN
                            CASE
                                WHEN CASE EXCLUDED.confidence
                                    WHEN 'profile_field' THEN 1
                                    WHEN 'strong_pattern' THEN 2
                                    WHEN 'weak_pattern' THEN 3
                                    ELSE 4
                                END < CASE user_phone_evidence.confidence
                                    WHEN 'profile_field' THEN 1
                                    WHEN 'strong_pattern' THEN 2
                                    WHEN 'weak_pattern' THEN 3
                                    ELSE 4
                                END
                                THEN EXCLUDED.confidence
                                ELSE user_phone_evidence.confidence
                            END
                        WHEN EXCLUDED.last_captured_at >=
                            user_phone_evidence.last_captured_at
                        THEN EXCLUDED.confidence
                        ELSE user_phone_evidence.confidence
                    END,
                    first_captured_at = LEAST(
                        user_phone_evidence.first_captured_at,
                        EXCLUDED.first_captured_at
                    ),
                    last_captured_at = GREATEST(
                        user_phone_evidence.last_captured_at,
                        EXCLUDED.last_captured_at
                    ),
                    evidence_count =
                        user_phone_evidence.evidence_count + 1,
                    updated_at = now()
                """,
                (
                    user_id,
                    phone_id,
                    _origin(evidence),
                    evidence.source,
                    evidence.source_url,
                    evidence.provider,
                    evidence.correlation_id,
                    evidence.confidence,
                    captured_at,
                    captured_at,
                ),
            )

    @staticmethod
    def _insert_attempt(
        cursor,
        user_id: int,
        provider_result: ProviderResult,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO enrichment_attempts (
                facebook_user_id,
                provider,
                status,
                checked_at,
                correlation_id,
                error_code,
                values_found
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                provider_result.provider,
                provider_result.status.value,
                provider_result.checked_at,
                provider_result.correlation_id,
                provider_result.error_code,
                len(provider_result.evidence),
            ),
        )
