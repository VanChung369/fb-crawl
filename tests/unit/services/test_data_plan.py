from datetime import datetime, timezone

import pytest

from fb_crawl.services.data_plan import DataPlanService


NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)


def row(**values: str) -> dict[str, str]:
    result = {
        "user_id": "10001",
        "name": "Example User",
        "username": "example.user",
        "profile_url": "https://www.facebook.com/example.user",
        "phone_numbers": "",
        "address": "",
        "current_city": "",
        "birth_date": "",
        "birth_year": "",
        "field_status": "",
        "last_enriched_at": "",
    }
    result.update(values)
    return result


def test_plan_prioritizes_identity_repair_then_more_missing_fields() -> None:
    result = DataPlanService().run(
        (
            row(
                user_id="",
                name="174 friends",
                username="repair.me",
                profile_url="https://www.facebook.com/repair.me",
                phone_numbers="0912 345 678",
                address="Known address",
            ),
            row(
                user_id="2",
                name="Missing Phone",
                username="missing.phone",
                profile_url="https://www.facebook.com/missing.phone",
                address="Known address",
                current_city="Known city",
                birth_year="1990",
            ),
            row(
                user_id="3",
                name="Missing Many",
                username="missing.many",
                profile_url="https://www.facebook.com/missing.many",
            ),
        ),
        missing_fields=("phone", "address", "current_city", "birth_year"),
        limit=2,
        cooldown_days=30,
        now=NOW,
    )

    assert result.targets == (
        "profile:https://www.facebook.com/repair.me",
        "profile:https://www.facebook.com/missing.many",
    )
    assert result.candidates[0].repair_identity is True
    assert result.candidates[0].reasons == (
        "identity:missing_user_id",
        "identity:suspicious_name",
        "missing:current_city",
        "missing:birth_year",
    )
    assert result.report.selected == 2
    assert result.report.eligible == 3
    assert result.report.limited == 1


def test_plan_applies_cooldown_only_to_records_that_need_work() -> None:
    result = DataPlanService().run(
        (
            row(
                username="recent",
                profile_url="https://www.facebook.com/recent",
                last_enriched_at="2026-08-01T12:00:00+00:00",
            ),
            row(
                username="complete",
                profile_url="https://www.facebook.com/complete",
                phone_numbers="0912 345 678",
                address="Known",
                current_city="Known",
                birth_year="1990",
            ),
        ),
        missing_fields=("phone", "address", "current_city", "birth_year"),
        limit=10,
        cooldown_days=30,
        now=NOW,
    )

    assert result.targets == ()
    assert result.report.skipped_recent == 1
    assert result.report.skipped_complete == 1


def test_force_bypasses_cooldown_and_birth_year_maps_to_birth_date() -> None:
    result = DataPlanService().run(
        (
            row(
                username="recent",
                profile_url="https://www.facebook.com/recent",
                last_enriched_at="2026-08-09T11:00:00Z",
            ),
        ),
        missing_fields=("phone", "birth_year"),
        limit=10,
        cooldown_days=30,
        force=True,
        now=NOW,
    )

    assert result.targets == ("profile:https://www.facebook.com/recent",)
    assert result.report.profile_fields == ("phone", "birth_date")
    assert result.report.skipped_recent == 0


def test_plan_skips_invalid_profiles_and_collapses_duplicate_targets() -> None:
    result = DataPlanService().run(
        (
            row(profile_url="https://example.test/not-facebook"),
            row(profile_url="https://www.facebook.com/same.user"),
            row(
                user_id="",
                username="same.user",
                profile_url="https://facebook.com/same.user?ref=duplicate",
            ),
        ),
        missing_fields=("phone",),
        limit=10,
        cooldown_days=30,
        now=NOW,
    )

    assert result.targets == ("profile:https://www.facebook.com/same.user",)
    assert result.report.skipped_invalid_profile == 1
    assert result.report.duplicates_collapsed == 1


