from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import psycopg

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.jobs import KeysetCursor, Page, decode_cursor, encode_cursor
from fb_data_pipeline.core.phone import InvalidPhoneNumber, normalize_phone
from fb_data_pipeline.repositories.errors import DatabaseError


PhoneOrigin = Literal["fbnumber", "fb_crawl"]


@dataclass(frozen=True, slots=True)
class UserQuery:
    """Typed filters; display-name q uses Unicode lower(), so STRASSE != Straße."""

    q: str | None = None
    uid: str | None = None
    username: str | None = None
    phone: str | None = None
    phone_origin: PhoneOrigin | None = None
    has_phone: bool | None = None
    limit: int = 20
    cursor: str | None = None
    _display_name_q: str | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        raw_q = self.q
        object.__setattr__(self, "q", _search_value(raw_q, name="Search"))
        object.__setattr__(self, "_display_name_q", _display_name_search_value(raw_q))
        object.__setattr__(self, "uid", _exact_value(self.uid, name="UID"))
        object.__setattr__(self, "username", _search_value(self.username, name="Username"))
        if self.phone is not None:
            if not isinstance(self.phone, str):
                raise ValidationError("Invalid phone filter.")
            try:
                object.__setattr__(self, "phone", normalize_phone(self.phone))
            except InvalidPhoneNumber as error:
                raise ValidationError("Invalid phone filter.") from error
        if self.phone_origin not in (None, "fbnumber", "fb_crawl"):
            raise ValidationError("Invalid phone origin.")
        if self.has_phone is not None and not isinstance(self.has_phone, bool):
            raise ValidationError("has_phone must be a boolean.")
        _bounded_limit(self.limit)
        if self.cursor is not None:
            _decode_page_cursor(self.cursor)


@dataclass(frozen=True, slots=True)
class UserSummary:
    id: int
    facebook_uid: str | None
    username: str | None
    name: str | None
    profile_url: str | None
    phone_1: str | None
    phone_2: str | None
    address: str | None
    birth_date: str | None
    gender: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PhoneEvidenceView:
    id: int
    normalized_phone: str
    display_phone: str
    origin: PhoneOrigin
    source: str
    source_url: str
    provider: str
    confidence: str
    first_captured_at: datetime
    last_captured_at: datetime
    evidence_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class EnrichmentAttemptView:
    id: int
    provider: str
    status: str
    checked_at: datetime
    error_code: str
    values_found: int
    created_at: datetime


_USER_COLUMNS = """
    slots.id,
    slots.facebook_uid,
    slots.facebook_username,
    slots.display_name,
    slots.profile_url,
    slots.phone_1,
    slots.phone_2,
    slots.address,
    slots.birth_date,
    slots.gender,
    slots.created_at,
    slots.updated_at
"""


