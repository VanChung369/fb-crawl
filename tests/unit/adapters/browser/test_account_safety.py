from __future__ import annotations

import pytest

from fb_crawl.adapters.browser.account_safety import (
    SafetyCode,
    SafetySignal,
    classify_account_safety,
)


class FakeBrowser:
    def __init__(self, url: str, *, cookies: list[object] | None = None) -> None:
        self.current_url = url
        self._cookies = list(cookies or [])

    def get_cookies(self) -> list[object]:
        return list(self._cookies)


AUTHENTICATED_COOKIE = [{"name": "c_user", "value": "100"}]


@pytest.mark.parametrize(
    ("code", "safe_message", "manual_review"),
    [
        (SafetyCode.SESSION_EXPIRED, "Facebook session is unavailable.", False),
        (SafetyCode.CHECKPOINT, "Facebook checkpoint requires manual review.", True),
        (SafetyCode.TWO_FACTOR, "Facebook two-factor verification requires manual review.", True),
        (SafetyCode.CAPTCHA, "Facebook CAPTCHA requires manual review.", True),
        (SafetyCode.TEMPORARY_BLOCK, "Facebook temporarily blocked requests.", False),
        (SafetyCode.ACCOUNT_RESTRICTED, "Facebook account restriction requires manual review.", True),
        (SafetyCode.UNUSUAL_ACTIVITY, "Facebook unusual activity requires manual review.", True),
        (SafetyCode.ACCOUNT_RECOVERY, "Facebook account recovery requires manual review.", True),
    ],
)
def test_safety_signal_accepts_only_each_canonical_definition(
    code: SafetyCode,
    safe_message: str,
    manual_review: bool,
) -> None:
    """Break caught: a signal code can carry a mismatched persistence policy."""
    assert SafetySignal(code, safe_message, manual_review).code is code

    with pytest.raises(ValueError):
        SafetySignal(code, "raw page text", manual_review)
    with pytest.raises(ValueError):
        SafetySignal(code, safe_message, not manual_review)


def test_safety_signal_rejects_equality_spoofs_and_boolean_equivalents_before_comparison() -> None:
    """Break caught: an object impersonates canonical signal metadata through equality."""
    class EqualitySpoof:
        def __eq__(self, other: object) -> bool:
            return True

        def __str__(self) -> str:
            return "private browser text"

    with pytest.raises(TypeError):
        SafetySignal(SafetyCode.SESSION_EXPIRED, EqualitySpoof(), False)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        SafetySignal(SafetyCode.SESSION_EXPIRED, "Facebook session is unavailable.", 0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        SafetySignal(0, "Facebook session is unavailable.", False)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("url", "text", "expected"),
    [
        ("https://www.facebook.com/login/?next=%2Fhome.php", "", SafetyCode.SESSION_EXPIRED),
        ("https://www.facebook.com/checkpoint/123", "", SafetyCode.CHECKPOINT),
        ("https://www.facebook.com/two_step_verification/", "", SafetyCode.TWO_FACTOR),
        ("https://www.facebook.com/captcha/", "", SafetyCode.CAPTCHA),
        ("https://www.facebook.com/home.php", "You are temporarily blocked.", SafetyCode.TEMPORARY_BLOCK),
        ("https://www.facebook.com/home.php", "Your account is restricted.", SafetyCode.ACCOUNT_RESTRICTED),
        ("https://www.facebook.com/home.php", "We noticed unusual activity.", SafetyCode.UNUSUAL_ACTIVITY),
        ("https://www.facebook.com/recover/initiate/", "", SafetyCode.ACCOUNT_RECOVERY),
        ("https://www.facebook.com/home.php", "hoạt động bất thường", SafetyCode.UNUSUAL_ACTIVITY),
        ("https://www.facebook.com/home.php", "xác nhận đó là bạn", SafetyCode.CHECKPOINT),
        ("https://www.facebook.com/home.php", "tạm thời bị chặn", SafetyCode.TEMPORARY_BLOCK),
    ],
)
def test_classifier_returns_stable_signal_for_canonical_surfaces(
    url: str,
    text: str,
    expected: SafetyCode,
) -> None:
    """Break caught: a known safety surface is treated as ordinary content."""
    signal = classify_account_safety(
        FakeBrowser(url, cookies=AUTHENTICATED_COOKIE),
        text_func=lambda _: text,
    )

    assert signal is not None
    assert signal.code is expected


