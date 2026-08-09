from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from collections.abc import Mapping, Sequence

from fb_crawl.core.identity import is_suspicious_profile_name
from fb_crawl.core.models import ProfileField
from fb_crawl.core.urls import profile_identity_url


MISSING_FIELD_COLUMNS = {
    "phone": ("phone_numbers",),
    "website": ("website",),
    "address": ("address",),
    "current_city": ("current_city",),
    "hometown": ("hometown",),
    "birth_date": ("birth_date", "birth_year"),
    "birth_year": ("birth_year",),
    "bio": ("bio",),
    "workplace": ("workplace",),
    "education": ("education",),
    "gender": ("gender",),
    "languages": ("languages",),
    "relationship_status": ("relationship_status",),
}
RETRY_FIELD_STATUSES = frozenset(
    {
        "navigation_failed",
        "section_unavailable",
    }
)
TERMINAL_FIELD_STATUSES = frozenset({"found", "not_visible"})
FIELD_STATUS_ALIASES = {
    "birth_year": ProfileField.BIRTH_DATE.value,
}


@dataclass(frozen=True, slots=True)
class PlanCandidate:
    target_url: str
    missing_fields: tuple[str, ...]
    retry_fields: tuple[str, ...]
    reasons: tuple[str, ...]
    repair_identity: bool
    last_enriched_at: str | None


@dataclass(frozen=True, slots=True)
class DataPlanReport:
    input_rows: int
    requested_fields: tuple[str, ...]
    profile_fields: tuple[str, ...]
    cooldown_days: int
    failure_cooldown_days: int
    force: bool
    eligible: int
    selected: int
    limited: int
    repair_candidates: int
    retry_candidates: int
    selected_retry_candidates: int
    skipped_complete: int
    skipped_recent: int
    skipped_recent_failure: int
    skipped_invalid_profile: int
    duplicates_collapsed: int
    field_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class DataPlanResult:
    targets: tuple[str, ...]
    candidates: tuple[PlanCandidate, ...]
    report: DataPlanReport


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _timestamp(value: str | None) -> datetime | None:
    cleaned = _clean(value)

    if not cleaned:
        return None

    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _missing(
    row: Mapping[str, str],
    requested_fields: Sequence[str],
) -> tuple[str, ...]:
    return tuple(
        field
        for field in requested_fields
        if not any(
            _clean(row.get(column))
            for column in MISSING_FIELD_COLUMNS[field]
        )
    )


def _profile_fields(requested_fields: Sequence[str]) -> tuple[str, ...]:
    values = [
        ProfileField.BIRTH_DATE.value
        if field == "birth_year"
        else field
        for field in requested_fields
    ]
    return tuple(dict.fromkeys(values))


def _field_statuses(row: Mapping[str, str]) -> Mapping[str, tuple[str, ...]]:
    result: dict[str, list[str]] = {}

    for item in str(row.get("field_status") or "").split(";"):
        field, separator, status = item.partition("=")

        if not separator:
            continue

        normalized_field = _clean(field).casefold()
        normalized_status = _clean(status).casefold()

        if normalized_field and normalized_status:
            result.setdefault(normalized_field, []).append(normalized_status)

    return {
        field: tuple(dict.fromkeys(statuses))
        for field, statuses in result.items()
    }


