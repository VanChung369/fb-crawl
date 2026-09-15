from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
import json
from typing import Any

import psycopg

from fb_crawl.entitlements.models import Entitlements
from fb_crawl.entitlements.time import add_duration
from fb_crawl.licenses.models import (
    AdminAuditEvent,
    LicenseDuration,
    LicenseGrant,
    LicenseKey,
    LicenseKeyStatus,
    Subscription,
    SubscriptionStatus,
)
from fb_crawl.licenses.repository import (
    InvalidLicenseKey,
    LicenseAlreadyRedeemed,
    LicenseScheduleError,
)
from fb_data_pipeline.repositories.errors import DatabaseError


_KEY_COLUMNS = """
    id, key_digest, key_version, masked_key, duration_unit, duration_value,
    monthly_contact_limit, max_devices, allow_group_crawl,
    allow_comment_crawl, status, created_by_account_id,
    redeemed_by_account_id, redeemed_at, created_at, revoked_at, encrypted_key
"""
_SUBSCRIPTION_COLUMNS = """
    id, account_id, license_key_id, duration_unit, duration_value,
    monthly_contact_limit, max_devices, allow_group_crawl,
    allow_comment_crawl, starts_at, ends_at, status, revoked_at, created_at
"""


class PostgresLicenseRepository:
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

    def create_key(
        self,
        *,
        key_digest: str,
        key_version: int,
        masked_key: str,
        grant: LicenseGrant,
        created_by_account_id: int | None,
        now: datetime,
        encrypted_key: str | None = None,
    ) -> LicenseKey:
        with self._connect() as cursor:
            cursor.execute(
                f"""
                INSERT INTO license_keys (
                    key_digest, key_version, masked_key,
                    duration_unit, duration_value, monthly_contact_limit,
                    max_devices, allow_group_crawl, allow_comment_crawl,
                    created_by_account_id, created_at, encrypted_key
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_KEY_COLUMNS}
                """,
                (
                    key_digest,
                    key_version,
                    masked_key,
                    grant.duration.unit,
                    grant.duration.value,
                    grant.monthly_contact_limit,
                    grant.max_devices,
                    grant.allow_group_crawl,
                    grant.allow_comment_crawl,
                    created_by_account_id,
                    now,
                    encrypted_key,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                self._write_audit(
                    cursor,
                    actor_account_id=created_by_account_id,
                    action="license_key_created",
                    target_type="license_key",
                    target_id=str(row[0]),
                    details={
                        "duration_unit": grant.duration.unit,
                        "duration_value": grant.duration.value,
                        "monthly_contact_limit": grant.monthly_contact_limit,
                        "max_devices": grant.max_devices,
                        "allow_group_crawl": grant.allow_group_crawl,
                        "allow_comment_crawl": grant.allow_comment_crawl,
                    },
                    now=now,
                )
        if row is None:
            raise DatabaseError("Database license-key write failed.")
        return self._key(row)

    def redeem(
        self, account_id: int, key_digest: str, now: datetime
    ) -> Subscription:
        with self._connect() as cursor:
            self._lock_account_schedule(cursor, account_id)
            cursor.execute(
                f"""
                SELECT {_KEY_COLUMNS}
                FROM license_keys
                WHERE key_digest = %s
                FOR UPDATE
                """,
                (key_digest,),
            )
            key_row = cursor.fetchone()
            if key_row is None:
                raise InvalidLicenseKey("License key is invalid.")
            key = self._key(key_row)
            if key.status is LicenseKeyStatus.REDEEMED:
                raise LicenseAlreadyRedeemed("License key has already been redeemed.")
            if key.status is not LicenseKeyStatus.AVAILABLE:
                raise InvalidLicenseKey("License key is invalid.")

            cursor.execute(
                """
                SELECT max(ends_at)
                FROM account_subscriptions
                WHERE account_id = %s AND status = 'valid'
                """,
                (account_id,),
            )
            latest_row = cursor.fetchone()
            latest_end = latest_row[0] if latest_row is not None else None
            starts_at = max(now, latest_end) if latest_end is not None else now
            ends_at = add_duration(starts_at, key.grant.duration)
            cursor.execute(
                f"""
                INSERT INTO account_subscriptions (
                    account_id, license_key_id, duration_unit, duration_value,
                    monthly_contact_limit, max_devices, allow_group_crawl,
                    allow_comment_crawl, starts_at, ends_at, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_SUBSCRIPTION_COLUMNS}
                """,
                (
                    account_id,
                    key.id,
                    key.grant.duration.unit,
                    key.grant.duration.value,
                    key.grant.monthly_contact_limit,
                    key.grant.max_devices,
                    key.grant.allow_group_crawl,
                    key.grant.allow_comment_crawl,
                    starts_at,
                    ends_at,
                    now,
                ),
            )
            subscription_row = cursor.fetchone()
            if subscription_row is None:
                raise DatabaseError("Database subscription write failed.")
            cursor.execute(
                """
                UPDATE license_keys
                SET status = 'redeemed', redeemed_by_account_id = %s,
                    redeemed_at = %s
                WHERE id = %s
                """,
                (account_id, now, key.id),
            )
            subscription = self._subscription(subscription_row)
            self._write_audit(
                cursor,
                actor_account_id=account_id,
                action="license_redeemed",
                target_type="license_key",
                target_id=str(key.id),
                details={"subscription_id": subscription.id},
                now=now,
            )
        return subscription

    def effective_entitlements(
        self, account_id: int, now: datetime
    ) -> Entitlements:
        with self._connect() as cursor:
            cursor.execute(
                f"""
                SELECT {_SUBSCRIPTION_COLUMNS}
                FROM account_subscriptions
                WHERE account_id = %s AND status = 'valid'
                  AND starts_at <= %s AND %s < ends_at
                ORDER BY starts_at DESC, id DESC
                LIMIT 1
                """,
                (account_id, now, now),
            )
            row = cursor.fetchone()
            if row is not None:
                subscription = self._subscription(row)
                return Entitlements(
                    monthly_contact_limit=(
                        subscription.grant.monthly_contact_limit
                    ),
                    max_devices=subscription.grant.max_devices,
                    allow_group_crawl=subscription.grant.allow_group_crawl,
                    allow_comment_crawl=(
                        subscription.grant.allow_comment_crawl
                    ),
                    subscription_id=subscription.id,
                    starts_at=subscription.starts_at,
                    ends_at=subscription.ends_at,
                )
            cursor.execute(
                """
                SELECT monthly_contact_limit, max_devices,
                       allow_group_crawl, allow_comment_crawl
                FROM plans
                WHERE code = 'default' AND is_system = true
                """
            )
            default_row = cursor.fetchone()
        if default_row is None:
            raise DatabaseError("Default entitlement plan is unavailable.")
        return Entitlements(
            monthly_contact_limit=default_row[0],
            max_devices=default_row[1],
            allow_group_crawl=default_row[2],
            allow_comment_crawl=default_row[3],
        )

    def list_subscriptions(self, account_id: int) -> tuple[Subscription, ...]:
        with self._connect() as cursor:
            cursor.execute(
                f"""
                SELECT {_SUBSCRIPTION_COLUMNS}
                FROM account_subscriptions
                WHERE account_id = %s
                ORDER BY starts_at, id
                """,
                (account_id,),
            )
            rows = cursor.fetchall()
        return tuple(self._subscription(row) for row in rows)

    def list_keys(
        self, *, limit: int = 100, cursor: int | None = None
    ) -> tuple[LicenseKey, ...]:
        if not 1 <= limit <= 101:
            raise ValueError("license key list limit must be from 1 to 101")
        if cursor is not None and cursor <= 0:
            raise ValueError("license key cursor must be positive")
        with self._connect() as database_cursor:
            if cursor is None:
                database_cursor.execute(
                    f"""
                    SELECT {_KEY_COLUMNS}
                    FROM license_keys
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            else:
                database_cursor.execute(
                    f"""
                    SELECT {_KEY_COLUMNS}
                    FROM license_keys
                    WHERE id < %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (cursor, limit),
                )
            rows = database_cursor.fetchall()
        return tuple(self._key(row) for row in rows)

    def revoke_key(
        self, key_id: int, actor_account_id: int, now: datetime
    ) -> LicenseKey:
        with self._connect() as cursor:
            cursor.execute(
                f"SELECT {_KEY_COLUMNS} FROM license_keys WHERE id = %s FOR UPDATE",
                (key_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise InvalidLicenseKey("License key is invalid.")
            cursor.execute(
                f"""
                UPDATE license_keys
                SET status = 'revoked', revoked_at = %s
                WHERE id = %s
                RETURNING {_KEY_COLUMNS}
                """,
                (now, key_id),
            )
            updated = cursor.fetchone()
            cursor.execute(
                """
                UPDATE account_subscriptions
                SET status = 'revoked', revoked_at = %s
                WHERE license_key_id = %s AND status = 'valid'
                """,
                (now, key_id),
            )
            self._write_audit(
                cursor,
                actor_account_id=actor_account_id,
                action="license_revoked",
                target_type="license_key",
                target_id=str(key_id),
                details={},
                now=now,
            )
        if updated is None:
            raise DatabaseError("Database license revoke failed.")
        return self._key(updated)

    def start_subscription_now(
        self,
        account_id: int,
        subscription_id: int,
        actor_account_id: int,
        now: datetime,
    ) -> tuple[Subscription, ...]:
        with self._connect() as cursor:
            self._lock_account_schedule(cursor, account_id)
            cursor.execute(
                f"""
                SELECT {_SUBSCRIPTION_COLUMNS}
                FROM account_subscriptions
                WHERE account_id = %s AND status = 'valid'
                ORDER BY starts_at, id
                FOR UPDATE
                """,
                (account_id,),
            )
            schedule = [self._subscription(row) for row in cursor.fetchall()]
            scheduled = [item for item in schedule if item.starts_at > now]
            if not scheduled or scheduled[0].id != subscription_id:
                raise LicenseScheduleError(
                    "Only the earliest scheduled subscription can start now."
                )
            for active in (item for item in schedule if item.starts_at <= now < item.ends_at):
                cursor.execute(
                    "UPDATE account_subscriptions SET ends_at = %s WHERE id = %s",
                    (now, active.id),
                )
            shifted: list[Subscription] = []
            next_start = now
            begin = schedule.index(scheduled[0])
            for item in schedule[begin:]:
                next_end = add_duration(next_start, item.grant.duration)
                cursor.execute(
                    f"""
                    UPDATE account_subscriptions
                    SET starts_at = %s, ends_at = %s
                    WHERE id = %s
                    RETURNING {_SUBSCRIPTION_COLUMNS}
                    """,
                    (next_start, next_end, item.id),
                )
                updated = cursor.fetchone()
                if updated is None:
                    raise DatabaseError("Database schedule update failed.")
                shifted.append(self._subscription(updated))
                next_start = next_end
            self._write_audit(
                cursor,
                actor_account_id=actor_account_id,
                action="subscription_started_now",
                target_type="subscription",
                target_id=str(subscription_id),
                details={"shifted_subscription_ids": [item.id for item in shifted]},
                now=now,
            )
        return tuple(shifted)

    def get_key(self, key_id: int) -> LicenseKey:
        with self._connect() as cursor:
            cursor.execute(
                f"SELECT {_KEY_COLUMNS} FROM license_keys WHERE id = %s",
                (key_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise InvalidLicenseKey("License key not found.")
        return self._key(row)

    def write_audit(
        self,
        *,
        actor_account_id: int | None,
        action: str,
        target_type: str,
        target_id: str,
        details: dict[str, object],
        now: datetime,
    ) -> None:
        with self._connect() as cursor:
            self._write_audit(
                cursor,
                actor_account_id=actor_account_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                details=details,
                now=now,
            )

    def list_audit_events(
        self, *, limit: int = 100, cursor: int | None = None
    ) -> tuple[AdminAuditEvent, ...]:
        if not 1 <= limit <= 101:
            raise ValueError("audit event list limit must be from 1 to 101")
        if cursor is not None and cursor <= 0:
            raise ValueError("audit event cursor must be positive")
        with self._connect() as database_cursor:
            if cursor is None:
                database_cursor.execute(
                    """
                    SELECT id, actor_account_id, action, target_type,
                           target_id, details, created_at
                    FROM admin_audit_events
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            else:
                database_cursor.execute(
                    """
                    SELECT id, actor_account_id, action, target_type,
                           target_id, details, created_at
                    FROM admin_audit_events
                    WHERE id < %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (cursor, limit),
                )
            rows = database_cursor.fetchall()
        return tuple(
            AdminAuditEvent(
                id=row[0],
                actor_account_id=row[1],
                action=row[2],
                target_type=row[3],
                target_id=row[4],
                details=dict(row[5]),
                created_at=row[6],
            )
            for row in rows
        )

    @staticmethod
    def _lock_account_schedule(cursor: Any, account_id: int) -> None:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (account_id,))

    @staticmethod
    def _write_audit(
        cursor: Any,
        *,
        actor_account_id: int | None,
        action: str,
        target_type: str,
        target_id: str,
        details: dict[str, object],
        now: datetime,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO admin_audit_events (
                actor_account_id, action, target_type,
                target_id, details, created_at
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
    def _key(row: tuple[Any, ...]) -> LicenseKey:
        return LicenseKey(
            id=row[0],
            key_digest=row[1],
            key_version=row[2],
            masked_key=row[3],
            grant=LicenseGrant(
                LicenseDuration(row[4], row[5]),
                row[6],
                row[7],
                row[8],
                row[9],
            ),
            status=LicenseKeyStatus(row[10]),
            created_by_account_id=row[11],
            redeemed_by_account_id=row[12],
            redeemed_at=row[13],
            created_at=row[14],
            revoked_at=row[15],
            encrypted_key=row[16],
        )

    @staticmethod
    def _subscription(row: tuple[Any, ...]) -> Subscription:
        return Subscription(
            id=row[0],
            account_id=row[1],
            license_key_id=row[2],
            grant=LicenseGrant(
                LicenseDuration(row[3], row[4]),
                row[5],
                row[6],
                row[7],
                row[8],
            ),
            starts_at=row[9],
            ends_at=row[10],
            status=SubscriptionStatus(row[11]),
            revoked_at=row[12],
            created_at=row[13],
        )