@pytest.mark.parametrize(
    ("segment", "expected"),
    [
        ("checkpoint.php", SafetyCode.CHECKPOINT),
        ("two_step_verification.php", SafetyCode.TWO_FACTOR),
        ("two_factor.php", SafetyCode.TWO_FACTOR),
        ("captcha.php", SafetyCode.CAPTCHA),
        ("temporary_block.php", SafetyCode.TEMPORARY_BLOCK),
        ("temporarily_blocked.php", SafetyCode.TEMPORARY_BLOCK),
        ("restricted.php", SafetyCode.ACCOUNT_RESTRICTED),
        ("account_restricted.php", SafetyCode.ACCOUNT_RESTRICTED),
        ("recover.php", SafetyCode.ACCOUNT_RECOVERY),
        ("account_recovery.php", SafetyCode.ACCOUNT_RECOVERY),
    ],
)
def test_classifier_recognizes_canonical_safety_php_route_variants(
    segment: str,
    expected: SafetyCode,
) -> None:
    """Break caught: a canonical Facebook safety route is treated as normal content."""
    signal = classify_account_safety(
        FakeBrowser(f"https://www.facebook.com/{segment}", cookies=AUTHENTICATED_COOKIE),
        text_func=lambda _: "ordinary visible content",
    )

    assert signal is not None
    assert signal.code is expected


def test_classifier_prioritizes_restriction_over_generic_rate_limit_text() -> None:
    """Break caught: an account restriction is downgraded to a cooldown signal."""
    signal = classify_account_safety(
        FakeBrowser("https://www.facebook.com/home.php", cookies=AUTHENTICATED_COOKIE),
        text_func=lambda _: "We limit how often you can do certain things. Your account is restricted.",
    )

    assert signal is not None
    assert signal.code is SafetyCode.ACCOUNT_RESTRICTED
    assert signal.manual_review is True


@pytest.mark.parametrize(
    ("url", "cookies", "expected"),
    [
        ("https://www.facebook.com/home.php", AUTHENTICATED_COOKIE, None),
        ("https://www.facebook.com/home.php", [], SafetyCode.SESSION_EXPIRED),
        ("https://www.facebook.com/login.php?next=%2Fhome.php", AUTHENTICATED_COOKIE, SafetyCode.SESSION_EXPIRED),
    ],
)
def test_classifier_requires_authenticated_session_for_normal_content(
    url: str,
    cookies: list[object],
    expected: SafetyCode | None,
) -> None:
    """Break caught: normal-looking content is accepted without an authenticated session."""
    signal = classify_account_safety(
        FakeBrowser(url, cookies=cookies),
        text_func=lambda _: "ordinary visible content",
    )

    if expected is None:
        assert signal is None
    else:
        assert signal is not None
        assert signal.code is expected


def test_classifier_uses_only_facebook_hosts_for_redirect_paths() -> None:
    """Break caught: an external URL can impersonate a Facebook checkpoint path."""
    signal = classify_account_safety(
        FakeBrowser("https://example.test/checkpoint/123", cookies=AUTHENTICATED_COOKIE),
        text_func=lambda _: "ordinary visible content",
    )

    assert signal is not None
    assert signal.code is SafetyCode.SESSION_EXPIRED


def test_classifier_ignores_safety_markers_on_non_facebook_hosts() -> None:
    """Break caught: an external page can synthesize a Facebook account warning."""
    signal = classify_account_safety(
        FakeBrowser("https://example.test/checkpoint/123", cookies=AUTHENTICATED_COOKIE),
        text_func=lambda _: "captcha confirm it's you",
    )

    assert signal is not None
    assert signal.code is SafetyCode.SESSION_EXPIRED


@pytest.mark.parametrize(
    "url",
    [
        "",
        "facebook.com/checkpoint",
        "//www.facebook.com/checkpoint",
        "ftp://www.facebook.com/checkpoint",
        "https://www.facebook.com:invalid/checkpoint",
        "https://[malformed-host",
    ],
)
def test_classifier_fails_closed_for_invalid_current_urls_without_scanning_text(url: str) -> None:
    """Break caught: malformed navigation state is accepted or visible text is read."""
    reads = 0

    def text_func(_: object) -> str:
        nonlocal reads
        reads += 1
        return "captcha"

    signal = classify_account_safety(
        FakeBrowser(url, cookies=AUTHENTICATED_COOKIE),
        text_func=text_func,
    )

    assert signal is not None
    assert signal.code is SafetyCode.SESSION_EXPIRED
    assert reads == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/checkpoint-news",
        "https://www.facebook.com/login-help",
        "https://www.facebook.com/foo/checkpoint",
    ],
)
def test_classifier_rejects_noncanonical_safety_path_prefixes(url: str) -> None:
    """Break caught: unrelated Facebook pages match a safety path prefix."""
    assert classify_account_safety(
        FakeBrowser(url, cookies=AUTHENTICATED_COOKIE),
        text_func=lambda _: "ordinary visible content",
    ) is None


