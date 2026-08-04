from pathlib import Path

import pytest


from fb_crawl.adapters.http.client import CurlHttpClient
from fb_crawl.config import Settings
from fb_crawl.core.exceptions import FetchError


class FakeResponse:
    text = "<html>ok</html>"

    def raise_for_status(self) -> None:
        return None


def test_client_returns_text_and_uses_timeout_and_headers() -> None:
    calls: list[dict[str, object]] = []

    def requester(
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        calls.append(
            {
                "url": url,
                **kwargs,
            }
        )
        return FakeResponse()

    client = CurlHttpClient(
        Settings(
            timeout_seconds=7,
            max_retries=0,
            output_dir=Path("runtime/output"),
        ),
        requester=requester,
        sleep_func=lambda _: None,
    )

    result = client.get_text("https://example.test/page")

    assert result == "<html>ok</html>"
    assert calls[0]["timeout"] == 7
    assert "User-Agent" in calls[0]["headers"]
    assert calls[0]["impersonate"] == "chrome"


def test_client_retries_only_configured_times_and_hides_query() -> None:
    attempts = 0

    def requester(
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("transport detail")

    client = CurlHttpClient(
        Settings(
            timeout_seconds=1,
            max_retries=2,
        ),
        requester=requester,
        sleep_func=lambda _: None,
    )

    with pytest.raises(FetchError) as caught:
        client.get_text("https://example.test/page?token=private")

    assert attempts == 3
    assert "token" not in caught.value.safe_message
    assert caught.value.target == "https://example.test/page"
