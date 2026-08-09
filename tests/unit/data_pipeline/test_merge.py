from datetime import UTC, datetime

import pytest

from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    ProfileData,
    UserBundle,
)
from fb_data_pipeline.services.merge import (
    IdentityConflictError,
    merge_bundles,
    merge_evidence,
)


def evidence(
    source: str,
    provider: str = "",
    *,
    confidence: str | None = None,
    captured_at: datetime | None = None,
) -> PhoneEvidence:
    return PhoneEvidence(
        phone_number="0912 345 678",
        normalized_phone="+84912345678",
        source=source,
        source_url="https://www.facebook.com/a",
        confidence=confidence or ("provider" if provider else "profile_field"),
        provider=provider,
        captured_at=captured_at,
    )


def test_provider_value_does_not_overwrite_facebook_evidence() -> None:
    merged = merge_evidence(
        (evidence("facebook:profile_field"),),
        (evidence("external:fbnumber", "fbnumber"),),
    )

    assert len(merged) == 2
    assert {item.provider for item in merged} == {"", "fbnumber"}


def test_duplicate_evidence_keeps_stronger_confidence_and_latest_time() -> None:
    earlier = datetime(2026, 8, 8, tzinfo=UTC)
    later = datetime(2026, 8, 9, tzinfo=UTC)

    merged = merge_evidence(
        (evidence("facebook:post", confidence="weak_pattern", captured_at=earlier),),
        (evidence("facebook:post", confidence="strong_pattern", captured_at=later),),
    )

    assert len(merged) == 1
    assert merged[0].confidence == "strong_pattern"
    assert merged[0].captured_at == later


def test_bundles_join_uid_and_profile_aliases() -> None:
    first = UserBundle(
        identity=FacebookIdentity(
            uid="10001",
            name="A",
            profile_url="https://www.facebook.com/profile.php?id=10001",
        ),
        evidence=(evidence("facebook:intro"),),
    )
    second = UserBundle(
        identity=FacebookIdentity(
            uid="10001",
            username="a.user",
            profile_url="https://www.facebook.com/a.user",
        ),
        evidence=(evidence("facebook:post"),),
    )

    merged = merge_bundles((first, second))

    assert len(merged) == 1
    assert merged[0].identity.username == "a.user"
    assert merged[0].identity.profile_url == "https://www.facebook.com/a.user"
    assert len(merged[0].evidence) == 2


def test_bundles_reject_conflicting_uids_sharing_username() -> None:
    with pytest.raises(IdentityConflictError, match="10001.*10002"):
        merge_bundles(
            (
                UserBundle(FacebookIdentity(uid="10001", username="a.user")),
                UserBundle(FacebookIdentity(uid="10002", username="a.user")),
            )
        )


def test_bundle_merge_uses_newer_non_empty_profile_fields() -> None:
    earlier = datetime(2026, 8, 8, tzinfo=UTC)
    later = datetime(2026, 8, 9, tzinfo=UTC)
    first = UserBundle(
        FacebookIdentity(uid="10001"),
        profile=ProfileData(
            address="Ha Noi",
            birth_date="1990",
            source_url="https://www.facebook.com/a/about/old",
            observed_at=earlier,
        ),
    )
    second = UserBundle(
        FacebookIdentity(uid="10001"),
        profile=ProfileData(
            address="",
            birth_date="12 thang 8, 1990",
            gender="Nam",
            source_url="https://www.facebook.com/a/about/new",
            observed_at=later,
        ),
    )

    profile = merge_bundles((first, second))[0].profile

    assert profile.address == "Ha Noi"
    assert profile.birth_date == "12 thang 8, 1990"
    assert profile.gender == "Nam"
    assert profile.observed_at == later
    assert profile.source_url == "https://www.facebook.com/a/about/new"


def test_bundle_merge_prefers_timestamped_profile_then_first_unknown() -> None:
    timestamped = datetime(2026, 8, 9, tzinfo=UTC)
    unknown_first = UserBundle(
        FacebookIdentity(uid="10001"),
        profile=ProfileData(address="Unknown first"),
    )
    known_second = UserBundle(
        FacebookIdentity(uid="10001"),
        profile=ProfileData(address="Known", observed_at=timestamped),
    )

    known = merge_bundles((unknown_first, known_second))[0].profile

    assert known.address == "Known"
    assert known.observed_at == timestamped

    unknown_second = UserBundle(
        FacebookIdentity(uid="10001"),
        profile=ProfileData(address="Unknown second"),
    )
    unknown = merge_bundles((unknown_first, unknown_second))[0].profile

    assert unknown.address == "Unknown first"
