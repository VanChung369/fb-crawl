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
    def __init__(
        self,
        outcomes: dict[str, str | Exception],
        redirects: dict[str, str] | None = None,
    ) -> None:
        self.outcomes = outcomes
        self.redirects = redirects or {}
        self.get_calls: list[str] = []
        self.current_url = "https://www.facebook.com/"
        self.page_source = ""

    def get(self, url: str) -> None:
        self.get_calls.append(url)
        outcome = self.outcomes[url]

        if isinstance(outcome, Exception):
            raise outcome

        self.current_url = self.redirects.get(url, url)
        self.page_source = outcome

    def execute_script(self, script: str, *args):
        return True


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
        (
            "https://www.facebook.com/synthetic.user"
            "/directory_personal_details"
        ),
        (
            "https://www.facebook.com/synthetic.user"
            "/directory_links"
        ),
    )


def test_profile_enricher_visits_directory_routes_once_and_merges_details() -> None:
    personal, links = routes()
    browser = Browser({personal: "personal", links: "links"})
    parser = Parser(
        {
            personal: ProfileDetails(
                current_city="Synthetic City",
                birth_date="1990-01-02",
                birth_year=1990,
            ),
            links: ProfileDetails(
                phone_numbers=("+1 202-555-0147",),
                phone_sources=("facebook:profile_contact",),
            ),
        }
    )
    ready_calls: list[tuple[object, float]] = []
    content_calls: list[tuple[object, float, str]] = []
    fields = (
        ProfileField.PHONE,
        ProfileField.BIRTH_DATE,
        ProfileField.WEBSITE,
    )

    details = ProfileEnricher(
        BrowserSettings(browser_timeout_seconds=7),
        parser,  # type: ignore[arg-type]
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: ready_calls.append((browser, timeout)),
        content_ready_func=lambda browser, timeout, route: content_calls.append(
            (browser, timeout, route)
        ),
    ).enrich(browser, record(), fields)

    assert browser.get_calls == [personal, links]
    assert ready_calls == [(browser, 7), (browser, 7)]
    assert content_calls == [
        (browser, 7, personal),
        (browser, 7, links),
    ]
    assert [call[2] for call in parser.calls] == [fields, fields]
    assert details.current_city == "Synthetic City"
    assert details.birth_year == 1990
    assert details.phone_numbers == ("+1 202-555-0147",)


def test_failed_optional_links_route_keeps_personal_details() -> None:
    personal, links = routes()
    browser = Browser({personal: "personal", links: OSError("network")})
    parser = Parser(
        {
            personal: ProfileDetails(address="123 Synthetic Street"),
        }
    )

    details = ProfileEnricher(
        BrowserSettings(),
        parser,  # type: ignore[arg-type]
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
    ).enrich(browser, record(), ())

    assert details.address == "123 Synthetic Street"
    assert browser.get_calls == [personal, links]


def test_website_only_skips_personal_route() -> None:
    _, links = routes()
    browser = Browser({links: "links"})
    parser = Parser(
        {links: ProfileDetails(website="https://profile.example.test")}
    )

    details = ProfileEnricher(
        BrowserSettings(),
        parser,  # type: ignore[arg-type]
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
    ).enrich(browser, record(), (ProfileField.WEBSITE,))

    assert details.website == "https://profile.example.test"
    assert browser.get_calls == [links]


def test_failed_required_personal_route_is_not_hidden_by_loaded_links() -> None:
    personal, links = routes()
    browser = Browser({personal: OSError("network"), links: "links"})
    parser = Parser({links: ProfileDetails()})

    with pytest.raises(BrowserNavigationError, match="profile navigation"):
        ProfileEnricher(
            BrowserSettings(),
            parser,  # type: ignore[arg-type]
            authenticated_func=lambda browser: True,
            ready_func=lambda browser, timeout: None,
        ).enrich(browser, record(), ())

    assert browser.get_calls == [personal, links]


def test_all_navigation_failures_are_sanitized() -> None:
    personal, links = routes()
    browser = Browser(
        {
            personal: OSError("cookie=secret"),
            links: OSError("raw html"),
        }
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
    personal, links = routes()
    browser = Browser({personal: "personal", links: "links"})
    parser = Parser(
        {
            personal: ValueError("raw html"),
            links: ValueError("raw html"),
        }
    )

    with pytest.raises(BrowserParseError, match="profile parsing"):
        ProfileEnricher(
            BrowserSettings(),
            parser,  # type: ignore[arg-type]
            authenticated_func=lambda browser: True,
            ready_func=lambda browser, timeout: None,
        ).enrich(browser, record(), ())


def test_session_loss_stops_before_later_routes() -> None:
    personal, links = routes()
    browser = Browser({personal: "personal", links: "links"})

    with pytest.raises(SessionError):
        ProfileEnricher(
            BrowserSettings(),
            Parser({}),  # type: ignore[arg-type]
            authenticated_func=lambda browser: False,
            ready_func=lambda browser, timeout: None,
        ).enrich(browser, record(), ())

    assert browser.get_calls == [personal]


def test_numeric_profile_switches_to_redirected_vanity_directory_routes() -> None:
    numeric_personal = (
        "https://www.facebook.com/profile.php"
        "?id=123&sk=directory_personal_details"
    )
    vanity_personal, vanity_links = routes()
    browser = Browser(
        {
            numeric_personal: "personal",
            vanity_links: "links",
        },
        redirects={numeric_personal: vanity_personal},
    )
    parser = Parser(
        {
            numeric_personal: ProfileDetails(current_city="Synthetic City"),
            vanity_links: ProfileDetails(),
        }
    )
    numeric_record = UserRecord(
        user_id="123",
        name="Synthetic User",
        profile_url="https://www.facebook.com/profile.php?id=123",
        source="members",
        source_url="https://www.facebook.com/groups/1/members",
    )

    details = ProfileEnricher(
        BrowserSettings(),
        parser,  # type: ignore[arg-type]
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
    ).enrich(browser, numeric_record, ())

    assert browser.get_calls == [numeric_personal, vanity_links]
    assert details.canonical_profile_url == (
        "https://www.facebook.com/synthetic.user"
    )


def test_numeric_profile_can_resolve_vanity_from_canonical_html() -> None:
    numeric_personal = (
        "https://www.facebook.com/profile.php"
        "?id=123&sk=directory_personal_details"
    )
    _, vanity_links = routes()
    canonical_html = """
    <link
      rel="canonical"
      href="https://www.facebook.com/synthetic.user/directory_personal_details"
    >
    """
    browser = Browser(
        {
            numeric_personal: canonical_html,
            vanity_links: "links",
        }
    )
    parser = Parser(
        {
            numeric_personal: ProfileDetails(),
            vanity_links: ProfileDetails(),
        }
    )
    numeric_record = UserRecord(
        user_id="123",
        name="Synthetic User",
        profile_url="https://www.facebook.com/profile.php?id=123",
        source="members",
        source_url="https://www.facebook.com/groups/1/members",
    )

    details = ProfileEnricher(
        BrowserSettings(),
        parser,  # type: ignore[arg-type]
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
    ).enrich(browser, numeric_record, ())

    assert browser.get_calls == [numeric_personal, vanity_links]
    assert details.canonical_profile_url == (
        "https://www.facebook.com/synthetic.user"
    )
