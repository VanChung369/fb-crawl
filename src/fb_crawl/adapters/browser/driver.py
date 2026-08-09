from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from selenium.webdriver.firefox.options import Options

from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    ConfigurationError,
)

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium import webdriver


MAX_PROFILE_CONTENT_WAIT_SECONDS = 15.0

PROFILE_SECTION_LABELS = {
    "directory_personal_details": (
        "basic info",
        "birthday",
        "location",
        "personal details",
        "personal information",
        "status",
        "thong tin ca nhan",
        "thong tin co ban",
    ),
    "directory_links": (
        "address",
        "contact and basic info",
        "contact info",
        "dia chi",
        "dien thoai",
        "lien ket",
        "links",
        "phone",
        "so dien thoai",
        "thong tin lien he",
    ),
    "directory_work": (
        "college",
        "cong viec",
        "education",
        "hoc van",
        "school",
        "work",
    ),
}

PROFILE_CONTENT_READY_SCRIPT = r"""
const labels = arguments[0];
const section = arguments[1];
const fold = value => (value || "")
  .normalize("NFD")
  .replace(/[\u0300-\u036f]/g, "")
  .toLowerCase()
  .replace(/\s+/g, " ")
  .trim();
const headings = Array.from(document.querySelectorAll("h1,h2,h3,h4"));
const hasHeading = headings.some(
  heading => labels.some(label => fold(heading.textContent).includes(label))
);
const hasItem = Boolean(
  document.querySelector('[role="list"] [role="listitem"]')
);
if (section === "directory_personal_details") {
  const text = fold(document.body ? document.body.innerText : "");
  const fieldLabels = [
    "birthday", "current city", "hometown", "mobile", "phone",
    "address", "sinh nhat", "thanh pho hien tai", "que quan",
    "dien thoai", "dia chi"
  ];
  return hasHeading && fieldLabels.some(label => text.includes(label));
}
if (section === "directory_links") {
  const facebookHost = host => host === "facebook.com" ||
    host.endsWith(".facebook.com");
  const hasExternalAnchor = Array.from(document.querySelectorAll("a[href]"))
    .some(anchor => {
      try { return !facebookHost(new URL(anchor.href).hostname.toLowerCase()); }
      catch { return false; }
    });
  const domainPattern = /(?:https?:\/\/)?(?:www\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:\/\S*)?/i;
  const hasVisibleDomain = Array.from(document.querySelectorAll('[role="link"]'))
    .some(link => domainPattern.test(link.innerText || ""));
  return hasHeading && (hasExternalAnchor || hasVisibleDomain);
}
return hasHeading && hasItem;
"""


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


def _profile_section(source_url: str) -> str:
    parsed = urlparse(source_url)
    parts = [part for part in parsed.path.split("/") if part]

    if len(parts) == 1 and parts[0].casefold() == "profile.php":
        return parse_qs(parsed.query).get("sk", [""])[0].casefold()

    return parts[-1].casefold() if parts else ""


def wait_for_profile_content(
    browser,
    timeout_seconds: float,
    source_url: str,
) -> bool:
    labels = PROFILE_SECTION_LABELS.get(_profile_section(source_url))
    section = _profile_section(source_url)

    if labels is None:
        return False

    timeout = min(timeout_seconds, MAX_PROFILE_CONTENT_WAIT_SECONDS)

    try:
        browser.execute_script(
            "window.scrollTo(0, Math.min(document.body.scrollHeight, 1200))"
        )
        WebDriverWait(browser, timeout).until(
            lambda driver: bool(
                driver.execute_script(
                    PROFILE_CONTENT_READY_SCRIPT,
                    labels,
                    section,
                )
            )
        )

    except TimeoutException:
        return False

    except WebDriverException as error:
        raise BrowserNavigationError(
            "Facebook profile content readiness failed."
        ) from error

    return True


def create_firefox_driver(
    settings: BrowserSettings,
):
    try:
        return webdriver.Firefox(options=build_firefox_options(settings))

    except WebDriverException as error:
        raise ConfigurationError(
            "Could not start Firefox. " "Install Firefox and the browser extra."
        ) from error
