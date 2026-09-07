from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator
from uuid import UUID, uuid4

import psycopg

from fb_crawl.product_jobs.models import (
    ProductCrawlJob,
    ProductCrawlOwner,
    ProductCrawlScope,
    ProductCrawlStatus,
)
from fb_data_pipeline.repositories.errors import DatabaseError


_COLUMNS = """
    id, account_id, scope, target_url, max_identities, status,
    discovered_count, processed_count, found_count, not_found_count,
    quota_exceeded_count, safe_error_code, cancel_requested_at,
    created_at, updated_at, completed_at
"""


class PostgresProductCrawlRepository:
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

    def create(
        self,
        account_id: int,
        scope: ProductCrawlScope,
        target_url: str,
        max_identities: int,
        now: datetime,
        *,
        status: ProductCrawlStatus = ProductCrawlStatus.QUEUED,
        safe_error_code: str = "",
    ) -> ProductCrawlJob:
        job_id = uuid4()
        with self._connect() as cursor:
            cursor.execute(
                f"""
                INSERT INTO product_crawl_jobs (
                    id, account_id, scope, target_url, max_identities,
                    status, safe_error_code, created_at, updated_at,
                    completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_COLUMNS}
                """,
                (
                    job_id, account_id, scope.value, target_url,
                    max_identities, status.value, safe_error_code, now, now,
                    now if status in {
                        ProductCrawlStatus.BLOCKED,
                        ProductCrawlStatus.FAILED,
                    } else None,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise DatabaseError("Database product crawl job creation failed.")
        return self._job(row)

    def get(self, account_id: int, job_id: UUID) -> ProductCrawlJob | None:
        with self._connect() as cursor:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM product_crawl_jobs WHERE account_id = %s AND id = %s",
                (account_id, job_id),
            )
            row = cursor.fetchone()
        return self._job(row) if row is not None else None

    def add_child(
        self,
        account_id: int,
        product_job_id: UUID,
        crawl_job_id: UUID,
        action: str,
    ) -> bool:
        with self._connect() as cursor:
            cursor.execute(
                """
                INSERT INTO product_crawl_job_children (
                    product_crawl_job_id, crawl_job_id, action
                )
                SELECT id, %s, %s FROM product_crawl_jobs
                WHERE id = %s AND account_id = %s
                ON CONFLICT DO NOTHING
                """,
                (crawl_job_id, action, product_job_id, account_id),
            )
            return bool(cursor.rowcount and cursor.rowcount > 0)

    def child_ids(self, account_id: int, product_job_id: UUID) -> tuple[UUID, ...]:
        with self._connect() as cursor:
            cursor.execute(
                """
                SELECT children.crawl_job_id
                FROM product_crawl_job_children AS children
                JOIN product_crawl_jobs AS jobs
                  ON jobs.id = children.product_crawl_job_id
                WHERE jobs.account_id = %s AND jobs.id = %s
                ORDER BY children.action, children.crawl_job_id
                """,
                (account_id, product_job_id),
            )
            rows = cursor.fetchall()
        return tuple(row[0] for row in rows)

    def owner_for_child(self, crawl_job_id: UUID) -> ProductCrawlOwner | None:
        with self._connect() as cursor:
            cursor.execute(
                """
                SELECT jobs.id, jobs.account_id, jobs.max_identities
                FROM product_crawl_job_children AS children
                JOIN product_crawl_jobs AS jobs
                  ON jobs.id = children.product_crawl_job_id
                WHERE children.crawl_job_id = %s
                  AND jobs.status NOT IN ('cancelled', 'failed', 'blocked')
                """,
                (crawl_job_id,),
            )
            row = cursor.fetchone()
        return None if row is None else ProductCrawlOwner(
            product_job_id=row[0],  # type: ignore[arg-type]
            account_id=int(row[1]),
            max_identities=int(row[2]),
        )

    def add_result_counts(
        self,
        product_job_id: UUID,
        account_id: int,
        *,
        processed: int,
        found: int,
        not_found: int,
        quota_exceeded: int,
        now: datetime,
    ) -> None:
        with self._connect() as cursor:
            cursor.execute(
                """
                UPDATE product_crawl_jobs
                SET processed_count = LEAST(
                        max_identities, processed_count + %s
                    ),
                    found_count = found_count + %s,
                    not_found_count = not_found_count + %s,
                    quota_exceeded_count = quota_exceeded_count + %s,
                    updated_at = %s
                WHERE id = %s AND account_id = %s
                  AND status NOT IN ('cancelled', 'failed', 'blocked')
                """,
                (
                    processed, found, not_found, quota_exceeded, now,
                    product_job_id, account_id,
                ),
            )

    def request_cancel(
        self, account_id: int, job_id: UUID, now: datetime
    ) -> ProductCrawlJob | None:
        with self._connect() as cursor:
            cursor.execute(
                f"""
                UPDATE product_crawl_jobs
                SET cancel_requested_at = COALESCE(cancel_requested_at, %s),
                    status = CASE
                        WHEN status = 'queued' THEN 'cancelled'
                        ELSE status
                    END,
                    completed_at = CASE
                        WHEN status = 'queued' THEN %s ELSE completed_at
                    END,
                    updated_at = %s
                WHERE account_id = %s AND id = %s
                  AND status IN ('queued', 'running', 'cancelled')
                RETURNING {_COLUMNS}
                """,
                (now, now, now, account_id, job_id),
            )
            row = cursor.fetchone()
        return self._job(row) if row is not None else self.get(account_id, job_id)

    def update_from_children(
        self,
        account_id: int,
        job_id: UUID,
        *,
        status: ProductCrawlStatus,
        discovered_count: int,
        processed_count: int,
        safe_error_code: str,
        now: datetime,
    ) -> ProductCrawlJob | None:
        terminal = status in {
            ProductCrawlStatus.SUCCEEDED,
            ProductCrawlStatus.PARTIAL,
            ProductCrawlStatus.FAILED,
            ProductCrawlStatus.CANCELLED,
            ProductCrawlStatus.BLOCKED,
        }
        with self._connect() as cursor:
            cursor.execute(
                f"""
                UPDATE product_crawl_jobs
                SET status = %s, discovered_count = %s,
                    processed_count = %s, safe_error_code = %s,
                    updated_at = %s,
                    completed_at = CASE WHEN %s THEN %s ELSE NULL END
                WHERE account_id = %s AND id = %s
                RETURNING {_COLUMNS}
                """,
                (
                    status.value, discovered_count, processed_count,
                    safe_error_code, now, terminal, now, account_id, job_id,
                ),
            )
            row = cursor.fetchone()
        return self._job(row) if row is not None else None

    @staticmethod
    def _job(row: tuple[object, ...]) -> ProductCrawlJob:
        return ProductCrawlJob(
            id=row[0],  # type: ignore[arg-type]
            account_id=int(row[1]),
            scope=ProductCrawlScope(str(row[2])),
            target_url=str(row[3]),
            max_identities=int(row[4]),
            status=ProductCrawlStatus(str(row[5])),
            discovered_count=int(row[6]),
            processed_count=int(row[7]),
            found_count=int(row[8]),
            not_found_count=int(row[9]),
            quota_exceeded_count=int(row[10]),
            safe_error_code=str(row[11] or ""),
            cancel_requested_at=row[12],  # type: ignore[arg-type]
            created_at=row[13],  # type: ignore[arg-type]
            updated_at=row[14],  # type: ignore[arg-type]
            completed_at=row[15],  # type: ignore[arg-type]
        )
