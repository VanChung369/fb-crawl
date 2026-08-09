import pytest

from fb_crawl.adapters.browser.inspect import BrowserInspector
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import BrowserNavigationError, SessionError


class Browser:
    def get(self, url: str) -> None:
        self.current_url = url

    def execute_script(self, script: str):
        return {
            "document_ready": True,
            "main_found": True,
            "dialog_count": 1,
            "visible_profile_links": 7,
            "message_rows": 0,
            "profile_field_labels": 3,
        }


def test_inspector_returns_only_sanitized_counts_and_booleans() -> None:
    result = BrowserInspector(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
    ).inspect(Browser(), "https://www.facebook.com/synthetic.user")

    assert result.target_action == "profile"
    assert result.session_valid is True
    assert result.visible_profile_links == 7
    assert result.profile_field_labels == 3
    assert not hasattr(result, "html")
    assert not hasattr(result, "text")


def test_inspector_propagates_session_loss() -> None:
    with pytest.raises(SessionError):
        BrowserInspector(
            BrowserSettings(),
            authenticated_func=lambda browser: False,
            ready_func=lambda browser, timeout: None,
        ).inspect(Browser(), "https://www.facebook.com/synthetic.user")


def test_inspector_sanitizes_driver_failure() -> None:
    browser = Browser()
    browser.execute_script = lambda script: (_ for _ in ()).throw(
        RuntimeError("private DOM text")
    )

    with pytest.raises(BrowserNavigationError) as captured:
        BrowserInspector(
            BrowserSettings(),
            authenticated_func=lambda browser: True,
            ready_func=lambda browser, timeout: None,
        ).inspect(browser, "https://www.facebook.com/synthetic.user")

    assert "private DOM text" not in captured.value.safe_message
