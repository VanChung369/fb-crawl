from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from fb_crawl.core.urls import FACEBOOK_HOSTS, profile_identity_url


CONFIDENCE_SCORE = {
    "": 0,
    "unknown": 0,
    "strong_pattern": 10,
    "profile_field": 20,
}


@dataclass(frozen=True, slots=True)
class SourcePhoneEvidence:
    source_file: str
    line_number: int
    user_id: str
    profile_url: str
    phone_number: str
    source: str
    source_url: str
    captured_at: str
    confidence: str


@dataclass(frozen=True, slots=True)
class PhoneEvidenceMasterRow:
    user_id: str
    profile_url: str
    phone_number: str
    normalized_phone: str
    sources: tuple[str, ...]
    source_urls: tuple[str, ...]
    first_captured_at: str
    last_captured_at: str
    confidence: str
    evidence_count: int
    quality_status: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PhoneEvidenceIssue:
    source_file: str
    line_number: int
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class PhoneEvidenceMergeReport:
    input_files: int
    skipped_files: tuple[str, ...]
    rows_read: int
    valid_rows: int
    records_written: int
    duplicates_merged: int
    invalid_phone_rows: int
    invalid_identity_rows: int
    missing_uid_rows: int
    invalid_source_url_rows: int
    missing_timestamp_rows: int
    invalid_timestamp_rows: int
    identity_conflict_rows: int
    unknown_confidence_rows: int
    coverage: Mapping[str, int]
    source_counts: Mapping[str, int]
    confidence_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class PhoneEvidenceMergeResult:
    rows: tuple[PhoneEvidenceMasterRow, ...]
    issues: tuple[PhoneEvidenceIssue, ...]
    report: PhoneEvidenceMergeReport


@dataclass(frozen=True, slots=True)
class _PreparedEvidence:
    source: SourcePhoneEvidence
    normalized_phone: str
    normalized_profile_url: str
    aliases: tuple[str, ...]
    captured_at: datetime | None
    quality_status: tuple[str, ...]
    source_url_valid: bool


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _country_code(value: str) -> str:
    cleaned = _clean(value)

    if re.fullmatch(r"[1-9]\d{0,2}", cleaned) is None:
        raise ValueError("default country code must contain 1 to 3 digits")

    return cleaned


def normalize_phone(value: str, default_country_code: str = "84") -> str | None:
    raw = _clean(value)
    digits = re.sub(r"\D", "", raw)

    if not digits:
        return None

    country_code = _country_code(default_country_code)
    international = False

    if digits.startswith("00"):
        digits = digits[2:]
        international = True
    elif raw.startswith("+"):
        international = True
    elif digits.startswith("0"):
        digits = f"{country_code}{digits[1:]}"
        international = True
    elif digits.startswith(country_code) and (
        len(digits) - len(country_code) >= 7
    ):
        international = True

    if not 7 <= len(digits) <= 15:
        return None

    if len(set(digits)) == 1:
        return None

    if international and digits.startswith("0"):
        return None

    return f"+{digits}" if international else digits


def _timestamp(value: str) -> tuple[datetime | None, str | None]:
    cleaned = _clean(value)

    if not cleaned:
        return None, "missing_captured_at"

    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None, "invalid_captured_at"

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc), None


def _valid_source_url(value: str) -> bool:
    parsed = urlparse(_clean(value))
    host = parsed.netloc.casefold().split(":")[0]
    return (
        parsed.scheme.casefold() in {"http", "https"}
        and host in FACEBOOK_HOSTS
        and bool(parsed.path.strip("/"))
    )


def _prefer_profile(first: str, later: str) -> str:
    if not first:
        return later

    first_identity = profile_identity_url(first)
    later_identity = profile_identity_url(later)

    if later_identity is None:
        return first

    if first_identity is None:
        return later

    if first_identity[0].isdigit() and not later_identity[0].isdigit():
        return later

    return first


