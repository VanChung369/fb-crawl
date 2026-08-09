from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from fb_crawl.core.identity import is_suspicious_profile_name
from fb_crawl.core.urls import normalize_facebook_url, profile_identity_url
from fb_crawl.exporters.schema import UNIFIED_FIELDS


MULTI_VALUE_FIELDS = frozenset(
    {
        "languages",
        "phone_numbers",
        "phone_sources",
        "field_status",
        "field_sources",
        "reaction_types",
        "source",
        "source_url",
        "identity_source",
    }
)
BOOLEAN_FIELDS = frozenset({"commented", "reacted"})
LATEST_FIELDS = frozenset({"last_seen", "last_enriched_at"})
CONFLICT_FIELDS = frozenset(
    {
        "user_id",
        "name",
        "username",
        "page_name",
        "category",
        "website",
        "address",
        "current_city",
        "hometown",
        "birth_date",
        "birth_year",
        "bio",
        "workplace",
        "education",
        "gender",
        "relationship_status",
        "profile_url",
    }
)
QUALITY_FIELDS = (
    "user_id",
    "name",
    "username",
    "phone_numbers",
    "address",
    "current_city",
    "birth_year",
)
STATUS_SCORE = {
    "repaired": 5,
    "verified": 4,
    "collected": 3,
    "merged": 2,
    "": 1,
    "failed": 0,
}


@dataclass(frozen=True, slots=True)
class SourceRow:
    source_file: str
    line_number: int
    values: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class MergeConflict:
    identity: str
    field: str
    kept: str
    alternatives: tuple[str, ...]
    locations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MergeReport:
    input_files: int
    skipped_files: tuple[str, ...]
    rows_read: int
    valid_rows: int
    records_written: int
    duplicates_merged: int
    issue_rows: int
    unidentified_rows: int
    conflicts: int
    repair_candidates: int
    coverage: Mapping[str, int]
    missing: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class DataMergeResult:
    rows: tuple[dict[str, str], ...]
    conflicts: tuple[MergeConflict, ...]
    report: MergeReport


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _aliases(row: Mapping[str, str]) -> tuple[str, ...]:
    aliases: list[str] = []
    user_id = _clean(row.get("user_id"))
    username = _clean(row.get("username"))
    profile_url = _clean(row.get("profile_url"))

    if user_id.isdigit():
        aliases.append(f"uid:{user_id}")
    elif user_id:
        aliases.append(f"username:{user_id.casefold()}")

    identity = profile_identity_url(profile_url)

    if identity is not None:
        profile_id, normalized = identity
        aliases.append(f"profile:{normalized.casefold()}")
        aliases.append(
            f"uid:{profile_id}"
            if profile_id.isdigit()
            else f"username:{profile_id.casefold()}"
        )

    if username:
        aliases.append(f"username:{username.casefold()}")

    return tuple(dict.fromkeys(aliases))


def _split_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(";") if item.strip())


def _merge_multi(field: str, values: Sequence[str]) -> str:
    merged: dict[str, str] = {}

    for value in values:
        for item in _split_values(value):
            key = (
                re.sub(r"\D", "", item)
                if field == "phone_numbers"
                else item.casefold()
            )

            if key:
                merged.setdefault(key, item)

    return "; ".join(merged.values())


def _row_score(row: SourceRow, field: str, value: str) -> tuple[int, int, int]:
    status = _clean(row.values.get("identity_status")).casefold()
    score = STATUS_SCORE.get(status, 1) * 10
    score += not bool(_clean(row.values.get("error_code")))

    if field == "user_id":
        score += 100 * value.isdigit()
    elif field == "name":
        score += 100 * (not is_suspicious_profile_name(value))
    elif field == "profile_url":
        identity = profile_identity_url(value)
        score += 20 * bool(identity and not identity[0].isdigit())
    elif field == "username":
        score += 20

    return score, len(value), -row.line_number


def _choose_scalar(rows: Sequence[SourceRow], field: str) -> str:
    candidates = [
        (row, _clean(row.values.get(field)))
        for row in rows
        if _clean(row.values.get(field))
    ]

    if not candidates:
        return ""

    row, value = max(
        candidates,
        key=lambda item: _row_score(item[0], field, item[1]),
    )
    del row
    return value


def _identity_label(row: Mapping[str, str]) -> str:
    return (
        row.get("user_id")
        or row.get("username")
        or row.get("profile_url")
        or "unknown"
    )


