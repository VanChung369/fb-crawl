from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from fb_data_pipeline.core.models import FacebookIdentity, ProviderResult, ProviderStatus
from fb_data_pipeline.repositories.errors import DatabaseError


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider_name: str
    configured: bool
    last_success_at: datetime | None
    safe_error_code: str
    updated_at: datetime


class PostgresProviderHealthRepository:
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
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error

    def record(
        self,
        *,
        provider_name: str,
        configured: bool,
        success_at: datetime | None,
        safe_error_code: str,
        updated_at: datetime,
    ) -> None:
        safe_code = safe_error_code[:128]
        with self._connect() as cursor:
            cursor.execute(
                """
                INSERT INTO provider_health (
                    provider_name, configured, last_success_at,
                    safe_error_code, updated_at
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (provider_name) DO UPDATE SET
                    configured = EXCLUDED.configured,
                    last_success_at = COALESCE(
                        EXCLUDED.last_success_at,
                        provider_health.last_success_at
                    ),
                    safe_error_code = EXCLUDED.safe_error_code,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    provider_name,
                    configured,
                    success_at,
                    safe_code,
                    updated_at,
                ),
            )

    def get(self, provider_name: str) -> ProviderHealth | None:
        with self._connect() as cursor:
            cursor.execute(
                """
                SELECT provider_name, configured, last_success_at,
                       safe_error_code, updated_at
                FROM provider_health
                WHERE provider_name = %s
                """,
                (provider_name,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return ProviderHealth(
            provider_name=str(row[0]),
            configured=bool(row[1]),
            last_success_at=row[2],
            safe_error_code=str(row[3] or ""),
            updated_at=row[4],
        )


class ObservedPhoneProvider:
    name = "fbnumber"

    def __init__(self, provider, repository, *, configured: bool) -> None:
        self.provider = provider
        self.repository = repository
        self.configured = configured
        self.name = provider.name

    def search(self, identity: FacebookIdentity) -> ProviderResult:
        result = self.provider.search(identity)
        success = result.status in {ProviderStatus.FOUND, ProviderStatus.NOT_FOUND}
        self.repository.record(
            provider_name=self.name,
            configured=self.configured,
            success_at=result.checked_at if success else None,
            safe_error_code="" if success else result.error_code,
            updated_at=result.checked_at,
        )
        return result

    def close(self) -> None:
        self.provider.close()
