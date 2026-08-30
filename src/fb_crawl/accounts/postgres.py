from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
import json
from typing import Any
from uuid import UUID, uuid4

import psycopg

from fb_crawl.accounts.models import (
    Account,
    AccountRole,
    AccountStatus,
    AuthSession,
    Device,
    DeviceStatus,
)
from fb_crawl.accounts.repository import (
    AdminAlreadyExists,
    AccountNotFound,
    AdminAccountProtected,
    DeviceNotFound,
    InvalidAccountToken,
    SessionReuseDetected,
    SessionUnavailable,
    TokenPurpose,
)
from fb_data_pipeline.repositories.errors import DatabaseError


_ACCOUNT_COLUMNS = """
    id, normalized_email, display_email, password_hash, role, status,
    email_verified_at, created_at, updated_at, deletion_requested_at
"""
_DEVICE_COLUMNS = """
    id, account_id, installation_id, display_name, status,
    first_seen_at, last_seen_at
"""
_SESSION_COLUMNS = """
    id, account_id, device_id, expires_at, rotated_from_id, revoked_at,
    created_at, last_used_at, authenticated_at
"""
_TOKEN_PURPOSES = frozenset({"email_verify", "password_reset"})


class PostgresAccountRepository:
    """Short PostgreSQL transactions for account-owned authentication state."""

    def __init__(
        self,
        database_url: str,
        *,
        statement_timeout_seconds: float = 5.0,
        connect_factory=psycopg.connect,
        uuid_factory=uuid4,
    ) -> None:
        self.database_url = database_url
        self.statement_timeout_ms = max(1, round(statement_timeout_seconds * 1000))
        self.connect_factory = connect_factory
        self.uuid_factory = uuid_factory

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

    def create_account(
        self,
        normalized_email: str,
        display_email: str,
        password_hash: str,
    ) -> Account:
        with self._connect() as cursor:
            cursor.execute(
                f"""
                INSERT INTO accounts (
                    normalized_email, display_email, password_hash
                ) VALUES (%s, %s, %s)
                RETURNING {_ACCOUNT_COLUMNS}
                """,
                (normalized_email, display_email, password_hash),
            )
            row = cursor.fetchone()
        return self._required_account(row)

    def find_account_by_email(self, normalized_email: str) -> Account | None:
        with self._connect() as cursor:
            cursor.execute(
                f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE normalized_email = %s",
                (normalized_email,),
            )
            row = cursor.fetchone()
        return None if row is None else self._account(row)

    def get_account(self, account_id: int) -> Account | None:
        with self._connect() as cursor:
            cursor.execute(
                f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE id = %s",
                (account_id,),
            )
            row = cursor.fetchone()
        return None if row is None else self._account(row)

    def list_accounts(
        self, *, limit: int = 100, cursor: int | None = None
    ) -> tuple[Account, ...]:
        if not 1 <= limit <= 101:
            raise ValueError("account list limit must be from 1 to 101")
        if cursor is not None and cursor <= 0:
            raise ValueError("account cursor must be positive")
        cursor_id = cursor
        with self._connect() as database_cursor:
            database_cursor.execute(
                f"""
                SELECT {_ACCOUNT_COLUMNS}
                FROM accounts
                WHERE (%s IS NULL OR id < %s)
                ORDER BY id DESC
                LIMIT %s
                """,
                (cursor_id, cursor_id, limit),
            )
            rows = database_cursor.fetchall()
        return tuple(self._account(row) for row in rows)

    def suspend_account(self, account_id: int, now: datetime) -> Account:
        with self._connect() as cursor:
            cursor.execute(
                f"""
                UPDATE accounts
                SET status = 'suspended', updated_at = %s
                WHERE id = %s AND status NOT IN ('deleted', 'suspended')
                RETURNING {_ACCOUNT_COLUMNS}
                """,
                (now, account_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise AccountNotFound("Account was not found.")
            cursor.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = COALESCE(revoked_at, %s)
                WHERE account_id = %s
                """,
                (now, account_id),
            )
        return self._account(row)

    def suspend_account_as_admin(
        self, account_id: int, actor_account_id: int, now: datetime
    ) -> Account:
        with self._connect() as cursor:
            cursor.execute(
                f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE id = %s FOR UPDATE",
                (account_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise AccountNotFound("Account was not found.")
            account = self._account(row)
            if account.id == actor_account_id or account.role is AccountRole.ADMIN:
                raise AdminAccountProtected("Administrator accounts cannot be suspended here.")
            if account.status in {AccountStatus.DELETED, AccountStatus.SUSPENDED}:
                raise AccountNotFound("Account was not found.")
            cursor.execute(
                "UPDATE accounts SET status = 'suspended', updated_at = %s WHERE id = %s",
                (now, account_id),
            )
            cursor.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = COALESCE(revoked_at, %s)
                WHERE account_id = %s
                """,
                (now, account_id),
            )
            self._write_admin_audit(
                cursor,
                actor_account_id,
                "account_suspended",
                "account",
                str(account_id),
                {},
                now,
            )
        return Account(
            id=account.id,
            normalized_email=account.normalized_email,
            display_email=account.display_email,
            password_hash=account.password_hash,
            role=account.role,
            status=AccountStatus.SUSPENDED,
            email_verified_at=account.email_verified_at,
            created_at=account.created_at,
            updated_at=now,
            deletion_requested_at=account.deletion_requested_at,
        )

    def create_account_token(
        self,
        account_id: int,
        purpose: TokenPurpose,
        token_digest: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        if purpose not in _TOKEN_PURPOSES:
            raise ValueError("invalid account token purpose")
        with self._connect() as cursor:
            cursor.execute(
                """
                UPDATE account_tokens
                SET consumed_at = %s
                WHERE account_id = %s AND purpose = %s AND consumed_at IS NULL
                """,
                (now, account_id, purpose),
            )
            cursor.execute(
                """
                INSERT INTO account_tokens (
                    account_id, purpose, token_hash, expires_at, created_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (account_id, purpose, token_digest, expires_at, now),
            )

    def verify_email_token(self, token_digest: str, now: datetime) -> Account:
        with self._connect() as cursor:
            token = self._lock_account_token(
                cursor, token_digest, "email_verify"
            )
            token_id, account_id, expires_at, consumed_at = token
            if consumed_at is not None or expires_at <= now:
                raise InvalidAccountToken("Account token is invalid or expired.")
            cursor.execute(
                "UPDATE account_tokens SET consumed_at = %s WHERE id = %s",
                (now, token_id),
            )
            cursor.execute(
                f"""
                UPDATE accounts
                SET email_verified_at = COALESCE(email_verified_at, %s),
                    status = CASE WHEN status = 'pending' THEN 'active' ELSE status END,
                    updated_at = %s
                WHERE id = %s AND status <> 'deleted'
                RETURNING {_ACCOUNT_COLUMNS}
                """,
                (now, now, account_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise InvalidAccountToken("Account token is invalid or expired.")
        return self._account(row)

    def consume_password_reset_token(
        self, token_digest: str, now: datetime
    ) -> Account:
        with self._connect() as cursor:
            token = self._lock_account_token(
                cursor, token_digest, "password_reset"
            )
            token_id, account_id, expires_at, consumed_at = token
            if consumed_at is not None or expires_at <= now:
                raise InvalidAccountToken("Account token is invalid or expired.")
            cursor.execute(
                "UPDATE account_tokens SET consumed_at = %s WHERE id = %s",
                (now, token_id),
            )
            cursor.execute(
                f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE id = %s AND status <> 'deleted'",
                (account_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise InvalidAccountToken("Account token is invalid or expired.")
        return self._account(row)

    def update_password(
        self, account_id: int, password_hash: str, now: datetime
    ) -> Account:
        with self._connect() as cursor:
            cursor.execute(
                f"""
                UPDATE accounts
                SET password_hash = %s, updated_at = %s
                WHERE id = %s AND status <> 'deleted'
                RETURNING {_ACCOUNT_COLUMNS}
                """,
                (password_hash, now, account_id),
            )
            row = cursor.fetchone()
        return self._required_account(row)

    def create_device(
        self,
        account_id: int,
        installation_id: UUID,
        display_name: str,
        now: datetime,
    ) -> Device:
        with self._connect() as cursor:
            cursor.execute(
                f"""
                INSERT INTO devices (
                    account_id, installation_id, display_name,
                    first_seen_at, last_seen_at
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (account_id, installation_id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    last_seen_at = EXCLUDED.last_seen_at
                RETURNING {_DEVICE_COLUMNS}
                """,
                (account_id, installation_id, display_name, now, now),
            )
            row = cursor.fetchone()
        if row is None:
            raise DatabaseError("Database device write failed.")
        return self._device(row)

    def get_device(self, account_id: int, device_id: int) -> Device | None:
        with self._connect() as cursor:
            cursor.execute(
                f"SELECT {_DEVICE_COLUMNS} FROM devices WHERE account_id = %s AND id = %s",
                (account_id, device_id),
            )
            row = cursor.fetchone()
        return None if row is None else self._device(row)

    def list_devices(self, account_id: int) -> tuple[Device, ...]:
        with self._connect() as cursor:
            cursor.execute(
                f"""
                SELECT {_DEVICE_COLUMNS}
                FROM devices
                WHERE account_id = %s
                ORDER BY first_seen_at, id
                """,
                (account_id,),
            )
            rows = cursor.fetchall()
        return tuple(self._device(row) for row in rows)

    def revoke_device(
        self, account_id: int, device_id: int, now: datetime
    ) -> Device:
        with self._connect() as cursor:
            cursor.execute(
                f"""
                SELECT {_DEVICE_COLUMNS}
                FROM devices
                WHERE account_id = %s AND id = %s
                FOR UPDATE
                """,
                (account_id, device_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise DeviceNotFound("Device was not found.")
            cursor.execute(
                """
                UPDATE devices
                SET status = 'revoked', last_seen_at = GREATEST(last_seen_at, %s)
                WHERE id = %s
                """,
                (now, device_id),
            )
            cursor.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = COALESCE(revoked_at, %s)
                WHERE account_id = %s AND device_id = %s
                """,
                (now, account_id, device_id),
            )
        device = self._device(row)
        return Device(
            id=device.id,
            account_id=device.account_id,
            installation_id=device.installation_id,
            display_name=device.display_name,
            status=DeviceStatus.REVOKED,
            first_seen_at=device.first_seen_at,
            last_seen_at=max(device.last_seen_at, now),
        )

    def revoke_device_as_admin(
        self, account_id: int, device_id: int, actor_account_id: int, now: datetime
    ) -> Device:
        with self._connect() as cursor:
            cursor.execute(
                f"""
                SELECT {_DEVICE_COLUMNS} FROM devices
                WHERE account_id = %s AND id = %s FOR UPDATE
                """,
                (account_id, device_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise DeviceNotFound("Device was not found.")
            device = self._device(row)
            cursor.execute(
                "UPDATE devices SET status = 'revoked', last_seen_at = GREATEST(last_seen_at, %s) WHERE id = %s",
                (now, device_id),
            )
            cursor.execute(
                "UPDATE auth_sessions SET revoked_at = COALESCE(revoked_at, %s) WHERE account_id = %s AND device_id = %s",
                (now, account_id, device_id),
            )
            self._write_admin_audit(
                cursor,
                actor_account_id,
                "account_device_revoked",
                "device",
                str(device_id),
                {"account_id": account_id},
                now,
            )
        return Device(
            id=device.id,
            account_id=device.account_id,
            installation_id=device.installation_id,
            display_name=device.display_name,
            status=DeviceStatus.REVOKED,
            first_seen_at=device.first_seen_at,
            last_seen_at=max(device.last_seen_at, now),
        )

    def create_session(
        self,
        account_id: int,
        device_id: int,
        refresh_digest: str,
        expires_at: datetime,
        now: datetime,
    ) -> AuthSession:
        session_id = self.uuid_factory()
        with self._connect() as cursor:
            cursor.execute(
                f"""
                INSERT INTO auth_sessions (
                    id, account_id, device_id, refresh_token_hash,
                    expires_at, created_at, last_used_at, authenticated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_SESSION_COLUMNS}
                """,
                (
                    session_id,
                    account_id,
                    device_id,
                    refresh_digest,
                    expires_at,
                    now,
                    now,
                    now,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise DatabaseError("Database session write failed.")
        return self._session(row)

    def get_session(self, session_id: UUID) -> AuthSession | None:
        with self._connect() as cursor:
            cursor.execute(
                f"SELECT {_SESSION_COLUMNS} FROM auth_sessions WHERE id = %s",
                (session_id,),
            )
            row = cursor.fetchone()
        return None if row is None else self._session(row)

    def rotate_session(
        self,
        old_digest: str,
        new_digest: str,
        now: datetime,
        expires_at: datetime,
    ) -> AuthSession:
        replacement: AuthSession | None = None
        reuse_detected = False
        with self._connect() as cursor:
            cursor.execute(
                f"""
                SELECT {_SESSION_COLUMNS}, (
                    SELECT child.id
                    FROM auth_sessions AS child
                    WHERE child.rotated_from_id = auth_sessions.id
                ) AS replacement_id
                FROM auth_sessions
                WHERE refresh_token_hash = %s
                FOR UPDATE
                """,
                (old_digest,),
            )
            row = cursor.fetchone()
            if row is None:
                raise SessionUnavailable("Refresh session is unavailable.")
            old_session = self._session(row[:9])
            replacement_id = row[9]
            if replacement_id is not None:
                self._revoke_session_family(cursor, old_session.id, now)
                reuse_detected = True
            elif old_session.revoked_at is not None or old_session.expires_at <= now:
                raise SessionUnavailable("Refresh session is unavailable.")
            else:
                cursor.execute(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = %s, last_used_at = %s
                    WHERE id = %s
                    """,
                    (now, now, old_session.id),
                )
                new_session_id = self.uuid_factory()
                cursor.execute(
                    f"""
                    INSERT INTO auth_sessions (
                        id, account_id, device_id, refresh_token_hash,
                        expires_at, rotated_from_id, created_at, last_used_at,
                        authenticated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING {_SESSION_COLUMNS}
                    """,
                    (
                        new_session_id,
                        old_session.account_id,
                        old_session.device_id,
                        new_digest,
                        expires_at,
                        old_session.id,
                        now,
                        now,
                        old_session.authenticated_at,
                    ),
                )
                new_row = cursor.fetchone()
                if new_row is None:
                    raise DatabaseError("Database session rotation failed.")
                replacement = self._session(new_row)
        if reuse_detected:
            raise SessionReuseDetected(
                "Refresh token reuse was detected; the session family was revoked."
            )
        if replacement is None:
            raise DatabaseError("Database session rotation failed.")
        return replacement

    def revoke_session(self, session_id: UUID, now: datetime) -> None:
        with self._connect() as cursor:
            cursor.execute(
                "UPDATE auth_sessions SET revoked_at = COALESCE(revoked_at, %s) WHERE id = %s",
                (now, session_id),
            )

    def revoke_session_by_refresh_digest(
        self, refresh_digest: str, now: datetime
    ) -> None:
        with self._connect() as cursor:
            cursor.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = COALESCE(revoked_at, %s)
                WHERE refresh_token_hash = %s
                """,
                (now, refresh_digest),
            )

    def mark_session_reauthenticated(
        self, session_id: UUID, now: datetime
    ) -> AuthSession:
        with self._connect() as cursor:
            cursor.execute(
                f"""
                UPDATE auth_sessions
                SET authenticated_at = %s, last_used_at = GREATEST(last_used_at, %s)
                WHERE id = %s AND revoked_at IS NULL AND expires_at > %s
                RETURNING {_SESSION_COLUMNS}
                """,
                (now, now, session_id, now),
            )
            row = cursor.fetchone()
        if row is None:
            raise SessionUnavailable("Authentication session is unavailable.")
        return self._session(row)

    def revoke_account_sessions(self, account_id: int, now: datetime) -> None:
        with self._connect() as cursor:
            cursor.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = COALESCE(revoked_at, %s)
                WHERE account_id = %s
                """,
                (now, account_id),
            )

    def revoke_account_sessions_as_admin(
        self, account_id: int, actor_account_id: int, now: datetime
    ) -> None:
        with self._connect() as cursor:
            cursor.execute(
                "SELECT 1 FROM accounts WHERE id = %s AND status <> 'deleted' FOR UPDATE",
                (account_id,),
            )
            if cursor.fetchone() is None:
                raise AccountNotFound("Account was not found.")
            cursor.execute(
                "UPDATE auth_sessions SET revoked_at = COALESCE(revoked_at, %s) WHERE account_id = %s",
                (now, account_id),
            )
            self._write_admin_audit(
                cursor,
                actor_account_id,
                "account_sessions_revoked",
                "account",
                str(account_id),
                {},
                now,
            )

    def request_account_deletion(
        self, account_id: int, now: datetime
    ) -> Account:
        with self._connect() as cursor:
            cursor.execute(
                f"""
                UPDATE accounts
                SET status = 'deleted', deletion_requested_at = %s, updated_at = %s
                WHERE id = %s AND status <> 'deleted'
                RETURNING {_ACCOUNT_COLUMNS}
                """,
                (now, now, account_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise AccountNotFound("Account was not found.")
            cursor.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = COALESCE(revoked_at, %s)
                WHERE account_id = %s
                """,
                (now, account_id),
            )
        return self._account(row)

    def purge_due_deleted_accounts(self, cutoff: datetime) -> tuple[int, ...]:
        with self._connect() as cursor:
            cursor.execute(
                """
                DELETE FROM accounts
                WHERE status = 'deleted' AND deletion_requested_at <= %s
                RETURNING id
                """,
                (cutoff,),
            )
            rows = cursor.fetchall()
        return tuple(row[0] for row in rows)

    def record_rate_limit_hit(
        self,
        bucket_hash: str,
        action: str,
        window_start: datetime,
        expires_at: datetime,
    ) -> int:
        with self._connect() as cursor:
            cursor.execute(
                """
                INSERT INTO rate_limit_buckets (
                    bucket_hash, action, window_start, request_count, expires_at
                ) VALUES (%s, %s, %s, 1, %s)
                ON CONFLICT (bucket_hash, action, window_start) DO UPDATE
                SET request_count = rate_limit_buckets.request_count + 1,
                    expires_at = GREATEST(rate_limit_buckets.expires_at, EXCLUDED.expires_at)
                RETURNING request_count
                """,
                (bucket_hash, action, window_start, expires_at),
            )
            row = cursor.fetchone()
        if row is None:
            raise DatabaseError("Database rate-limit write failed.")
        return int(row[0])

    def bootstrap_admin(
        self,
        normalized_email: str,
        display_email: str,
        password_hash: str,
        now: datetime,
    ) -> Account:
        with self._connect() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext('lead_finder_admin_bootstrap'))"
            )
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM accounts WHERE role = 'admin')"
            )
            row = cursor.fetchone()
            if row is None:
                raise DatabaseError("Database administrator check failed.")
            if bool(row[0]):
                raise AdminAlreadyExists("An administrator already exists.")
            cursor.execute(
                f"""
                INSERT INTO accounts (
                    normalized_email, display_email, password_hash,
                    role, status, email_verified_at, created_at, updated_at
                ) VALUES (%s, %s, %s, 'admin', 'active', %s, %s, %s)
                RETURNING {_ACCOUNT_COLUMNS}
                """,
                (
                    normalized_email,
                    display_email,
                    password_hash,
                    now,
                    now,
                    now,
                ),
            )
            created = cursor.fetchone()
        return self._required_account(created)

    @staticmethod
    def _write_admin_audit(
        cursor: Any,
        actor_account_id: int,
        action: str,
        target_type: str,
        target_id: str,
        details: dict[str, object],
        now: datetime,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO admin_audit_events (
                actor_account_id, action, target_type, target_id, details, created_at
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                actor_account_id,
                action,
                target_type,
                target_id,
                json.dumps(details, sort_keys=True),
                now,
            ),
        )

    @staticmethod
    def _lock_account_token(
        cursor: Any, token_digest: str, purpose: TokenPurpose
    ) -> tuple[Any, ...]:
        cursor.execute(
            """
            SELECT id, account_id, expires_at, consumed_at
            FROM account_tokens
            WHERE token_hash = %s AND purpose = %s
            FOR UPDATE
            """,
            (token_digest, purpose),
        )
        row = cursor.fetchone()
        if row is None:
            raise InvalidAccountToken("Account token is invalid or expired.")
        return row

    @staticmethod
    def _revoke_session_family(cursor: Any, session_id: UUID, now: datetime) -> None:
        cursor.execute(
            """
            WITH RECURSIVE ancestors AS (
                SELECT id, rotated_from_id
                FROM auth_sessions
                WHERE id = %s
                UNION ALL
                SELECT parent.id, parent.rotated_from_id
                FROM auth_sessions AS parent
                JOIN ancestors AS child ON child.rotated_from_id = parent.id
            ), root AS (
                SELECT id FROM ancestors WHERE rotated_from_id IS NULL LIMIT 1
            ), family AS (
                SELECT session.id
                FROM auth_sessions AS session
                JOIN root ON session.id = root.id
                UNION ALL
                SELECT child.id
                FROM auth_sessions AS child
                JOIN family AS parent ON child.rotated_from_id = parent.id
            )
            UPDATE auth_sessions
            SET revoked_at = COALESCE(revoked_at, %s)
            WHERE id IN (SELECT id FROM family)
            """,
            (session_id, now),
        )

    @staticmethod
    def _account(row: tuple[Any, ...]) -> Account:
        return Account(
            id=row[0],
            normalized_email=row[1],
            display_email=row[2],
            password_hash=row[3],
            role=AccountRole(row[4]),
            status=AccountStatus(row[5]),
            email_verified_at=row[6],
            created_at=row[7],
            updated_at=row[8],
            deletion_requested_at=row[9],
        )

    @classmethod
    def _required_account(cls, row: tuple[Any, ...] | None) -> Account:
        if row is None:
            raise DatabaseError("Database account write failed.")
        return cls._account(row)

    @staticmethod
    def _device(row: tuple[Any, ...]) -> Device:
        return Device(
            id=row[0],
            account_id=row[1],
            installation_id=row[2],
            display_name=row[3],
            status=DeviceStatus(row[4]),
            first_seen_at=row[5],
            last_seen_at=row[6],
        )

    @staticmethod
    def _session(row: tuple[Any, ...]) -> AuthSession:
        return AuthSession(
            id=row[0],
            account_id=row[1],
            device_id=row[2],
            expires_at=row[3],
            rotated_from_id=row[4],
            revoked_at=row[5],
            created_at=row[6],
            last_used_at=row[7],
            authenticated_at=row[8],
        )