def _retry_policy(
    row: Mapping[str, str],
    missing_fields: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    statuses = _field_statuses(row)
    retry_fields: list[str] = []
    reasons: list[str] = []

    for field in missing_fields:
        status_field = FIELD_STATUS_ALIASES.get(field, field)
        field_statuses = statuses.get(status_field, ())

        if TERMINAL_FIELD_STATUSES.intersection(field_statuses):
            continue

        for status in field_statuses:
            if status not in RETRY_FIELD_STATUSES:
                continue

            retry_fields.append(field)
            reasons.append(f"retry:{field}={status}")

    return (
        tuple(dict.fromkeys(retry_fields)),
        tuple(dict.fromkeys(reasons)),
    )


def _merge_candidates(
    first: PlanCandidate,
    later: PlanCandidate,
) -> PlanCandidate:
    first_time = _timestamp(first.last_enriched_at)
    later_time = _timestamp(later.last_enriched_at)
    latest = first
    merge_retry = False

    if later_time is not None and (
        first_time is None or later_time > first_time
    ):
        latest = later
    elif first_time == later_time:
        merge_retry = True

    non_retry_reasons = tuple(
        dict.fromkeys(
            reason
            for reason in (*first.reasons, *later.reasons)
            if not reason.startswith("retry:")
        )
    )
    retry_fields = (
        tuple(dict.fromkeys((*first.retry_fields, *later.retry_fields)))
        if merge_retry
        else latest.retry_fields
    )
    retry_reasons = (
        tuple(
            dict.fromkeys(
                reason
                for reason in (*first.reasons, *later.reasons)
                if reason.startswith("retry:")
            )
        )
        if merge_retry
        else tuple(
            reason
            for reason in latest.reasons
            if reason.startswith("retry:")
        )
    )

    return replace(
        first,
        missing_fields=tuple(
            dict.fromkeys((*first.missing_fields, *later.missing_fields))
        ),
        retry_fields=retry_fields,
        reasons=(*non_retry_reasons, *retry_reasons),
        repair_identity=first.repair_identity or later.repair_identity,
        last_enriched_at=latest.last_enriched_at,
    )


class DataPlanService:
    def run(
        self,
        rows: Sequence[Mapping[str, str]],
        *,
        missing_fields: Sequence[str],
        limit: int,
        cooldown_days: int,
        failure_cooldown_days: int = 1,
        force: bool = False,
        include_repair: bool = True,
        now: datetime | None = None,
    ) -> DataPlanResult:
        requested_fields = tuple(dict.fromkeys(missing_fields))
        unsupported = tuple(
            field
            for field in requested_fields
            if field not in MISSING_FIELD_COLUMNS
        )

        if unsupported:
            raise ValueError(
                "Unsupported missing field: " + ", ".join(unsupported)
            )

        if not requested_fields:
            raise ValueError("At least one missing field is required.")

        if limit <= 0:
            raise ValueError("data plan limit must be greater than 0")

        if cooldown_days < 0:
            raise ValueError("data plan cooldown days must be at least 0")

        if failure_cooldown_days < 0:
            raise ValueError(
                "data plan failure cooldown days must be at least 0"
            )

        current_time = (now or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        )
        cutoff = current_time - timedelta(days=cooldown_days)
        failure_cutoff = current_time - timedelta(
            days=failure_cooldown_days
        )
        candidates_by_url: dict[str, PlanCandidate] = {}
        order: dict[str, int] = {}
        skipped_complete = 0
        skipped_invalid = 0
        duplicates = 0

        for row_index, row in enumerate(rows):
            identity = profile_identity_url(_clean(row.get("profile_url")))

            if identity is None:
                skipped_invalid += 1
                continue

            _, normalized_url = identity
            missing = _missing(row, requested_fields)
            retry_fields, retry_reasons = _retry_policy(row, missing)
            missing_user_id = not _clean(row.get("user_id")).isdigit()
            suspicious_name = is_suspicious_profile_name(
                _clean(row.get("name"))
            )
            repair = include_repair and (
                missing_user_id or suspicious_name
            )

            if not missing and not repair:
                skipped_complete += 1
                continue

            reasons: list[str] = []

            if repair and missing_user_id:
                reasons.append("identity:missing_user_id")

            if repair and suspicious_name:
                reasons.append("identity:suspicious_name")

            reasons.extend(f"missing:{field}" for field in missing)
            reasons.extend(retry_reasons)
            candidate = PlanCandidate(
                target_url=normalized_url,
                missing_fields=missing,
                retry_fields=retry_fields,
                reasons=tuple(reasons),
                repair_identity=repair,
                last_enriched_at=(
                    _clean(row.get("last_enriched_at")) or None
                ),
            )
            existing = candidates_by_url.get(normalized_url)

            if existing is None:
                candidates_by_url[normalized_url] = candidate
                order[normalized_url] = row_index
            else:
                candidates_by_url[normalized_url] = _merge_candidates(
                    existing, candidate
                )
                duplicates += 1

        eligible: list[PlanCandidate] = []
        skipped_recent = 0
        skipped_recent_failure = 0

        for candidate in candidates_by_url.values():
            enriched_at = _timestamp(candidate.last_enriched_at)
            candidate_cutoff = (
                failure_cutoff if candidate.retry_fields else cutoff
            )

            if (
                not force
                and enriched_at is not None
                and enriched_at >= candidate_cutoff
            ):
                skipped_recent += 1

                if candidate.retry_fields:
                    skipped_recent_failure += 1

                continue

            eligible.append(candidate)

        eligible.sort(
            key=lambda candidate: (
                not candidate.repair_identity,
                not bool(candidate.retry_fields),
                -len(candidate.missing_fields),
                _timestamp(candidate.last_enriched_at)
                or datetime.min.replace(tzinfo=timezone.utc),
                order[candidate.target_url],
            )
        )
        selected = tuple(eligible[:limit])
        field_counts = {
            field: sum(
                field in candidate.missing_fields
                for candidate in selected
            )
            for field in requested_fields
        }
        report = DataPlanReport(
            input_rows=len(rows),
            requested_fields=requested_fields,
            profile_fields=_profile_fields(requested_fields),
            cooldown_days=cooldown_days,
            failure_cooldown_days=failure_cooldown_days,
            force=force,
            eligible=len(eligible),
            selected=len(selected),
            limited=max(0, len(eligible) - len(selected)),
            repair_candidates=sum(
                candidate.repair_identity
                for candidate in candidates_by_url.values()
            ),
            retry_candidates=sum(
                bool(candidate.retry_fields)
                for candidate in candidates_by_url.values()
            ),
            selected_retry_candidates=sum(
                bool(candidate.retry_fields) for candidate in selected
            ),
            skipped_complete=skipped_complete,
            skipped_recent=skipped_recent,
            skipped_recent_failure=skipped_recent_failure,
            skipped_invalid_profile=skipped_invalid,
            duplicates_collapsed=duplicates,
            field_counts=field_counts,
        )
        return DataPlanResult(
            targets=tuple(
                f"profile:{candidate.target_url}"
                for candidate in selected
            ),
            candidates=selected,
            report=report,
        )
