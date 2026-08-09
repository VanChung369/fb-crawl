from __future__ import annotations


import time
from urllib.parse import urlparse

from collections.abc import Callable

from selenium.webdriver.common.by import By
from selenium.webdriver.support import (
    expected_conditions as EC,
)
from selenium.webdriver.support.ui import WebDriverWait

from fb_crawl.adapters.browser.session import (
    SessionStore,
    is_authenticated,
)
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import SessionError

CredentialsProvider = Callable[
    [],
    tuple[str, str],
]

LoginFunction = Callable[..., None]


LOGIN_URL = "https://www.facebook.com/login"

VERIFICATION_PATHS = (
    "/checkpoint",
    "/two_step_verification",
)


def _needs_manual_verification(
    browser,
) -> bool:
    path = urlparse(str(browser.current_url or "")).path.lower()

    return any(path.startswith(prefix) for prefix in VERIFICATION_PATHS)


def _wait_for_resolution(
    browser,
    timeout_seconds: float,
    *,
    sleep_func: Callable[[float], None],
    monotonic_func: Callable[[], float],
) -> str:
    deadline = monotonic_func() + timeout_seconds

    while monotonic_func() < deadline:
        if is_authenticated(browser):
            return "authenticated"

        if _needs_manual_verification(browser):
            return "verification"

        sleep_func(
            min(
                0.5,
                timeout_seconds,
            )
        )

    if is_authenticated(browser):
        return "authenticated"

    if _needs_manual_verification(browser):
        return "verification"

    return "timeout"


def _wait_until_authenticated(
    browser,
    timeout_seconds: float,
    *,
    sleep_func: Callable[[float], None],
    monotonic_func: Callable[[], float],
) -> bool:
    deadline = monotonic_func() + timeout_seconds

    while monotonic_func() < deadline:
        if is_authenticated(browser):
            return True

        sleep_func(
            min(
                0.5,
                timeout_seconds,
            )
        )

    return is_authenticated(browser)


def _login_flow(
    browser,
    email: str,
    password: str,
    *,
    settings: BrowserSettings,
    sleep_func: Callable[[float], None],
    monotonic_func: Callable[[], float],
    print_func: Callable[[str], None],
    wait_factory,
) -> None:
    browser.get(LOGIN_URL)

    wait = wait_factory(
        browser,
        settings.browser_timeout_seconds,
    )

    email_input = wait.until(
        EC.presence_of_element_located(
            (
                By.NAME,
                "email",
            )
        )
    )

    password_input = wait.until(
        EC.presence_of_element_located(
            (
                By.NAME,
                "pass",
            )
        )
    )

    submit_locator = (
        By.CSS_SELECTOR,
        "form#login_form [role='button']",
    )

    # Find the submit button before filling the inputs, because Facebook may change the DOM after inputting.
    wait.until(EC.element_to_be_clickable(submit_locator))

    email_input.clear()
    email_input.send_keys(email)

    password_input.clear()
    password_input.send_keys(password)

    # Facebook may change the submit button after filling the inputs, so we need to find it again.
    # We wait for it to be clickable again, in case it is not immediately clickable after the DOM change.
    submit = wait.until(EC.element_to_be_clickable(submit_locator))

    submit.click()

    resolution = _wait_for_resolution(
        browser,
        settings.browser_timeout_seconds,
        sleep_func=sleep_func,
        monotonic_func=monotonic_func,
    )

    if resolution == "authenticated":
        return

    if resolution == "timeout":
        raise SessionError(
            "Authenticated login did not " "complete before its timeout."
        )

    if settings.headless:
        raise SessionError(
            "Authenticated login requires "
            "manual verification; rerun "
            "with --no-headless."
        )

    print_func(
        "Complete any Facebook checkpoint "
        "or two-factor step in the open browser; "
        "fb-crawl will wait for the bounded "
        "verification timeout."
    )

    if _wait_until_authenticated(
        browser,
        settings.verification_timeout_seconds,
        sleep_func=sleep_func,
        monotonic_func=monotonic_func,
    ):
        return

    raise SessionError("Authenticated login or manual " "verification timed out.")


def login_to_facebook(
    browser,
    email: str,
    password: str,
    *,
    settings: BrowserSettings,
    sleep_func: Callable[[float], None] = time.sleep,
    monotonic_func: Callable[[], float] = time.monotonic,
    print_func: Callable[[str], None] = print,
    wait_factory=WebDriverWait,
) -> None:
    try:
        _login_flow(
            browser,
            email,
            password,
            settings=settings,
            sleep_func=sleep_func,
            monotonic_func=monotonic_func,
            print_func=print_func,
            wait_factory=wait_factory,
        )

    except SessionError:
        raise

    except Exception as error:
        raise SessionError(
            "Authenticated Facebook login " "could not be completed."
        ) from error


class SessionManager:
    def __init__(
        self,
        store: SessionStore,
        settings: BrowserSettings,
        credentials_provider: CredentialsProvider,
        *,
        login_func: LoginFunction | None = None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._credentials_provider = credentials_provider
        self._login = login_func or login_to_facebook

    def ensure_authenticated(
        self,
        browser,
    ) -> None:
        if is_authenticated(browser):
            return

        if self._store.restore(browser):
            return

        if self._settings.headless:
            raise SessionError(
                "No valid session is available; "
                "run once interactively "
                "with --no-headless."
            )

        email, password = self._credentials_provider()

        email = email.strip()

        if not email or not password:
            raise SessionError(
                "Facebook email and password " "are required for interactive login."
            )

        self._login(
            browser,
            email,
            password,
            settings=self._settings,
        )

        self.assert_authenticated(browser)
        self._store.save(browser)

    def assert_authenticated(
        self,
        browser,
    ) -> None:
        if not is_authenticated(browser):
            raise SessionError(
                "The authenticated Facebook " "session is no longer valid."
            )
