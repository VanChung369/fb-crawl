from fb_crawl.core.exceptions import ExportError, FetchError, ValidationError


def test_errors_expose_stable_code_safe_message_and_exit_code() -> None:
    fetch = FetchError("Public fetch failed.", target="https://example.test/page",)

    assert fetch.code == "public_fetch_failed"
    assert fetch.safe_message == "Public fetch failed."
    assert fetch.target == "https://example.test/page"
    assert fetch.exit_code == 1

    assert ValidationError("Bad input.").exit_code == 2
    assert ExportError("Cannot write.").exit_code == 4
