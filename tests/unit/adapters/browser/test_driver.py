import pytest

from fb_crawl.adapters.browser.driver import (
    build_firefox_options,
    create_firefox_driver,
    wait_for_document_ready,
    wait_for_profile_content,
)
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import ConfigurationError
from selenium.common.exceptions import TimeoutException, WebDriverException


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


def test_profile_content_wait_scrolls_and_uses_bounded_timeout(monkeypatch) -> None:
    observed_timeouts: list[float] = []
    scripts: list[tuple[str, tuple[object, ...]]] = []

    class Browser:
        def execute_script(self, script: str, *args):
            scripts.append((script, args))
            return True

    browser = Browser()

    class FakeWait:
        def __init__(self, candidate, timeout: float) -> None:
            assert candidate is browser
            observed_timeouts.append(timeout)

        def until(self, predicate) -> None:
            assert predicate(browser) is True

    monkeypatch.setattr(
        "fb_crawl.adapters.browser.driver.WebDriverWait",
        FakeWait,
    )

    assert wait_for_profile_content(
        browser,
        30,
        "https://www.facebook.com/synthetic.user/directory_personal_details",
    ) is True
    assert observed_timeouts == [15.0]
    assert "scrollTo" in scripts[0][0]
    assert scripts[1][1]
    assert "location" in scripts[1][1][0]
    assert scripts[1][1][1] == "directory_personal_details"


def test_profile_content_timeout_is_a_valid_empty_section(monkeypatch) -> None:
    class Browser:
        def execute_script(self, script: str, *args):
            return None

    class FakeWait:
        def __init__(self, browser, timeout: float) -> None:
            pass

        def until(self, predicate) -> None:
            raise TimeoutException()

    monkeypatch.setattr(
        "fb_crawl.adapters.browser.driver.WebDriverWait",
        FakeWait,
    )

    assert wait_for_profile_content(
        Browser(),
        30,
        "https://www.facebook.com/synthetic.user/directory_links",
    ) is False


def test_profile_content_timeout_ignores_unrelated_global_spinner(
    monkeypatch,
) -> None:
    class Browser:
        def execute_script(self, script: str, *args):
            return "progressbar" in script

    class FakeWait:
        def __init__(self, browser, timeout: float) -> None:
            pass

        def until(self, predicate) -> None:
            raise TimeoutException()

    monkeypatch.setattr(
        "fb_crawl.adapters.browser.driver.WebDriverWait",
        FakeWait,
    )

    assert wait_for_profile_content(
        Browser(),
        30,
        "https://www.facebook.com/synthetic.user/directory_personal_details",
    ) is False


def test_create_firefox_driver_uses_generated_options(
    monkeypatch,
) -> None:
    created = object()
    observed = []

    def fake_firefox(*, options):
        observed.append(options)
        return created

    monkeypatch.setattr(
        "fb_crawl.adapters.browser.driver.webdriver.Firefox",
        fake_firefox,
    )

    result = create_firefox_driver(BrowserSettings(headless=True))

    assert result is created
    assert len(observed) == 1
    assert "-headless" in observed[0].arguments


def test_create_firefox_driver_sanitizes_startup_failure(
    monkeypatch,
) -> None:
    def fail_to_start(*, options):
        raise WebDriverException("private driver path")

    monkeypatch.setattr(
        "fb_crawl.adapters.browser.driver.webdriver.Firefox",
        fail_to_start,
    )

    with pytest.raises(
        ConfigurationError,
        match="Could not start Firefox",
    ) as captured:
        create_firefox_driver(BrowserSettings())

    assert "private driver path" not in captured.value.safe_message
