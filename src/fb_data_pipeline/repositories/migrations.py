from __future__ import annotations

import psycopg

from fb_data_pipeline.migrations import Migration, load_migrations
from fb_data_pipeline.repositories.errors import DatabaseError, MigrationChecksumError


class MigrationRunner:
    def __init__(
        self,
        database_url: str,
        *,
        connect_factory=psycopg.connect,
        migrations: tuple[Migration, ...] | None = None,
    ) -> None:
        self.database_url = database_url
        self.connect_factory = connect_factory
        self.migrations = (
            load_migrations() if migrations is None else migrations
        )

    def is_applied(self, version: str) -> bool:
        """Read one migration marker without mutating the database schema."""
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM schema_migrations
                            WHERE version = %s
                        )
                        """,
                        (version,),
                    )
                    row = cursor.fetchone()
        except DatabaseError:
            raise
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database readiness check failed.") from error
        if row is None:
            raise DatabaseError("Database readiness check failed.")
        return bool(row[0])

    def apply(self) -> tuple[str, ...]:
        applied: list[str] = []
        with self.connect_factory(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version text PRIMARY KEY,
                        checksum text NOT NULL,
                        applied_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cursor.execute(
                    "SELECT version, checksum FROM schema_migrations"
                )
                existing = dict(cursor.fetchall())
                for migration in self.migrations:
                    previous = existing.get(migration.version)
                    if previous == migration.checksum:
                        continue
                    if previous is not None:
                        raise MigrationChecksumError(
                            "Applied database migration checksum changed: "
                            f"{migration.version}."
                        )
                    cursor.execute(migration.sql)
                    cursor.execute(
                        """
                        INSERT INTO schema_migrations (version, checksum)
                        VALUES (%s, %s)
                        """,
                        (migration.version, migration.checksum),
                    )
                    applied.append(migration.version)
        return tuple(applied)
