from fb_data_pipeline.core.models import (
    FacebookIdentity,
    PhoneEvidence,
    PhoneSlot,
    UserBundle,
)


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
