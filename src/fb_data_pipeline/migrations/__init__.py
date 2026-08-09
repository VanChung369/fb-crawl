from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    sql: str
    checksum: str


def load_migrations() -> tuple[Migration, ...]:
    resources = files(__package__)
    migrations: list[Migration] = []
    for resource in sorted(resources.iterdir(), key=lambda item: item.name):
        if resource.suffix != ".sql":
            continue
        sql = resource.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=resource.stem,
                sql=sql,
                checksum=sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(migrations)
