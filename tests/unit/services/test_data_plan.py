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


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"missing_fields": ("unknown",)}, "Unsupported missing field"),
        ({"limit": 0}, "plan limit"),
        ({"cooldown_days": -1}, "cooldown days"),
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
