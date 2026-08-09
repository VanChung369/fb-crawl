from __future__ import annotations

import csv
from pathlib import Path

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.models import IdentityRepairResult
from fb_crawl.exporters.atomic import atomic_text_writer


IDENTITY_FIELDS = (
    "identity_status",
    "identity_source",
    "identity_error_code",
    "identity_error_message",
)

REQUIRED_FIELDS = frozenset(
    {
        "user_id",
        "name",
        "username",
        "profile_url",
    }
)


def read_identity_csv(path: Path) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    source = Path(path)

    try:
        with source.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            original_fields = tuple(reader.fieldnames or ())

            if not original_fields:
                raise ValidationError(
                    f"Identity repair input has no CSV header: {source}."
                )

            missing = REQUIRED_FIELDS.difference(original_fields)

            if missing:
                raise ValidationError(
                    "Identity repair input is missing required columns: "
                    f"{', '.join(sorted(missing))}."
                )

            fieldnames = (
                *original_fields,
                *(field for field in IDENTITY_FIELDS if field not in original_fields),
            )
            rows = []

            for raw in reader:
                row = {
                    field: str(raw.get(field) or "")
                    for field in fieldnames
                }
                rows.append(row)

    except ValidationError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise ValidationError(
            f"Cannot read identity repair input {source}."
        ) from error

    return tuple(fieldnames), tuple(rows)


def write_identity_csv(result: IdentityRepairResult, path: Path) -> bool:
    with atomic_text_writer(
        Path(path),
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=result.fieldnames)
        writer.writeheader()
        writer.writerows(result.rows)

    return True