def _ordered(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _numeric_user_id(item: _PreparedEvidence) -> str:
    user_id = _clean(item.source.user_id)

    if user_id.isdigit():
        return user_id

    identity = profile_identity_url(item.normalized_profile_url)

    if identity is not None and identity[0].isdigit():
        return identity[0]

    return ""


def _issue(
    source: SourcePhoneEvidence,
    code: str,
    message: str,
) -> PhoneEvidenceIssue:
    return PhoneEvidenceIssue(
        source_file=source.source_file,
        line_number=source.line_number,
        code=code,
        message=message,
    )


class PhoneEvidenceMergeService:
    def run(
        self,
        rows: Sequence[SourcePhoneEvidence],
        *,
        input_files: int,
        skipped_files: Sequence[str] = (),
        default_country_code: str = "84",
    ) -> PhoneEvidenceMergeResult:
        country_code = _country_code(default_country_code)
        prepared: list[_PreparedEvidence] = []
        issues: list[PhoneEvidenceIssue] = []
        invalid_phone = 0
        invalid_identity = 0
        missing_uid = 0
        invalid_source_url = 0
        missing_timestamp = 0
        invalid_timestamp = 0
        direct_identity_conflicts = 0
        unknown_confidence = 0

        for row in rows:
            normalized_phone = normalize_phone(
                row.phone_number,
                country_code,
            )

            if normalized_phone is None:
                invalid_phone += 1
                issues.append(
                    _issue(row, "invalid_phone", "Phone value is invalid.")
                )
                continue

            user_id = _clean(row.user_id)
            identity = profile_identity_url(_clean(row.profile_url))
            aliases: list[str] = []
            quality: list[str] = []
            identity_conflict = False

            if user_id.isdigit():
                aliases.append(f"uid:{user_id}")
            else:
                missing_uid += 1
                quality.append("missing_uid")

            normalized_profile_url = ""

            if identity is not None:
                profile_id, normalized_profile_url = identity
                identity_conflict = bool(
                    user_id.isdigit()
                    and profile_id.isdigit()
                    and user_id != profile_id
                )

                if not identity_conflict:
                    aliases.append(
                        f"uid:{profile_id}"
                        if profile_id.isdigit()
                        else f"profile:{normalized_profile_url.casefold()}"
                    )
                    aliases.append(
                        f"profile:{normalized_profile_url.casefold()}"
                    )
            elif user_id.isdigit():
                normalized_profile_url = _clean(row.profile_url)
                quality.append("invalid_profile_url")

            if not aliases:
                invalid_identity += 1
                issues.append(
                    _issue(
                        row,
                        "invalid_identity",
                        "Phone evidence has no usable Facebook identity.",
                    )
                )
                continue

            if identity_conflict:
                direct_identity_conflicts += 1
                quality.append("identity_conflict")
                issues.append(
                    _issue(
                        row,
                        "identity_conflict",
                        "User ID conflicts with the profile URL identity.",
                    )
                )

            captured_at, timestamp_issue = _timestamp(row.captured_at)

            if timestamp_issue == "missing_captured_at":
                missing_timestamp += 1
                quality.append(timestamp_issue)
                issues.append(
                    _issue(
                        row,
                        timestamp_issue,
                        "Phone evidence capture time is missing.",
                    )
                )
            elif timestamp_issue == "invalid_captured_at":
                invalid_timestamp += 1
                quality.append(timestamp_issue)
                issues.append(
                    _issue(
                        row,
                        timestamp_issue,
                        "Phone evidence capture time is invalid.",
                    )
                )

            source_url_valid = _valid_source_url(row.source_url)

            if not source_url_valid:
                invalid_source_url += 1
                quality.append("invalid_source_url")
                issues.append(
                    _issue(
                        row,
                        "invalid_source_url",
                        "Phone evidence source URL is invalid.",
                    )
                )

            confidence = _clean(row.confidence).casefold()

            if confidence not in CONFIDENCE_SCORE:
                unknown_confidence += 1
                quality.append("unknown_confidence")
                issues.append(
                    _issue(
                        row,
                        "unknown_confidence",
                        "Phone evidence confidence is unknown.",
                    )
                )

            prepared.append(
                _PreparedEvidence(
                    source=row,
                    normalized_phone=normalized_phone,
                    normalized_profile_url=normalized_profile_url,
                    aliases=_ordered(aliases),
                    captured_at=captured_at,
                    quality_status=_ordered(quality),
                    source_url_valid=source_url_valid,
                )
            )

        parent = list(range(len(prepared)))

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

        for index, item in enumerate(prepared):
            for alias in item.aliases:
                owner = alias_owner.setdefault(alias, index)
                union(owner, index)

        groups: dict[tuple[int, str], list[_PreparedEvidence]] = {}

        for index, item in enumerate(prepared):
            groups.setdefault(
                (find(index), item.normalized_phone),
                [],
            ).append(item)

        master_rows: list[PhoneEvidenceMasterRow] = []
        grouped_identity_conflicts = 0

        for group in groups.values():
            user_ids = _ordered(_numeric_user_id(item) for item in group)
            quality = list(
                _ordered(
                    status
                    for item in group
                    for status in item.quality_status
                )
            )

            if len(user_ids) > 1 and "identity_conflict" not in quality:
                quality.append("identity_conflict")
                grouped_identity_conflicts += 1
                issues.append(
                    _issue(
                        group[0].source,
                        "identity_conflict",
                        "Merged evidence contains conflicting user IDs.",
                    )
                )

            profile_url = ""

            for item in group:
                profile_url = _prefer_profile(
                    profile_url,
                    item.normalized_profile_url,
                )

            captured = sorted(
                item.captured_at
                for item in group
                if item.captured_at is not None
            )
            best_confidence = max(
                (_clean(item.source.confidence).casefold() for item in group),
                key=lambda value: CONFIDENCE_SCORE.get(value, 0),
            )
            master_rows.append(
                PhoneEvidenceMasterRow(
                    user_id=user_ids[0] if user_ids else "",
                    profile_url=profile_url,
                    phone_number=_clean(group[0].source.phone_number),
                    normalized_phone=group[0].normalized_phone,
                    sources=_ordered(
                        _clean(item.source.source) for item in group
                    ),
                    source_urls=_ordered(
                        _clean(item.source.source_url) for item in group
                    ),
                    first_captured_at=(
                        captured[0].isoformat() if captured else ""
                    ),
                    last_captured_at=(
                        captured[-1].isoformat() if captured else ""
                    ),
                    confidence=best_confidence or "unknown",
                    evidence_count=len(group),
                    quality_status=tuple(quality) if quality else ("ok",),
                )
            )

        source_counts: dict[str, int] = {}
        confidence_counts: dict[str, int] = {}

        for item in prepared:
            source = _clean(item.source.source) or "unknown"
            confidence = _clean(item.source.confidence).casefold() or "unknown"
            source_counts[source] = source_counts.get(source, 0) + 1
            confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1

        coverage = {
            "numeric_uid": sum(row.user_id.isdigit() for row in master_rows),
            "profile_url": sum(bool(row.profile_url) for row in master_rows),
            "captured_at": sum(
                bool(row.last_captured_at) for row in master_rows
            ),
            "valid_source_url": sum(
                any(item.source_url_valid for item in group)
                for group in groups.values()
            ),
            "profile_field_confidence": sum(
                row.confidence == "profile_field" for row in master_rows
            ),
        }
        report = PhoneEvidenceMergeReport(
            input_files=input_files,
            skipped_files=tuple(skipped_files),
            rows_read=len(rows),
            valid_rows=len(prepared),
            records_written=len(master_rows),
            duplicates_merged=max(0, len(prepared) - len(master_rows)),
            invalid_phone_rows=invalid_phone,
            invalid_identity_rows=invalid_identity,
            missing_uid_rows=missing_uid,
            invalid_source_url_rows=invalid_source_url,
            missing_timestamp_rows=missing_timestamp,
            invalid_timestamp_rows=invalid_timestamp,
            identity_conflict_rows=(
                direct_identity_conflicts + grouped_identity_conflicts
            ),
            unknown_confidence_rows=unknown_confidence,
            coverage=coverage,
            source_counts=source_counts,
            confidence_counts=confidence_counts,
        )
        return PhoneEvidenceMergeResult(
            rows=tuple(master_rows),
            issues=tuple(issues),
            report=report,
        )
