from __future__ import annotations

from urllib.parse import urlparse

from selenium.webdriver.firefox.options import Options

from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    ConfigurationError,
)

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.support.ui import WebDriverWait


def _apply_proxy(
    options: Options,
    value: str,
) -> None:
    parsed = urlparse(value)

    if parsed.username or parsed.password:
        raise ConfigurationError("Authenticated proxy URLs are not supported.")

    try:
        port = parsed.port
    except ValueError as error:
        raise ConfigurationError("The proxy port must be numeric.") from error

    if parsed.hostname is None or port is None:
        raise ConfigurationError("The proxy URL must include a host and port.")

    options.set_preference(
        "network.proxy.type",
        1,
    )

    if parsed.scheme in {"http", "https"}:
        options.set_preference(
            "network.proxy.http",
            parsed.hostname,
        )
        options.set_preference(
            "network.proxy.http_port",
            port,
        )
        options.set_preference(
            "network.proxy.ssl",
            parsed.hostname,
        )
        options.set_preference(
            "network.proxy.ssl_port",
            port,
        )

    elif parsed.scheme in {"socks4", "socks5"}:
        options.set_preference(
            "network.proxy.socks",
            parsed.hostname,
        )
        options.set_preference(
            "network.proxy.socks_port",
            port,
        )
        options.set_preference(
            "network.proxy.socks_version",
            int(parsed.scheme[-1]),
        )
        options.set_preference(
            "network.proxy.socks_remote_dns",
            True,
        )

    else:
        raise ConfigurationError(
            "The proxy scheme must be " "http, https, socks4, or socks5."
        )


def build_firefox_options(
    settings: BrowserSettings,
) -> Options:
    options = Options()

    options.add_argument("--width=1920")
    options.add_argument("--height=1080")

    options.set_preference(
        "dom.webnotifications.enabled",
        False,
    )
    options.set_preference(
        "geo.enabled",
        False,
    )

    if settings.headless:
        options.add_argument("-headless")

    if settings.proxy:
        _apply_proxy(
            options,
            settings.proxy,
        )

    return options


def wait_for_document_ready(
    browser,
    timeout_seconds: float,
) -> None:
    try:
        WebDriverWait(
            browser,
            timeout_seconds,
        ).until(
            lambda driver: driver.execute_script("return document.readyState")
            == "complete"
        )

    except WebDriverException as error:
        raise BrowserNavigationError("Facebook page readiness timed out.") from error