def test_failure_status_uses_short_retry_cooldown() -> None:
    result = DataPlanService().run(
        (
            row(
                user_id="1",
                profile_url="https://www.facebook.com/retry.ready",
                field_status="phone=navigation_failed",
                last_enriched_at="2026-08-07T12:00:00+00:00",
            ),
            row(
                user_id="2",
                profile_url="https://www.facebook.com/not.visible",
                field_status="phone=not_visible",
                last_enriched_at="2026-08-07T12:00:00+00:00",
            ),
            row(
                user_id="3",
                profile_url="https://www.facebook.com/retry.fresh",
                field_status="phone=section_unavailable",
                last_enriched_at="2026-08-09T06:00:00+00:00",
            ),
        ),
        missing_fields=("phone",),
        limit=10,
        cooldown_days=30,
        failure_cooldown_days=1,
        now=NOW,
    )

    assert result.targets == (
        "profile:https://www.facebook.com/retry.ready",
    )
    assert result.candidates[0].retry_fields == ("phone",)
    assert result.candidates[0].reasons == (
        "missing:phone",
        "retry:phone=navigation_failed",
    )
    assert result.report.retry_candidates == 2
    assert result.report.selected_retry_candidates == 1
    assert result.report.skipped_recent == 2
    assert result.report.skipped_recent_failure == 1


def test_retry_candidates_rank_before_ordinary_missing_profiles() -> None:
    result = DataPlanService().run(
        (
            row(
                user_id="1",
                profile_url="https://www.facebook.com/missing.many",
            ),
            row(
                user_id="2",
                profile_url="https://www.facebook.com/retry.one",
                address="Known",
                field_status="phone=section_unavailable",
            ),
        ),
        missing_fields=("phone", "address"),
        limit=1,
        cooldown_days=30,
        now=NOW,
    )

    assert result.targets == (
        "profile:https://www.facebook.com/retry.one",
    )


def test_birth_year_uses_birth_date_field_status_for_retry() -> None:
    result = DataPlanService().run(
        (
            row(
                field_status="birth_date=section_unavailable",
                last_enriched_at="2026-08-07T12:00:00+00:00",
            ),
        ),
        missing_fields=("birth_year",),
        limit=10,
        cooldown_days=30,
        failure_cooldown_days=1,
        now=NOW,
    )

    assert result.candidates[0].retry_fields == ("birth_year",)
    assert "retry:birth_year=section_unavailable" in (
        result.candidates[0].reasons
    )


def test_terminal_status_suppresses_a_stale_failure_status() -> None:
    result = DataPlanService().run(
        (
            row(
                field_status=(
                    "phone=navigation_failed; phone=not_visible"
                ),
                last_enriched_at="2026-08-07T12:00:00+00:00",
            ),
        ),
        missing_fields=("phone",),
        limit=10,
        cooldown_days=30,
        failure_cooldown_days=1,
        now=NOW,
    )

    assert result.targets == ()
    assert result.report.retry_candidates == 0
    assert result.report.skipped_recent == 1
    assert result.report.skipped_recent_failure == 0


def test_newer_duplicate_status_replaces_an_older_retry_policy() -> None:
    result = DataPlanService().run(
        (
            row(
                field_status="phone=navigation_failed",
                last_enriched_at="2026-07-01T12:00:00+00:00",
            ),
            row(
                field_status="phone=not_visible",
                last_enriched_at="2026-08-07T12:00:00+00:00",
            ),
        ),
        missing_fields=("phone",),
        limit=10,
        cooldown_days=30,
        failure_cooldown_days=1,
        now=NOW,
    )

    assert result.targets == ()
    assert result.report.duplicates_collapsed == 1
    assert result.report.retry_candidates == 0
    assert result.report.skipped_recent_failure == 0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"missing_fields": ("unknown",)}, "Unsupported missing field"),
        ({"limit": 0}, "plan limit"),
        ({"cooldown_days": -1}, "cooldown days"),
        ({"failure_cooldown_days": -1}, "failure cooldown days"),
    ],
)
def test_plan_validates_policy(kwargs: dict, message: str) -> None:
    options = {
        "missing_fields": ("phone",),
        "limit": 10,
        "cooldown_days": 30,
        "now": NOW,
    }
    options.update(kwargs)

    with pytest.raises(ValueError, match=message):
        DataPlanService().run((row(),), **options)
