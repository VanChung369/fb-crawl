from fb_crawl.core.models import TargetKind
from fb_crawl.core.urls import (
    canonicalize_targets,
    facebook_url_kind,
    normalize_facebook_url,
    normalize_group_url,
)


def test_normalizes_profiles_and_rejects_internal_or_asset_paths() -> None:
    assert (
        normalize_facebook_url(
            "https://m.facebook.com/profile.php?id=100013976614656&sk=about"
        )
        == "https://www.facebook.com/profile.php?id=100013976614656"
    )

    assert (
        normalize_facebook_url("https://www.facebook.com/people/Test/100013976614656")
        == "https://www.facebook.com/profile.php?id=100013976614656"
    )

    assert normalize_facebook_url("https://www.facebook.com/login/") is None

    assert normalize_facebook_url("https://www.facebook.com/video.mpd") is None


def test_group_normalization_is_separate_from_page_targets() -> None:
    assert (
        normalize_group_url("https://m.facebook.com/groups/pythonvn?ref=share")
        == "https://www.facebook.com/groups/pythonvn"
    )

    assert normalize_facebook_url("https://www.facebook.com/groups/pythonvn") is None


def test_canonicalize_targets_filters_kind_deduplicates_and_limits() -> None:
    result = canonicalize_targets(
        [
            "https://facebook.com/example?ref=one",
            "https://www.facebook.com/example#two",
            "https://www.facebook.com/profile.php?id=100013976614656",
        ],
        target=TargetKind.PAGES,
        limit=5,
    )

    assert result == [
        "https://www.facebook.com/example",
    ]

    assert (
        facebook_url_kind("https://www.facebook.com/profile.php?id=100013976614656")
        is TargetKind.PEOPLE
    )


def test_rejects_places_urls_from_page_targets() -> None:
    places_url = (
        "https://www.facebook.com/places/"
        "Hoat-dong-giai-tri-tai-Ha-Noi/106388046062960/"
    )

    assert normalize_facebook_url(places_url) is None

    assert (
        normalize_facebook_url("https://m.facebook.com/places/restaurants/12345/")
        is None
    )

    assert canonicalize_targets(
        [
            places_url,
            "https://www.facebook.com/example",
        ],
        target=TargetKind.PAGES,
        limit=5,
    ) == [
        "https://www.facebook.com/example",
    ]
