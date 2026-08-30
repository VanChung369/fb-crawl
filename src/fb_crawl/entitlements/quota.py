from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import psycopg

from fb_crawl.entitlements.time import quota_period
from fb_data_pipeline.repositories.errors import DatabaseError


@dataclass(frozen=True, slots=True)
class QuotaPrecheck:
    allowed: bool
    already_revealed: bool
    used: int
    limit: int


@dataclass(frozen=True, slots=True)
class RevealDecision:
    allowed: bool
    charged: bool
    reveal_id: int | None
    used: int
    limit: int


class ContactQuotaRepository(Protocol):
    def precheck(
        self,
        account_id: int,
        facebook_user_id: int,
        period_start: date,
        now: datetime,
    ) -> QuotaPrecheck: ...

    def reserve(
        self,
        account_id: int,
        facebook_user_id: int,
        phone_number_id: int,
        lookup_event_id: int | None,
        period_start: date,
        now: datetime,
    ) -> RevealDecision: ...


class ContactQuotaService:
    def __init__(
        self, repository: ContactQuotaRepository, timezone: ZoneInfo
    ) -> None:
        self.repository = repository
        self.timezone = timezone

    def precheck(
        self, account_id: int, facebook_user_id: int, now: datetime
    ) -> QuotaPrecheck:
        period_start = quota_period(now, self.timezone)
        return self.repository.precheck(
            account_id, facebook_user_id, period_start, now
        )

    def reserve(
        self,
        account_id: int,
        facebook_user_id: int,
        phone_number_id: int,
        lookup_event_id: int | None,
        now: datetime,
    ) -> RevealDecision:
        period_start = quota_period(now, self.timezone)
        return self.repository.reserve(
            account_id,
            facebook_user_id,
            phone_number_id,
            lookup_event_id,
            period_start,
            now,
        )


class PostgresContactQuotaRepository:
    def __init__(
        self,
        database_url: str,
        *,
        statement_timeout_seconds: float = 5.0,
        connect_factory=psycopg.connect,
    ) -> None:
        self.database_url = database_url
        self.statement_timeout_ms = max(1, round(statement_timeout_seconds * 1000))
        self.connect_factory = connect_factory

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (f"{self.statement_timeout_ms}ms",),
                    )
                    yield cursor
        except DatabaseError:
            raise
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error

    def precheck(
        self,
        account_id: int,
        facebook_user_id: int,
        period_start: date,
        now: datetime,
    ) -> QuotaPrecheck:
        with self._connect() as cursor:
            limit, used = self._lock_usage(cursor, account_id, period_start, now)
            reveal_id = self._find_reveal(
                cursor, account_id, facebook_user_id, period_start
            )
        already_revealed = reveal_id is not None
        return QuotaPrecheck(
            allowed=already_revealed or used < limit,
            already_revealed=already_revealed,
            used=used,
            limit=limit,
        )

    def reserve(
        self,
        account_id: int,
        facebook_user_id: int,
        phone_number_id: int,
        lookup_event_id: int | None,
        period_start: date,
        now: datetime,
    ) -> RevealDecision:
        with self._connect() as cursor:
            limit, used = self._lock_usage(cursor, account_id, period_start, now)
            reveal_id = self._find_reveal(
                cursor, account_id, facebook_user_id, period_start
            )
            if reveal_id is not None:
                return RevealDecision(True, False, reveal_id, used, limit)
            if used >= limit:
                return RevealDecision(False, False, None, used, limit)

            cursor.execute(
                """
                INSERT INTO account_contact_reveals (
                    account_id, facebook_user_id, phone_number_id,
                    lookup_event_id, period_start, revealed_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (account_id, facebook_user_id, period_start)
                    DO NOTHING
                RETURNING id
                """,
                (
                    account_id,
                    facebook_user_id,
                    phone_number_id,
                    lookup_event_id,
                    period_start,
                    now,
                ),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                reveal_id = self._find_reveal(
                    cursor, account_id, facebook_user_id, period_start
                )
                if reveal_id is None:
                    raise DatabaseError("Database contact reveal write failed.")
                return RevealDecision(True, False, reveal_id, used, limit)

            cursor.execute(
                """
                UPDATE usage_monthly
                SET used_contact_count = used_contact_count + 1,
                    updated_at = %s
                WHERE account_id = %s AND period_start = %s
                RETURNING used_contact_count
                """,
                (now, account_id, period_start),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise DatabaseError("Database quota increment failed.")
            return RevealDecision(True, True, inserted[0], updated[0], limit)

    @staticmethod
    def _effective_limit(cursor: Any, account_id: int, now: datetime) -> int:
        cursor.execute(
            """
            SELECT COALESCE(
                (
                    SELECT monthly_contact_limit
                    FROM account_subscriptions
                    WHERE account_id = %s AND status = 'valid'
                      AND starts_at <= %s AND %s < ends_at
                    ORDER BY starts_at DESC, id DESC
                    LIMIT 1
                ),
                (
                    SELECT monthly_contact_limit
                    FROM plans
                    WHERE code = 'default' AND is_system = true
                )
            )
            """,
            (account_id, now, now),
        )
        row = cursor.fetchone()
        if row is None or row[0] is None:
            raise DatabaseError("Default entitlement plan is unavailable.")
        return row[0]

    def _lock_usage(
        self,
        cursor: Any,
        account_id: int,
        period_start: date,
        now: datetime,
    ) -> tuple[int, int]:
        limit = self._effective_limit(cursor, account_id, now)
        cursor.execute(
            """
            INSERT INTO usage_monthly (
                account_id, period_start, used_contact_count, created_at, updated_at
            ) VALUES (%s, %s, 0, %s, %s)
            ON CONFLICT (account_id, period_start) DO NOTHING
            """,
            (account_id, period_start, now, now),
        )
        cursor.execute(
            """
            SELECT used_contact_count
            FROM usage_monthly
            WHERE account_id = %s AND period_start = %s
            FOR UPDATE
            """,
            (account_id, period_start),
        )
        row = cursor.fetchone()
        if row is None:
            raise DatabaseError("Database monthly quota row is unavailable.")
        return limit, row[0]

    @staticmethod
    def _find_reveal(
        cursor: Any,
        account_id: int,
        facebook_user_id: int,
        period_start: date,
    ) -> int | None:
        cursor.execute(
            """
            SELECT id
            FROM account_contact_reveals
            WHERE account_id = %s AND facebook_user_id = %s
              AND period_start = %s
            """,
            (account_id, facebook_user_id, period_start),
        )
        row = cursor.fetchone()
        return None if row is None else row[0]