def _search_value(value: object, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{name} filter must be a string.")
    normalized = value.casefold().strip()
    if not normalized:
        raise ValidationError(f"{name} filter must not be empty.")
    return normalized


def _exact_value(value: object, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{name} filter must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValidationError(f"{name} filter must not be empty.")
    return normalized


def _display_name_search_value(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("Search filter must be a string.")
    normalized = value.lower().strip()
    if not normalized:
        raise ValidationError("Search filter must not be empty.")
    return normalized


def _bounded_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValidationError("limit must be an integer from 1 to 100.")
    return value


def _decode_page_cursor(value: str) -> KeysetCursor:
    try:
        return decode_cursor(value)
    except ValidationError as error:
        raise ValidationError("Invalid pagination cursor.") from error


def _prefix_pattern(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class UserQueryRepository:
    """Bounded, parameterized read queries for persisted Facebook users."""

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

    def list_users(self, query: UserQuery) -> Page[UserSummary]:
        if not isinstance(query, UserQuery):
            raise ValidationError("Invalid user query.")
        cursor_values = _decode_page_cursor(query.cursor) if query.cursor else None
        where, params = self._user_filters(query, cursor_values)
        with self._connect() as cursor:
            cursor.execute(
                self._list_users_sql(where),
                (*params, query.limit + 1),
            )
            rows = cursor.fetchall()
        items = tuple(self._user_from_row(row) for row in rows[:query.limit])
        next_cursor = (
            encode_cursor(KeysetCursor(items[-1].updated_at, items[-1].id))
            if len(rows) > query.limit
            else None
        )
        return Page(items, next_cursor)

    def get_user(self, user_id: int) -> UserSummary | None:
        user_id = self._user_id(user_id)
        with self._connect() as cursor:
            cursor.execute(
                f"""
                SELECT {_USER_COLUMNS}
                FROM facebook_user_phone_slots AS slots
                WHERE slots.id = %s
                """,
                (user_id,),
            )
            row = cursor.fetchone()
        return None if row is None else self._user_from_row(row)

    def list_phone_evidence(
        self,
        user_id: int,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[PhoneEvidenceView]:
        user_id = self._user_id(user_id)
        limit = _bounded_limit(limit)
        cursor_values = _decode_page_cursor(cursor) if cursor is not None else None
        where = "WHERE evidence.facebook_user_id = %s"
        params: tuple[object, ...] = (user_id,)
        if cursor_values is not None:
            where += " AND (evidence.last_captured_at, evidence.id) < (%s, %s)"
            params += (cursor_values.sort_at, cursor_values.row_id)
        with self._connect() as db_cursor:
            db_cursor.execute(
                f"""
                SELECT
                    evidence.id,
                    numbers.normalized_phone,
                    numbers.display_phone,
                    evidence.origin,
                    evidence.source,
                    evidence.source_url,
                    evidence.provider,
                    evidence.confidence,
                    evidence.first_captured_at,
                    evidence.last_captured_at,
                    evidence.evidence_count,
                    evidence.created_at,
                    evidence.updated_at
                FROM user_phone_evidence AS evidence
                JOIN phone_numbers AS numbers ON numbers.id = evidence.phone_number_id
                {where}
                ORDER BY evidence.last_captured_at DESC, evidence.id DESC
                LIMIT %s
                """,
                (*params, limit + 1),
            )
            rows = db_cursor.fetchall()
        items = tuple(self._evidence_from_row(row) for row in rows[:limit])
        next_cursor = (
            encode_cursor(KeysetCursor(items[-1].last_captured_at, items[-1].id))
            if len(rows) > limit
            else None
        )
        return Page(items, next_cursor)

    def list_enrichment_attempts(
        self,
        user_id: int,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[EnrichmentAttemptView]:
        user_id = self._user_id(user_id)
        limit = _bounded_limit(limit)
        cursor_values = _decode_page_cursor(cursor) if cursor is not None else None
        where = "WHERE attempts.facebook_user_id = %s"
        params: tuple[object, ...] = (user_id,)
        if cursor_values is not None:
            where += " AND (attempts.checked_at, attempts.id) < (%s, %s)"
            params += (cursor_values.sort_at, cursor_values.row_id)
        with self._connect() as db_cursor:
            db_cursor.execute(
                f"""
                SELECT
                    attempts.id,
                    attempts.provider,
                    attempts.status,
                    attempts.checked_at,
                    attempts.error_code,
                    attempts.values_found,
                    attempts.created_at
                FROM enrichment_attempts AS attempts
                {where}
                ORDER BY attempts.checked_at DESC, attempts.id DESC
                LIMIT %s
                """,
                (*params, limit + 1),
            )
            rows = db_cursor.fetchall()
        items = tuple(self._attempt_from_row(row) for row in rows[:limit])
        next_cursor = (
            encode_cursor(KeysetCursor(items[-1].checked_at, items[-1].id))
            if len(rows) > limit
            else None
        )
        return Page(items, next_cursor)

    def delete_user(self, user_id: int) -> bool:
        user_id = self._user_id(user_id)
        with self._connect() as cursor:
            cursor.execute("DELETE FROM facebook_users WHERE id = %s", (user_id,))
            return bool(cursor.rowcount and cursor.rowcount > 0)

    def update_user(
        self,
        user_id: int,
        *,
        name: str | None = None,
        username: str | None = None,
        address: str | None = None,
        gender: str | None = None,
        birth_date: str | None = None,
        phone_1: str | None = None,
        phone_2: str | None = None,
    ) -> UserSummary | None:
        user_id = self._user_id(user_id)
        with self._connect() as cursor:
            # Update facebook_users display_name / username
            updates = []
            params = []
            if name is not None:
                updates.append("display_name = %s")
                params.append(name.strip())
            if username is not None:
                updates.append("facebook_username = %s")
                params.append(username.strip())
            if updates:
                updates.append("updated_at = now()")
                params.append(user_id)
                cursor.execute(
                    f"UPDATE facebook_users SET {', '.join(updates)} WHERE id = %s",
                    tuple(params),
                )

            # Update facebook_user_profiles
            if address is not None or gender is not None or birth_date is not None:
                cursor.execute(
                    """
                    INSERT INTO facebook_user_profiles (facebook_user_id, address, gender, birth_date, updated_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (facebook_user_id) DO UPDATE SET
                        address = COALESCE(EXCLUDED.address, facebook_user_profiles.address),
                        gender = COALESCE(EXCLUDED.gender, facebook_user_profiles.gender),
                        birth_date = COALESCE(EXCLUDED.birth_date, facebook_user_profiles.birth_date),
                        updated_at = now()
                    """,
                    (user_id, address, gender, birth_date),
                )

            # Update phone evidence if phone_1 or phone_2 is provided
            for phone_val, origin in [(phone_1, "fbnumber"), (phone_2, "fb_crawl")]:
                if phone_val is not None and phone_val.strip():
                    display_phone = phone_val.strip()
                    try:
                        norm = normalize_phone(display_phone)
                    except InvalidPhoneNumber as error:
                        raise ValidationError("Invalid phone number.") from error
                    # Ensure phone number exists in phone_numbers table
                    cursor.execute(
                        """
                        INSERT INTO phone_numbers (normalized_phone, display_phone)
                        VALUES (%s, %s)
                        ON CONFLICT (normalized_phone) DO NOTHING
                        RETURNING id
                        """,
                        (norm, display_phone),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        cursor.execute("SELECT id FROM phone_numbers WHERE normalized_phone = %s", (norm,))
                        row = cursor.fetchone()
                    if row:
                        phone_id = row[0]
                        cursor.execute(
                            """
                            INSERT INTO user_phone_evidence (
                                facebook_user_id, phone_number_id, origin, source, source_url, provider, confidence,
                                first_captured_at, last_captured_at, evidence_count
                            ) VALUES (%s, %s, %s, 'manual_edit', '', 'manual', 'manual_edit', now(), now(), 1)
                            ON CONFLICT (facebook_user_id, phone_number_id, origin, source, source_url, provider)
                            DO UPDATE SET last_captured_at = now(), updated_at = now()
                            """,
                            (user_id, phone_id, origin),
                        )

        return self.get_user(user_id)

    @staticmethod
    def _user_id(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValidationError("Invalid user id.")
        return value

    @staticmethod
    def _user_filters(
        query: UserQuery,
        cursor_values: KeysetCursor | None,
    ) -> tuple[str, tuple[object, ...]]:
        clauses = ["WHERE TRUE"]
        params: tuple[object, ...] = ()
        if query.uid is not None:
            clauses.append("AND users.facebook_uid = %s")
            params += (query.uid,)
        if query.username is not None:
            clauses.append("AND users.normalized_username LIKE %s ESCAPE '\\'")
            params += (_prefix_pattern(query.username),)
        if query.q is not None:
            display_name_q = query._display_name_q
            if display_name_q is None:
                raise ValidationError("Invalid user query.")
            clauses.append(
                "AND (users.normalized_username LIKE %s ESCAPE '\\' "
                "OR lower(users.display_name) LIKE %s ESCAPE '\\')"
            )
            params += (_prefix_pattern(query.q), _prefix_pattern(display_name_q))
        if query.phone is not None or query.phone_origin is not None:
            evidence_clauses = ["phone_evidence.facebook_user_id = users.id"]
            if query.phone is not None:
                evidence_clauses.append("phone_numbers.normalized_phone = %s")
                params += (query.phone,)
            if query.phone_origin is not None:
                evidence_clauses.append("phone_evidence.origin = %s")
                params += (query.phone_origin,)
            clauses.append(
                """AND EXISTS (
                    SELECT 1
                    FROM user_phone_evidence AS phone_evidence
                    JOIN phone_numbers AS phone_numbers
                        ON phone_numbers.id = phone_evidence.phone_number_id
                    WHERE """
                + " AND ".join(evidence_clauses)
                + ")"
            )
        if query.has_phone is True:
            clauses.append(
                "AND EXISTS (SELECT 1 FROM user_phone_evidence AS any_evidence "
                "WHERE any_evidence.facebook_user_id = users.id)"
            )
        elif query.has_phone is False:
            clauses.append(
                "AND NOT EXISTS (SELECT 1 FROM user_phone_evidence AS any_evidence "
                "WHERE any_evidence.facebook_user_id = users.id)"
            )
        if cursor_values is not None:
            clauses.append("AND (users.updated_at, users.id) < (%s, %s)")
            params += (cursor_values.sort_at, cursor_values.row_id)
        return "\n".join(clauses), params

    @staticmethod
    def _list_users_sql(where: str) -> str:
        return f"""
            WITH selected_users AS MATERIALIZED (
                SELECT users.id
                FROM facebook_users AS users
                {where}
                ORDER BY users.updated_at DESC, users.id DESC
                LIMIT %s
            )
            SELECT {_USER_COLUMNS}
            FROM selected_users
            JOIN facebook_user_phone_slots AS slots ON slots.id = selected_users.id
            ORDER BY slots.updated_at DESC, slots.id DESC
        """

    @staticmethod
    def _user_from_row(row: tuple[object, ...]) -> UserSummary:
        try:
            return UserSummary(
                id=int(row[0]),
                facebook_uid=_optional_text(row[1]),
                username=_optional_text(row[2]),
                name=_optional_text(row[3]),
                profile_url=_optional_text(row[4]),
                phone_1=_optional_text(row[5]),
                phone_2=_optional_text(row[6]),
                address=_optional_text(row[7]),
                birth_date=_optional_text(row[8]),
                gender=_optional_text(row[9]),
                created_at=_timestamp(row[10]),
                updated_at=_timestamp(row[11]),
            )
        except (IndexError, TypeError, ValueError) as error:
            raise DatabaseError("Database user data is invalid.") from error

    @staticmethod
    def _evidence_from_row(row: tuple[object, ...]) -> PhoneEvidenceView:
        try:
            return PhoneEvidenceView(
                id=int(row[0]),
                normalized_phone=str(row[1]),
                display_phone=str(row[2]),
                origin=_phone_origin(row[3]),
                source=str(row[4]),
                source_url=str(row[5]),
                provider=str(row[6]),
                confidence=str(row[7]),
                first_captured_at=_timestamp(row[8]),
                last_captured_at=_timestamp(row[9]),
                evidence_count=int(row[10]),
                created_at=_timestamp(row[11]),
                updated_at=_timestamp(row[12]),
            )
        except (IndexError, TypeError, ValueError) as error:
            raise DatabaseError("Database phone evidence is invalid.") from error

    @staticmethod
    def _attempt_from_row(row: tuple[object, ...]) -> EnrichmentAttemptView:
        try:
            return EnrichmentAttemptView(
                id=int(row[0]),
                provider=str(row[1]),
                status=str(row[2]),
                checked_at=_timestamp(row[3]),
                error_code=str(row[4]),
                values_found=int(row[5]),
                created_at=_timestamp(row[6]),
            )
        except (IndexError, TypeError, ValueError) as error:
            raise DatabaseError("Database enrichment attempt is invalid.") from error


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError
    return value


def _phone_origin(value: object) -> PhoneOrigin:
    if value not in ("fbnumber", "fb_crawl"):
        raise ValueError
    return value