def _merge_group(
    rows: Sequence[SourceRow],
) -> tuple[dict[str, str], tuple[MergeConflict, ...]]:
    merged = {field: "" for field in UNIFIED_FIELDS}

    for field in UNIFIED_FIELDS:
        values = [
            _clean(row.values.get(field))
            for row in rows
            if _clean(row.values.get(field))
        ]

        if field in MULTI_VALUE_FIELDS:
            merged[field] = _merge_multi(field, values)
        elif field in BOOLEAN_FIELDS:
            if any(value.casefold() == "true" for value in values):
                merged[field] = "true"
            elif values:
                merged[field] = "false"
        elif field == "interaction_count":
            numbers = [int(value) for value in values if value.isdigit()]
            merged[field] = str(max(numbers)) if numbers else ""
        elif field == "depth":
            numbers = [int(value) for value in values if value.isdigit()]
            merged[field] = str(min(numbers)) if numbers else ""
        elif field == "first_seen":
            merged[field] = min(values) if values else ""
        elif field in LATEST_FIELDS:
            merged[field] = max(values) if values else ""
        else:
            merged[field] = _choose_scalar(rows, field)

    normalized = normalize_facebook_url(merged["profile_url"])

    if normalized is not None:
        merged["profile_url"] = normalized
        identity = profile_identity_url(normalized)

        if identity is not None:
            profile_id, _ = identity

            if profile_id.isdigit() and not merged["user_id"]:
                merged["user_id"] = profile_id
            elif not profile_id.isdigit() and not merged["username"]:
                merged["username"] = profile_id

    successful = any(
        not _clean(row.values.get("error_code"))
        and not _clean(row.values.get("identity_error_code"))
        for row in rows
    )

    if successful:
        merged["error_code"] = ""
        merged["error_message"] = ""
        merged["identity_error_code"] = ""
        merged["identity_error_message"] = ""

    conflicts: list[MergeConflict] = []
    identity_label = _identity_label(merged)

    for field in CONFLICT_FIELDS:
        unique: dict[str, str] = {}
        locations: list[str] = []

        for row in rows:
            value = _clean(row.values.get(field))

            if value:
                unique.setdefault(value.casefold(), value)
                locations.append(f"{row.source_file}:{row.line_number}")

        if len(unique) > 1:
            kept = merged[field]
            alternatives = tuple(
                value
                for value in unique.values()
                if value.casefold() != kept.casefold()
            )
            conflicts.append(
                MergeConflict(
                    identity=identity_label,
                    field=field,
                    kept=kept,
                    alternatives=alternatives,
                    locations=tuple(dict.fromkeys(locations)),
                )
            )

    return merged, tuple(conflicts)


class DataMergeService:
    def run(
        self,
        source_rows: Sequence[SourceRow],
        *,
        input_files: int,
        skipped_files: Sequence[str] = (),
    ) -> DataMergeResult:
        rows = tuple(source_rows)
        valid: list[SourceRow] = []
        issue_rows = 0
        unidentified_rows = 0

        for source_row in rows:
            if _aliases(source_row.values):
                valid.append(source_row)
            elif _clean(source_row.values.get("error_code")):
                issue_rows += 1
            else:
                unidentified_rows += 1

        parent = list(range(len(valid)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(first: int, later: int) -> None:
            first_root = find(first)
            later_root = find(later)

            if first_root != later_root:
                parent[later_root] = first_root

        alias_owner: dict[str, int] = {}

        for index, source_row in enumerate(valid):
            for alias in _aliases(source_row.values):
                owner = alias_owner.setdefault(alias, index)
                union(owner, index)

        groups: dict[int, list[SourceRow]] = {}

        for index, source_row in enumerate(valid):
            groups.setdefault(find(index), []).append(source_row)

        merged_rows: list[dict[str, str]] = []
        conflicts: list[MergeConflict] = []

        for group in groups.values():
            merged, group_conflicts = _merge_group(group)
            merged_rows.append(merged)
            conflicts.extend(group_conflicts)

        coverage = {
            field: sum(bool(row[field]) for row in merged_rows)
            for field in QUALITY_FIELDS
        }
        missing = {
            field: len(merged_rows) - coverage[field]
            for field in QUALITY_FIELDS
        }
        repair_candidates = sum(
            not row["user_id"].isdigit()
            or is_suspicious_profile_name(row["name"])
            for row in merged_rows
        )
        report = MergeReport(
            input_files=input_files,
            skipped_files=tuple(skipped_files),
            rows_read=len(rows),
            valid_rows=len(valid),
            records_written=len(merged_rows),
            duplicates_merged=max(0, len(valid) - len(merged_rows)),
            issue_rows=issue_rows,
            unidentified_rows=unidentified_rows,
            conflicts=len(conflicts),
            repair_candidates=repair_candidates,
            coverage=coverage,
            missing=missing,
        )
        return DataMergeResult(
            rows=tuple(merged_rows),
            conflicts=tuple(conflicts),
            report=report,
        )
