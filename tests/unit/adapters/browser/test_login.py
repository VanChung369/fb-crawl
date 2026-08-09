import pytest

from fb_crawl.adapters.browser.login import (
    SessionManager,
    login_to_facebook,
)
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import SessionError


class Browser:
    def __init__(self) -> None:
        self.current_url = "https://www.facebook.com/"
        self.cookies: list[dict[str, str]] = []

    def get_cookies(self):
        return list(self.cookies)


class FakeStore:
    def __init__(
        self,
        restored: bool,
    ) -> None:
        self.restored = restored
        self.saved = 0
        self.restore_calls = 0

    def restore(self, browser) -> bool:
        self.restore_calls += 1
        return self.restored

    def save(self, browser) -> None:
        self.saved += 1


class Element:
    def __init__(
        self,
        on_click=lambda: None,
    ) -> None:
        self.values: list[str] = []
        self.clicks = 0
        self.on_click = on_click

    def clear(self) -> None:
        self.values.clear()

    def send_keys(
        self,
        value: str,
    ) -> None:
        self.values.append(value)

    def click(self) -> None:
        self.clicks += 1
        self.on_click()

    def is_displayed(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True


class LoginBrowser:
    def __init__(self) -> None:
        self.current_url = "https://www.facebook.com/login"

        self.cookies: list[dict[str, str]] = []

        self.elements = {
            ("name", "email"): Element(),
            ("name", "pass"): Element(),
        }

        self.submit_elements = [
            Element(),
            Element(self._complete_login),
        ]

        self.submit_lookups = 0

    def _complete_login(self) -> None:
        self.current_url = "https://www.facebook.com/"

        self.cookies = [
            {
                "name": "c_user",
                "value": "100",
            }
        ]

    def get(self, url: str) -> None:
        self.current_url = url

    def get_cookies(self):
        return list(self.cookies)

    def find_element(
        self,
        by: str,
        value: str,
    ):
        if value == ("form#login_form [role='button']"):
            element = self.submit_elements[self.submit_lookups]

            self.submit_lookups += 1
            return element

        return self.elements[
            (
                by,
                value,
            )
        ]


class ImmediateWait:
    def __init__(
        self,
        browser,
        timeout: float,
    ) -> None:
        self.browser = browser

    def until(self, predicate):
        result = predicate(self.browser)

        assert result

        return result


def test_manager_reuses_restored_session_without_credentials() -> None:
    store = FakeStore(restored=True)

    manager = SessionManager(
        store,
        BrowserSettings(headless=True),
        credentials_provider=lambda: pytest.fail("credentials must not be requested"),
    )

    manager.ensure_authenticated(Browser())

    assert store.saved == 0


def test_manager_reuses_browser_session_without_restoring_cookies() -> None:
    store = FakeStore(restored=False)
    browser = Browser()
    browser.cookies = [{"name": "c_user", "value": "100"}]
    manager = SessionManager(
        store,
        BrowserSettings(headless=True),
        credentials_provider=lambda: pytest.fail("credentials must not be requested"),
    )

    manager.ensure_authenticated(browser)

    assert store.restore_calls == 0


def test_headless_manager_fails_without_prompt_when_restore_fails() -> None:
    manager = SessionManager(
        FakeStore(restored=False),
        BrowserSettings(headless=True),
        credentials_provider=lambda: pytest.fail("headless mode must not prompt"),
    )

    with pytest.raises(
        SessionError,
        match="interactive",
    ):
        manager.ensure_authenticated(Browser())


def test_interactive_manager_logs_in_then_saves() -> None:
    store = FakeStore(restored=False)
    calls: list[tuple[str, str]] = []
    browser = Browser()

    def fake_login(
        browser,
        email: str,
        password: str,
        **kwargs,
    ) -> None:
        calls.append(
            (
                email,
                password,
            )
        )

        browser.cookies = [
            {
                "name": "c_user",
                "value": "100",
            }
        ]

    manager = SessionManager(
        store,
        BrowserSettings(headless=False),
        credentials_provider=lambda: (
            "synthetic@example.test",
            "not-a-real-password",
        ),
        login_func=fake_login,
    )

    manager.ensure_authenticated(browser)

    assert calls == [
        (
            "synthetic@example.test",
            "not-a-real-password",
        )
    ]

    assert store.saved == 1


def test_login_reacquires_submit_after_filling_inputs() -> None:
    browser = LoginBrowser()
    clock = iter([0.0, 0.1])

    login_to_facebook(
        browser,
        "synthetic@example.test",
        "not-a-real-password",
        settings=BrowserSettings(
            browser_timeout_seconds=1.0,
        ),
        sleep_func=lambda seconds: None,
        monotonic_func=clock.__next__,
        print_func=lambda message: None,
        wait_factory=ImmediateWait,
    )

    assert browser.submit_lookups == 2
    assert browser.submit_elements[1].clicks == 1

    assert browser.elements[("name", "email")].values == ["synthetic@example.test"]

    assert browser.elements[("name", "pass")].values == ["not-a-real-password"]


def test_headless_checkpoint_fails_without_manual_polling() -> None:
    browser = LoginBrowser()

    browser.submit_elements[1].on_click = lambda: setattr(
        browser,
        "current_url",
        "https://www.facebook.com/checkpoint/",
    )

    sleeps: list[float] = []
    messages: list[str] = []
    clock = iter([0.0, 0.1])

    with pytest.raises(
        SessionError,
        match="manual verification",
    ) as captured:
        login_to_facebook(
            browser,
            "synthetic@example.test",
            "not-a-real-password",
            settings=BrowserSettings(
                headless=True,
                browser_timeout_seconds=1.0,
            ),
            sleep_func=sleeps.append,
            monotonic_func=clock.__next__,
            print_func=messages.append,
            wait_factory=ImmediateWait,
        )

    assert sleeps == []
    assert messages == []

    safe_output = captured.value.safe_message

    assert "synthetic@example.test" not in safe_output
    assert "not-a-real-password" not in safe_output


def test_interactive_checkpoint_allows_bounded_manual_verification() -> None:
    browser = LoginBrowser()

    browser.submit_elements[1].on_click = lambda: setattr(
        browser,
        "current_url",
        "https://www.facebook.com/checkpoint/",
    )

    messages: list[str] = []
    clock = iter(
        [
            0.0,
            0.1,
            0.2,
            0.3,
            0.4,
        ]
    )

    def sleep_and_complete(
        seconds: float,
    ) -> None:
        browser.current_url = "https://www.facebook.com/"

        browser.cookies = [
            {
                "name": "c_user",
                "value": "100",
            }
        ]

    login_to_facebook(
        browser,
        "synthetic@example.test",
        "not-a-real-password",
        settings=BrowserSettings(
            headless=False,
            browser_timeout_seconds=0.15,
            verification_timeout_seconds=1.0,
        ),
        sleep_func=sleep_and_complete,
        monotonic_func=clock.__next__,
        print_func=messages.append,
        wait_factory=ImmediateWait,
    )

    assert len(messages) == 1
    assert "checkpoint" in messages[0]
    assert browser.cookies[0]["name"] == "c_user"


def test_interactive_checkpoint_stops_at_verification_timeout() -> None:
    browser = LoginBrowser()

    browser.submit_elements[1].on_click = lambda: setattr(
        browser,
        "current_url",
        "https://www.facebook.com/checkpoint/",
    )

    messages: list[str] = []

    clock = iter(
        [
            0.0,
            0.1,
            0.2,
            0.3,
            0.8,
        ]
    )

    with pytest.raises(
        SessionError,
        match="timed out",
    ):
        login_to_facebook(
            browser,
            "synthetic@example.test",
            "not-a-real-password",
            settings=BrowserSettings(
                headless=False,
                browser_timeout_seconds=0.15,
                verification_timeout_seconds=0.5,
            ),
            sleep_func=lambda seconds: None,
            monotonic_func=clock.__next__,
            print_func=messages.append,
            wait_factory=ImmediateWait,
        )

    assert len(messages) == 1
