import pytest

from fb_crawl.adapters.browser.driver import (
    build_firefox_options,
    wait_for_document_ready,
)
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import ConfigurationError


def test_firefox_options_include_headless_window_and_http_proxy() -> None:
    options = build_firefox_options(
        BrowserSettings(
            headless=True,
            proxy="http://127.0.0.1:8080",
        )
    )

    assert "-headless" in options.arguments
    assert "--width=1920" in options.arguments
    assert "--height=1080" in options.arguments

    assert options.preferences["network.proxy.type"] == 1

    assert options.preferences["network.proxy.http"] == "127.0.0.1"

    assert options.preferences["network.proxy.http_port"] == 8080


def test_firefox_options_include_socks_proxy_version() -> None:
    options = build_firefox_options(BrowserSettings(proxy="socks5://127.0.0.1:9050"))

    assert options.preferences["network.proxy.socks"] == "127.0.0.1"

    assert options.preferences["network.proxy.socks_port"] == 9050

    assert options.preferences["network.proxy.socks_version"] == 5


@pytest.mark.parametrize(
    "proxy",
    [
        "ftp://127.0.0.1:21",
        "http://user:secret@127.0.0.1:8080",
        "http://missing-port",
    ],
)
def test_firefox_options_reject_invalid_proxy(
    proxy: str,
) -> None:
    with pytest.raises(
        ConfigurationError,
        match="proxy",
    ):
        build_firefox_options(BrowserSettings(proxy=proxy))


def test_document_ready_wait_uses_explicit_timeout(
    monkeypatch,
) -> None:
    observed: list[float] = []

    class FakeWait:
        def __init__(
            self,
            browser,
            timeout: float,
        ) -> None:
            observed.append(timeout)

        def until(self, predicate) -> None:
            browser = type(
                "Browser",
                (),
                {
                    "execute_script": (lambda self, script: "complete"),
                },
            )()

            assert predicate(browser)

    monkeypatch.setattr(
        "fb_crawl.adapters.browser.driver.WebDriverWait",
        FakeWait,
    )

    wait_for_document_ready(
        object(),
        12.5,
    )

    assert observed == [12.5]
