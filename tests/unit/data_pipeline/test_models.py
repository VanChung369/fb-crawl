from datetime import UTC, datetime

import fb_data_pipeline.core.models as pipeline_models
import pytest

from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    PhoneSlot,
    UserBundle,
)


def test_profile_data_cleans_raw_values_and_detects_content() -> None:
    profile = pipeline_models.ProfileData(
        address="  Ha Noi  ",
        birth_date="  12 thang 8, 1990  ",
        gender="  Nam  ",
        source_url="  https://www.facebook.com/a.user/about  ",
    )

    assert profile.address == "Ha Noi"
    assert profile.birth_date == "12 thang 8, 1990"
    assert profile.gender == "Nam"
    assert profile.source_url == "https://www.facebook.com/a.user/about"
    assert profile.is_empty is False


def test_user_bundle_defaults_to_empty_profile_data() -> None:
    bundle = UserBundle(FacebookIdentity(uid="100"))

    assert bundle.profile.is_empty is True


def test_identity_canonicalizes_mobile_and_tracking_profile_urls() -> None:
    vanity = FacebookIdentity(
        profile_url="https://m.facebook.com/a.user/?ref=bookmarks"
    )
    numeric = FacebookIdentity(
        profile_url="https://www.facebook.com/profile.php?id=10001&ref=bookmarks"
    )

    assert vanity.profile_url == "https://www.facebook.com/a.user"
    assert numeric.profile_url == (
        "https://www.facebook.com/profile.php?id=10001"
    )


def test_fbnumber_is_phone_1_and_crawler_is_phone_2() -> None:
    external = PhoneEvidence(
        phone_number="0987 654 321",
        normalized_phone="+84987654321",
        source="external:fbnumber",
        provider="fbnumber",
    )
    visible = PhoneEvidence(
        phone_number="0912 345 678",
        normalized_phone="+84912345678",
        source="facebook:profile_field",
    )
    bundle = UserBundle(
        identity=FacebookIdentity(uid="10001"),
        evidence=(visible, external),
    )

    assert external.slot is PhoneSlot.PHONE_1
    assert visible.slot is PhoneSlot.PHONE_2
    assert bundle.phone_1 == "+84987654321"
    assert bundle.phone_2 == "+84912345678"


def test_missing_source_slot_is_none_without_losing_other_evidence() -> None:
    visible = PhoneEvidence(
        phone_number="0912 345 678",
        normalized_phone="+84912345678",
        source="facebook:post",
    )
    bundle = UserBundle(
        identity=FacebookIdentity(uid="10001"),
        evidence=(visible,),
    )

    assert bundle.phone_1 is None
    assert bundle.phone_2 == "+84912345678"


def test_retry_candidate_converts_to_empty_bundle() -> None:
    checked_at = datetime(2026, 8, 8, tzinfo=UTC)
    identity = FacebookIdentity(
        uid="100",
        username="sample.user",
        name="Sample User",
        profile_url="https://www.facebook.com/sample.user",
    )

    candidate = pipeline_models.RetryCandidate(
        user_id=7,
        identity=identity,
        status=pipeline_models.ProviderStatus.FAILED,
        checked_at=checked_at,
        error_code=" provider_transport_error ",
    )

    assert candidate.to_bundle() == UserBundle(identity=identity)
    assert candidate.user_id == 7
    assert candidate.checked_at == checked_at
    assert candidate.error_code == "provider_transport_error"


@pytest.mark.parametrize(
    ("user_id", "status"),
    [
        (0, pipeline_models.ProviderStatus.FAILED),
        (1, pipeline_models.ProviderStatus.FOUND),
    ],
)
def test_retry_candidate_rejects_invalid_selection_state(
    user_id: int,
    status: pipeline_models.ProviderStatus,
) -> None:
    with pytest.raises(ValueError):
        pipeline_models.RetryCandidate(
            user_id=user_id,
            identity=FacebookIdentity(uid="100"),
            status=status,
            checked_at=datetime(2026, 8, 8, tzinfo=UTC),
        )
