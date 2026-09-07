from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg

from fb_crawl.contacts.models import (
    LookupOutcome,
    LookupScanMode,
    LookupSource,
    LookupSourceType,
)
from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.jobs import KeysetCursor, Page, decode_cursor, encode_cursor
from fb_crawl.history.models import AccountHistoryQuery, HistoryItem
from fb_data_pipeline.repositories.errors import DatabaseError


_COLUMNS = """
    events.id, events.account_id, events.device_id,
    events.facebook_user_id, users.facebook_uid,
    users.facebook_username, users.display_name, users.profile_url,
    events.revealed_phone_number_id, numbers.normalized_phone,
    events.outcome, events.result_source,
    events.provider_called, events.quota_charged, events.safe_error_code,
    events.created_at, events.completed_at,
    events.scan_mode, events.source_type, events.source_url,
    events.product_crawl_job_id
"""


class PostgresHistoryRepository:
    def __init__(
        self,
        database_url: str,
        *,
        statement_timeout_seconds: float = 5.0,
        connect_factory=psycopg.connect,
    ) -> None:
        self.database_url = database_url
        self.statement_timeout_ms = max(
            1, round(statement_timeout_seconds * 1000)
        )
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

    def list(self, query: AccountHistoryQuery) -> Page[HistoryItem]:
        self._require_query(query)
        where, params = self._filters(
            query,
            include_cursor=True,
            include_account=False,
            latest_only=True,
        )
        with self._connect() as cursor:
            cursor.execute(
                f"""
                WITH ranked_events AS (
                    SELECT events.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY events.account_id,
                                            events.facebook_user_id
                               ORDER BY events.created_at DESC, events.id DESC
                           ) AS person_rank
                    FROM lookup_events AS events
                    WHERE events.account_id = %s
                )
                SELECT {_COLUMNS}
                FROM ranked_events AS events
                JOIN facebook_users AS users
                  ON users.id = events.facebook_user_id
                LEFT JOIN phone_numbers AS numbers
                  ON numbers.id = events.revealed_phone_number_id
                {where}
                ORDER BY events.created_at DESC, events.id DESC
                LIMIT %s
                """,
                (query.account_id, *params, query.limit + 1),
            )
            rows = cursor.fetchall()
        items = tuple(self._item(row) for row in rows[: query.limit])
        next_cursor = (
            encode_cursor(KeysetCursor(items[-1].created_at, items[-1].id))
            if len(rows) > query.limit
            else None
        )
        return Page(items, next_cursor)

    def get(self, account_id: int, event_id: int) -> HistoryItem | None:
        self._ids(account_id, event_id)
        with self._connect() as cursor:
            cursor.execute(
                f"""
                SELECT {_COLUMNS}
                FROM lookup_events AS events
                JOIN facebook_users AS users
                  ON users.id = events.facebook_user_id
                LEFT JOIN phone_numbers AS numbers
                  ON numbers.id = events.revealed_phone_number_id
                WHERE events.account_id = %s AND events.id = %s
                """,
                (account_id, event_id),
            )
            row = cursor.fetchone()
        return self._item(row) if row is not None else None

    def delete_one(self, account_id: int, event_id: int) -> bool:
        self._ids(account_id, event_id)
        with self._connect() as cursor:
            cursor.execute(
                """
                DELETE FROM lookup_events
                WHERE account_id = %s AND id = %s
                """,
                (account_id, event_id),
            )
            return bool(cursor.rowcount and cursor.rowcount > 0)

    def delete_person(self, account_id: int, facebook_user_id: int) -> int:
        self._ids(account_id, facebook_user_id)
        with self._connect() as cursor:
            cursor.execute(
                """
                DELETE FROM lookup_events
                WHERE account_id = %s AND facebook_user_id = %s
                """,
                (account_id, facebook_user_id),
            )
            return max(0, int(cursor.rowcount or 0))

    def delete_filtered(self, query: AccountHistoryQuery) -> int:
        self._require_query(query)
        if query.cursor is not None:
            raise ValidationError("History deletion cannot use a cursor.")
        where, params = self._filters(query, include_cursor=False)
        using: list[str] = []
        join_clauses: list[str] = []
        if any((query.name, query.uid, query.username)):
            using.append("facebook_users AS users")
            join_clauses.append("users.id = events.facebook_user_id")
        if query.phone is not None:
            using.append("phone_numbers AS numbers")
            join_clauses.append(
                "numbers.id = events.revealed_phone_number_id"
            )
        if join_clauses:
            where += " AND " + " AND ".join(join_clauses)
        using_sql = " USING " + ", ".join(using) if using else ""
        with self._connect() as cursor:
            cursor.execute(
                f"DELETE FROM lookup_events AS events{using_sql} {where}",
                params,
            )
            return max(0, int(cursor.rowcount or 0))

    @staticmethod
    def _require_query(query: AccountHistoryQuery) -> None:
        if not isinstance(query, AccountHistoryQuery):
            raise ValidationError("Invalid history query.")

    @staticmethod
    def _ids(account_id: int, event_id: int) -> None:
        for value in (account_id, event_id):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValidationError("Invalid history identifier.")

    @staticmethod
    def _filters(
        query: AccountHistoryQuery,
        *,
        include_cursor: bool,
        include_account: bool = True,
        latest_only: bool = False,
    ) -> tuple[str, tuple[object, ...]]:
        clauses: list[str] = []
        params: list[object] = []
        if include_account:
            clauses.append("events.account_id = %s")
            params.append(query.account_id)
        if latest_only:
            clauses.append("events.person_rank = 1")
        if query.product_crawl_job_id is not None:
            clauses.append("events.product_crawl_job_id = %s")
            params.append(query.product_crawl_job_id)
        if query.outcome is not None:
            clauses.append("events.outcome = %s")
            params.append(query.outcome.value)
        if query.name is not None:
            clauses.append("users.display_name ILIKE %s ESCAPE '\\'")
            params.append(_contains(query.name))
        if query.uid is not None:
            clauses.append("users.facebook_uid LIKE %s ESCAPE '\\'")
            params.append(_prefix(query.uid))
        if query.username is not None:
            clauses.append("users.normalized_username LIKE %s ESCAPE '\\'")
            params.append(_prefix(query.username.casefold()))
        if query.phone is not None:
            clauses.append("numbers.normalized_phone = %s")
            params.append(query.phone)
        if query.created_from is not None:
            clauses.append("events.created_at >= %s")
            params.append(query.created_from)
        if query.created_to is not None:
            clauses.append("events.created_at <= %s")
            params.append(query.created_to)
        if include_cursor and query.cursor is not None:
            page = decode_cursor(query.cursor)
            clauses.append("(events.created_at, events.id) < (%s, %s)")
            params.extend((page.sort_at, page.row_id))
        return "WHERE " + " AND ".join(clauses), tuple(params)

    @staticmethod
    def _item(row: tuple[object, ...]) -> HistoryItem:
        return HistoryItem(
            id=int(row[0]),
            account_id=int(row[1]),
            device_id=int(row[2]) if row[2] is not None else None,
            facebook_user_id=int(row[3]),
            facebook_uid=str(row[4] or ""),
            username=str(row[5] or ""),
            name=str(row[6] or ""),
            profile_url=str(row[7] or ""),
            phone_number_id=int(row[8]) if row[8] is not None else None,
            phone=str(row[9] or ""),
            outcome=LookupOutcome(str(row[10])),
            source=LookupSource(str(row[11])),
            provider_called=bool(row[12]),
            quota_charged=bool(row[13]),
            safe_error_code=str(row[14] or ""),
            created_at=row[15],  # type: ignore[arg-type]
            completed_at=row[16],  # type: ignore[arg-type]
            scan_mode=LookupScanMode(str(row[17])),
            source_type=LookupSourceType(str(row[18])),
            source_url=str(row[19] or ""),
            product_crawl_job_id=row[20],  # type: ignore[arg-type]
        )


def _escaped(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _contains(value: str) -> str:
    return "%" + _escaped(value) + "%"


def _prefix(value: str) -> str:
    return _escaped(value) + "%"
