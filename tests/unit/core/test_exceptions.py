from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    BrowserParseError,
    ExportError,
    FetchError,
    SessionError,
    ValidationError,
)


def test_errors_expose_stable_code_safe_message_and_exit_code() -> None:
    fetch = FetchError(
        "Public fetch failed.",
        target="https://example.test/page",
    )

    assert fetch.code == "public_fetch_failed"
    assert fetch.safe_message == "Public fetch failed."
    assert fetch.target == "https://example.test/page"
    assert fetch.exit_code == 1

    assert ValidationError("Bad input.").exit_code == 2
    assert ExportError("Cannot write.").exit_code == 4


def test_authenticated_errors_have_stable_codes_and_exit_codes() -> None:
    session = SessionError("Session unavailable.")
    navigation = BrowserNavigationError("Navigation failed.")
    parse = BrowserParseError("Parse failed.")

    assert session.code == "authenticated_session_unavailable"
    assert session.exit_code == 3

    assert navigation.code == "authenticated_navigation_failed"
    assert navigation.exit_code == 1

    assert parse.code == "authenticated_parse_failed"
    assert parse.exit_code == 1
