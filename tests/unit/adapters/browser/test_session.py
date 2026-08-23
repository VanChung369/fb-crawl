import pytest

import os
import stat

import json
from pathlib import Path

from fb_crawl.adapters.browser.session import (
    SessionStore,
    is_authenticated,
)


from fb_crawl.core.exceptions import SessionError
from fb_crawl.services.execution_control import (
    AccountSafetyStop,
    CrawlCancelled,
    JobBudgetReached,
)
from fb_crawl.adapters.browser.account_safety import SafetyCode, SafetySignal


class FakeBrowser:
    def __init__(
        self,
        cookies=None,
        current_url: str = ("https://www.facebook.com/"),
    ) -> None:
        self.cookies = list(cookies or [])
        self.current_url = current_url
        self.added: list[dict[str, object]] = []
        self.visited: list[str] = []
        self.refreshes = 0

    def get(self, url: str) -> None:
        self.current_url = url
        self.visited.append(url)

    def get_cookies(self):
        return list(self.cookies or self.added)

    def add_cookie(self, cookie) -> None:
        self.added.append(cookie)

    def refresh(self) -> None:
        self.refreshes += 1
        self.cookies = list(self.added)


def valid_session_path() -> Path:
    return Path(__file__).parents[3] / "fixtures" / "authenticated" / "session-valid.json"


def test_authentication_requires_c_user_cookie() -> None:
    authenticated = FakeBrowser(
        [
            {
                "name": "c_user",
                "value": "100",
            }
        ]
    )

    anonymous = FakeBrowser([])

    assert is_authenticated(authenticated) is True
    assert is_authenticated(anonymous) is False


@pytest.mark.parametrize(
    "current_url",
    [
        "https://www.facebook.com/login",
        "https://www.facebook.com/checkpoint/123",
        ("https://www.facebook.com/" "two_step_verification/"),
    ],
)
def test_authentication_rejects_verification_routes(
    current_url: str,
) -> None:
    browser = FakeBrowser(
        [
            {
                "name": "c_user",
                "value": "100",
            }
        ],
        current_url=current_url,
    )

    assert is_authenticated(browser) is False