def test_classifier_honors_blank_captcha_query_key() -> None:
    """Break caught: a canonical CAPTCHA query parameter is discarded when blank."""
    signal = classify_account_safety(
        FakeBrowser("https://www.facebook.com/?captcha=", cookies=AUTHENTICATED_COOKIE),
        text_func=lambda _: "ordinary visible content",
    )

    assert signal is not None
    assert signal.code is SafetyCode.CAPTCHA


@pytest.mark.parametrize(
    "text",
    [
        "Please try again later.",
        "Complete the security check.",
        "Account recovery",
    ],
)
def test_classifier_does_not_overclassify_generic_normal_content(text: str) -> None:
    """Break caught: generic normal copy is mistaken for an account warning."""
    assert classify_account_safety(
        FakeBrowser("https://www.facebook.com/home.php", cookies=AUTHENTICATED_COOKIE),
        text_func=lambda _: text,
    ) is None


@pytest.mark.parametrize(
    "text",
    [
        "Facebook has locked your account.\nStart   the account recovery process.",
        "Facebook đã khóa tài khoản của bạn. Hãy bắt đầu quá trình khôi phục tài khoản.",
    ],
)
def test_classifier_detects_specific_account_recovery_warnings(text: str) -> None:
    """Break caught: a concrete recovery warning is not stopped for review."""
    signal = classify_account_safety(
        FakeBrowser("https://www.facebook.com/home.php", cookies=AUTHENTICATED_COOKIE),
        text_func=lambda _: text,
    )

    assert signal is not None
    assert signal.code is SafetyCode.ACCOUNT_RECOVERY


@pytest.mark.parametrize(
    "text",
    [
        "This tutorial explains how to recover a Facebook account.",
        "Hướng dẫn khôi phục tài khoản Facebook.",
    ],
)
def test_classifier_does_not_treat_recovery_tutorials_as_account_warnings(text: str) -> None:
    """Break caught: recovery discussion text falsely blocks an authenticated job."""
    assert classify_account_safety(
        FakeBrowser("https://www.facebook.com/home.php", cookies=AUTHENTICATED_COOKIE),
        text_func=lambda _: text,
    ) is None


def test_classifier_caps_visible_text_before_matching() -> None:
    """Break caught: unbounded visible text changes the classification result."""
    private_page_text = "secret visible text " + ("x" * 100_001)
    signal = classify_account_safety(
        FakeBrowser("https://www.facebook.com/home.php", cookies=AUTHENTICATED_COOKIE),
        text_func=lambda _: private_page_text + " captcha",
    )

    assert signal is None


def test_classifier_never_includes_page_text_in_safe_message() -> None:
    """Break caught: visible page content leaks into a persisted safety event."""
    private_page_text = "private browser content"
    signal = classify_account_safety(
        FakeBrowser("https://www.facebook.com/home.php", cookies=AUTHENTICATED_COOKIE),
        text_func=lambda _: private_page_text + " captcha",
    )

    assert signal is not None
    assert private_page_text not in signal.safe_message


def test_classifier_sanitizes_browser_property_errors_without_leaking_them() -> None:
    """Break caught: a browser failure leaks a driver value through an account signal."""
    class BrokenBrowser:
        @property
        def current_url(self) -> str:
            raise RuntimeError("private driver URL")

        def get_cookies(self) -> list[object]:
            raise RuntimeError("private cookie value")

    signal = classify_account_safety(BrokenBrowser(), text_func=lambda _: "")

    assert signal is not None
    assert signal.code is SafetyCode.SESSION_EXPIRED
    assert "private" not in signal.safe_message


def test_classifier_treats_an_unreadable_current_url_as_a_safe_session_stop() -> None:
    """Break caught: a driver URL error permits authenticated collection to continue."""
    class BrokenUrlBrowser:
        @property
        def current_url(self) -> str:
            raise RuntimeError("private driver URL")

        def get_cookies(self) -> list[object]:
            return AUTHENTICATED_COOKIE

    signal = classify_account_safety(BrokenUrlBrowser(), text_func=lambda _: "")

    assert signal is not None
    assert signal.code is SafetyCode.SESSION_EXPIRED
    assert "private" not in signal.safe_message
