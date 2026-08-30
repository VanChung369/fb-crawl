from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fb_crawl.licenses.models import (
    LicenseDuration,
    LicenseGrant,
    Subscription,
    SubscriptionStatus,
)


NOW = datetime(2026, 8, 30, 8, tzinfo=UTC)


def test_license_grant_and_subscription_are_validated_snapshots() -> None:
    duration = LicenseDuration("month", 3)
    grant = LicenseGrant(duration, 1000, 2, True, False)
    subscription = Subscription(
        id=5,
        account_id=7,
        license_key_id=11,
        grant=grant,
        starts_at=NOW,
        ends_at=NOW + timedelta(days=90),
        status=SubscriptionStatus.VALID,
        revoked_at=None,
        created_at=NOW,
    )

    assert subscription.grant.monthly_contact_limit == 1000
    assert subscription.status is SubscriptionStatus.VALID


@pytest.mark.parametrize(
    "factory",
    [
        lambda: LicenseDuration("day", 0),
        lambda: LicenseDuration("week", 1),
        lambda: LicenseGrant(LicenseDuration("day", 1), -1, 1, False, False),
        lambda: LicenseGrant(LicenseDuration("day", 1), 100, 0, False, False),
    ],
)
def test_license_models_reject_invalid_duration_or_entitlements(factory) -> None:
    with pytest.raises(ValueError):
        factory()