def test_restore_filters_cookie_fields_and_revalidates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.json"

    path.write_text(
        json.dumps(
            [
                {
                    "name": "c_user",
                    "value": "100",
                    "domain": ".facebook.com",
                    "sameSite": "Lax",
                    "unsupported": "secret",
                },
                {
                    "name": 7,
                    "value": "invalid",
                },
            ]
        ),
        encoding="utf-8",
    )

    browser = FakeBrowser()

    assert SessionStore(path).restore(browser) is True

    assert browser.visited == ["https://www.facebook.com/"]

    assert browser.added == [
        {
            "name": "c_user",
            "value": "100",
            "domain": ".facebook.com",
            "sameSite": "Lax",
        }
    ]


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        "{}",
        "[1, null]",
    ],
)
def test_restore_treats_malformed_content_as_unavailable(
    content: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.json"
    path.write_text(
        content,
        encoding="utf-8",
    )

    assert SessionStore(path).restore(FakeBrowser()) is False


def test_save_is_atomic_owner_only_and_requires_authentication(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "session.json"

    authenticated = FakeBrowser(
        [
            {
                "name": "c_user",
                "value": "100",
                "domain": ".facebook.com",
                "sameSite": "Lax",
            }
        ]
    )

    SessionStore(path).save(authenticated)

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload[0]["name"] == "c_user"

    assert not path.with_name("session.json.tmp").exists()

    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    with pytest.raises(
        SessionError,
        match="valid authenticated session",
    ):
        SessionStore(tmp_path / "invalid.json").save(FakeBrowser())


def test_restore_propagates_cancellation_before_home_navigation(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps([{"name": "c_user", "value": "100"}]), encoding="utf-8")

    class Cancelled:
        def is_cancel_requested(self): return True
        def emit(self, event_type, *, counters=None, safe_message=""): return None
        def check_account_safety(self, browser): return None

    browser = FakeBrowser()
    with pytest.raises(CrawlCancelled):
        SessionStore(path, control=Cancelled()).restore(browser)
    assert browser.visited == []


def test_restore_paces_home_and_refresh_then_checks_page_safety(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps([{"name": "c_user", "value": "100"}]), encoding="utf-8")
    calls = []
    class Control:
        def is_cancel_requested(self): return False
        def emit(self, event_type, *, counters=None, safe_message=""): return None
        def check_account_safety(self, browser):
            calls.append((len(browser.added), browser.refreshes))
            return None
    class Pacer:
        def wait(self): calls.append("pace")
    browser = FakeBrowser()
    assert SessionStore(path, control=Control(), navigation_pacer=Pacer()).restore(browser) is True
    assert calls == ["pace", "pace", (1, 1)]


def test_restore_accepts_valid_cookie_when_anonymous_home_starts_on_login(
) -> None:
    """The anonymous landing page must not invalidate freshly installed cookies."""
    path = valid_session_path()
    trace: list[str] = []

    class LoginBeforeRefreshBrowser(FakeBrowser):
        def get(self, url: str) -> None:
            self.visited.append(url)
            self.current_url = "https://www.facebook.com/login"

        def refresh(self) -> None:
            trace.append("refresh")
            super().refresh()
            self.current_url = "https://www.facebook.com/"

    class Control:
        def is_cancel_requested(self):
            trace.append("cooperative")
            return False

        def emit(self, event_type, *, counters=None, safe_message=""):
            return None

        def check_account_safety(self, browser):
            trace.append(f"safety:{browser.current_url}")
            return None

    class Pacer:
        def wait(self):
            trace.append("pace")

    browser = LoginBeforeRefreshBrowser()
    assert SessionStore(
        path,
        control=Control(),
        navigation_pacer=Pacer(),
    ).restore(browser) is True
    assert trace == [
        "cooperative",
        "pace",
        "cooperative",
        "pace",
        "refresh",
        "cooperative",
        "safety:https://www.facebook.com/",
    ]


def test_restore_rechecks_cancellation_immediately_before_refresh(
) -> None:
    class CancelBeforeRefresh:
        def __init__(self, trace) -> None:
            self.cancelled = False
            self.trace = trace

        def is_cancel_requested(self):
            self.trace.append("guard:cancellation")
            return self.cancelled

        def emit(self, event_type, *, counters=None, safe_message=""):
            return None

        def check_account_safety(self, browser):
            self.trace.append("guard:safety")
            return None

    class Pacer:
        def __init__(self, trace) -> None:
            self.trace = trace

        def wait(self):
            self.trace.append("pace")

    path = valid_session_path()
    trace: list[str] = []
    browser = FakeBrowser()
    original_get = browser.get

    def traced_get(url):
        trace.append("home")
        original_get(url)
        control.cancelled = True

    browser.get = traced_get
    control = CancelBeforeRefresh(trace)
    with pytest.raises(CrawlCancelled) as captured:
        SessionStore(
            path,
            control=control,
            navigation_pacer=Pacer(trace),
        ).restore(browser)

    assert captured.value.__cause__ is None
    assert trace == [
        "guard:cancellation",
        "pace",
        "home",
        "guard:cancellation",
    ]
    assert browser.refreshes == 0

def test_restore_propagates_account_safety_stop_after_refresh_unwrapped(
) -> None:
    path = valid_session_path()
    trace: list[str] = []

    stop = AccountSafetyStop(
        SafetySignal(
            SafetyCode.CHECKPOINT,
            "Facebook checkpoint requires manual review.",
            True,
        )
    )

    class StopAfterRefreshSafety:
        def is_cancel_requested(self):
            trace.append("guard:cancellation")
            return False

        def emit(self, event_type, *, counters=None, safe_message=""):
            return None

        def check_account_safety(self, browser):
            trace.append("guard:safety")
            raise stop

    class Pacer:
        def wait(self):
            trace.append("pace")

    browser = FakeBrowser()
    original_get = browser.get

    def traced_get(url):
        trace.append("home")
        original_get(url)

    browser.get = traced_get
    with pytest.raises(type(stop)) as captured:
        SessionStore(
            path,
            control=StopAfterRefreshSafety(),
            navigation_pacer=Pacer(),
        ).restore(browser)

    assert captured.value is stop
    assert type(captured.value) is type(stop)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert trace == [
        "guard:cancellation",
        "pace",
        "home",
        "guard:cancellation",
        "pace",
        "guard:cancellation",
        "guard:safety",
    ]
    assert browser.refreshes == 1


def test_restore_propagates_budget_stop_before_refresh_unwrapped() -> None:
    path = valid_session_path()

    class BudgetAfterCookies:
        def __init__(self) -> None:
            self.checks = 0

        def is_cancel_requested(self):
            self.checks += 1
            if self.checks == 2:
                raise JobBudgetReached()
            return False

        def emit(self, event_type, *, counters=None, safe_message=""):
            return None

        def check_account_safety(self, browser):
            pytest.fail("page safety must not run before refresh")

    browser = FakeBrowser()
    with pytest.raises(JobBudgetReached):
        SessionStore(path, control=BudgetAfterCookies()).restore(browser)
    assert browser.refreshes == 0


def test_restore_does_not_swallow_cooperative_lease_stop_before_refresh() -> None:
    path = valid_session_path()

    class LeaseStop(RuntimeError):
        pass

    stop = LeaseStop("lease is no longer owned")

    class LeaseLostAfterCookies:
        def __init__(self) -> None:
            self.checks = 0

        def is_cancel_requested(self):
            self.checks += 1
            if self.checks == 2:
                raise stop
            return False

        def emit(self, event_type, *, counters=None, safe_message=""):
            return None

        def check_account_safety(self, browser):
            pytest.fail("page safety must not run before refresh")

    browser = FakeBrowser()
    with pytest.raises(LeaseStop) as captured:
        SessionStore(path, control=LeaseLostAfterCookies()).restore(browser)
    assert captured.value is stop
    assert browser.refreshes == 0
