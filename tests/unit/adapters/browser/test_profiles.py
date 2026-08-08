import pytest

from fb_crawl.adapters.browser.profiles import ProfileEnricher
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    BrowserParseError,
    SessionError,
)
from fb_crawl.core.models import ProfileDetails, ProfileField, UserRecord


class Browser:
    def __init__(self, outcomes: dict[str, str | Exception]) -> None:
        self.outcomes = outcomes
        self.get_calls: list[str] = []
        self.current_url = "https://www.facebook.com/"
        self.page_source = ""

    def get(self, url: str) -> None:
        self.get_calls.append(url)
        outcome = self.outcomes[url]

        if isinstance(outcome, Exception):
            raise outcome

        self.current_url = url
        self.page_source = outcome


class Parser:
    def __init__(self, outcomes: dict[str, ProfileDetails | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, str, tuple[ProfileField, ...]]] = []

    def parse(self, html: str, *, source_url: str, requested_fields=()):
        self.calls.append((html, source_url, requested_fields))
        outcome = self.outcomes[source_url]

        if isinstance(outcome, Exception):
            raise outcome

        return outcome


def record() -> UserRecord:
    return UserRecord(
        user_id="synthetic.user",
        name="Synthetic User",
        profile_url="https://www.facebook.com/synthetic.user",
        source="members",
        source_url="https://www.facebook.com/groups/1/members",
    )


def routes() -> tuple[str, str]:
    return (
        "https://www.facebook.com/synthetic.user/about",
        (
            "https://www.facebook.com/synthetic.user"
            "/about_contact_and_basic_info"
        ),
    )


def test_profile_enricher_visits_two_routes_once_and_merges_details() -> None:
    overview, contact = routes()
    browser = Browser({overview: "overview", contact: "contact"})
    parser = Parser(
        {
            overview: ProfileDetails(
                current_city="Synthetic City",
                birth_date="1990-01-02",
                birth_year=1990,
            ),
            contact: ProfileDetails(
                phone_numbers=("+1 202-555-0147",),
                phone_sources=("facebook:profile_contact",),
            ),
        }
    )
    ready_calls: list[tuple[object, float]] = []
    fields = (ProfileField.PHONE, ProfileField.BIRTH_DATE)

    details = ProfileEnricher(
        BrowserSettings(browser_timeout_seconds=7),
        parser,  # type: ignore[arg-type]
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: ready_calls.append((browser, timeout)),
    ).enrich(browser, record(), fields)

    assert browser.get_calls == [overview, contact]
    assert ready_calls == [(browser, 7), (browser, 7)]
    assert [call[2] for call in parser.calls] == [fields, fields]
    assert details.current_city == "Synthetic City"
    assert details.birth_year == 1990
    assert details.phone_numbers == ("+1 202-555-0147",)


def test_one_failed_route_keeps_successful_partial_details() -> None:
    overview, contact = routes()
    browser = Browser({overview: OSError("network"), contact: "contact"})
    parser = Parser({contact: ProfileDetails(address="123 Synthetic Street")})

    details = ProfileEnricher(
        BrowserSettings(),
        parser,  # type: ignore[arg-type]
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
    ).enrich(browser, record(), ())

    assert details.address == "123 Synthetic Street"
    assert browser.get_calls == [overview, contact]


def test_all_navigation_failures_are_sanitized() -> None:
    overview, contact = routes()
    browser = Browser(
        {overview: OSError("cookie=secret"), contact: OSError("raw html")}
    )

    with pytest.raises(BrowserNavigationError, match="profile navigation") as caught:
        ProfileEnricher(
            BrowserSettings(),
            Parser({}),  # type: ignore[arg-type]
            authenticated_func=lambda browser: True,
            ready_func=lambda browser, timeout: None,
        ).enrich(browser, record(), ())

    assert "secret" not in caught.value.safe_message
    assert caught.value.target == record().profile_url


def test_all_parser_failures_raise_safe_parse_error() -> None:
    overview, contact = routes()
    browser = Browser({overview: "overview", contact: "contact"})
    parser = Parser(
        {overview: ValueError("raw html"), contact: ValueError("raw html")}
    )

    with pytest.raises(BrowserParseError, match="profile parsing"):
        ProfileEnricher(
            BrowserSettings(),
            parser,  # type: ignore[arg-type]
            authenticated_func=lambda browser: True,
            ready_func=lambda browser, timeout: None,
        ).enrich(browser, record(), ())


def test_session_loss_stops_before_later_routes() -> None:
    overview, contact = routes()
    browser = Browser({overview: "overview", contact: "contact"})

    with pytest.raises(SessionError):
        ProfileEnricher(
            BrowserSettings(),
            Parser({}),  # type: ignore[arg-type]
            authenticated_func=lambda browser: False,
            ready_func=lambda browser, timeout: None,
        ).enrich(browser, record(), ())

    assert browser.get_calls == [overview]
