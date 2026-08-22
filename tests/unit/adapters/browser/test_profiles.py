import pytest

from fb_crawl.adapters.browser.profiles import ProfileEnricher
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    BrowserParseError,
    SessionError,
)
from fb_crawl.core.models import ProfileDetails, ProfileField, UserRecord
from fb_crawl.services.execution_control import CrawlCancelled
from fb_crawl.services.execution_control import AccountSafetyStop
from fb_crawl.adapters.browser.account_safety import SafetyCode, SafetySignal


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
        self.title = "Facebook"

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


def test_profile_enrichment_cancelled_before_first_route_does_not_navigate() -> None:
    class Cancelled:
        def is_cancel_requested(self): return True
        def emit(self, event_type, *, counters=None, safe_message=""): return None
        def check_account_safety(self, browser): return None
    personal, links = routes()
    browser = Browser({personal: "personal", links: "links"})
    with pytest.raises(CrawlCancelled):
        ProfileEnricher(BrowserSettings(), control=Cancelled()).enrich(browser, record(), (ProfileField.WEBSITE,))
    assert browser.get_calls == []


def test_profile_safety_after_readiness_never_reads_or_parses_source() -> None:
    class Unsafe:
        def is_cancel_requested(self): return False
        def emit(self, event_type, *, counters=None, safe_message=""): return None
        def check_account_safety(self, browser):
            return SafetySignal(SafetyCode.CHECKPOINT, "Facebook checkpoint requires manual review.", True)
    personal, links = routes()
    browser = Browser({personal: "must-not-read", links: "unused"})
    parser = Parser({personal: pytest.fail, links: pytest.fail})
    with pytest.raises(AccountSafetyStop):
        ProfileEnricher(BrowserSettings(), parser, control=Unsafe(), authenticated_func=lambda browser: True,
            ready_func=lambda browser, timeout: None).enrich(browser, record(), (ProfileField.WEBSITE,))
    assert parser.calls == []


def test_profile_readiness_safety_stops_before_page_source_getter_or_parser() -> None:
    class SourceSentinelBrowser:
        title = "Facebook"

        def __init__(self) -> None:
            self.current_url = "https://www.facebook.com/"
            self.get_calls: list[str] = []

        def get(self, url: str) -> None:
            self.get_calls.append(url)
            self.current_url = url

        @property
        def page_source(self):
            pytest.fail("page source must not be read after readiness safety stop")

    class ParserSentinel:
        def parse(self, *args, **kwargs):
            pytest.fail("profile parser must not run after readiness safety stop")

    class Unsafe:
        def is_cancel_requested(self):
            return False

        def emit(self, event_type, *, counters=None, safe_message=""):
            return None

        def check_account_safety(self, browser):
            return SafetySignal(
                SafetyCode.CHECKPOINT,
                "Facebook checkpoint requires manual review.",
                True,
            )

    _, links = routes()
    browser = SourceSentinelBrowser()
    with pytest.raises(AccountSafetyStop):
        ProfileEnricher(
            BrowserSettings(),
            ParserSentinel(),  # type: ignore[arg-type]
            control=Unsafe(),
            authenticated_func=lambda browser: True,
            ready_func=lambda browser, timeout: None,
        ).enrich(browser, record(), (ProfileField.WEBSITE,))

    assert browser.get_calls == [links]


def work_route() -> str:
    return "https://www.facebook.com/synthetic.user/directory_work"


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

    assert browser.get_calls == [
        personal,
        links,
        "https://www.facebook.com/synthetic.user",
    ]
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
    assert browser.get_calls == [
        personal,
        links,
        work_route(),
        "https://www.facebook.com/synthetic.user",
    ]


def test_profile_enricher_uses_sanitized_browser_title_for_missing_name() -> None:
    personal, _ = routes()
    browser = Browser({personal: "personal"})
    browser.title = "(2) Synthetic User | Facebook"
    parser = Parser({personal: ProfileDetails(current_city="Synthetic City")})

    details = ProfileEnricher(
        BrowserSettings(),
        parser,  # type: ignore[arg-type]
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        content_ready_func=lambda browser, timeout, route: True,
    ).enrich(browser, record(), (ProfileField.CURRENT_CITY,))

    assert details.name == "Synthetic User"


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

    assert browser.get_calls == [
        personal,
        links,
        work_route(),
        "https://www.facebook.com/synthetic.user",
    ]


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

    assert browser.get_calls == [
        numeric_personal,
        vanity_links,
        work_route(),
        "https://www.facebook.com/synthetic.user",
    ]
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

    assert browser.get_calls == [
        numeric_personal,
        vanity_links,
        work_route(),
        "https://www.facebook.com/synthetic.user",
    ]
    assert details.canonical_profile_url == (
        "https://www.facebook.com/synthetic.user"
    )


def test_profile_enricher_reports_field_status_and_sources() -> None:
    personal, _ = routes()
    browser = Browser({personal: "personal"})
    parser = Parser(
        {personal: ProfileDetails(current_city="Synthetic City")}
    )

    details = ProfileEnricher(
        BrowserSettings(),
        parser,  # type: ignore[arg-type]
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        content_ready_func=lambda browser, timeout, route: True,
    ).enrich(
        browser,
        record(),
        (ProfileField.CURRENT_CITY, ProfileField.HOMETOWN),
    )

    statuses = dict(details.field_status)
    sources = dict(details.field_sources)
    assert statuses["current_city"] == "found"
    assert statuses["hometown"] == "not_visible"
    assert statuses["website"] == "not_requested"
    assert sources["current_city"] == (
        "facebook:directory_personal_details"
    )


def test_phone_enrichment_also_scans_visible_profile_timeline() -> None:
    personal, _ = routes()
    timeline = "https://www.facebook.com/synthetic.user"
    browser = Browser({personal: "personal", timeline: "timeline"})
    parser = Parser(
        {
            personal: ProfileDetails(),
            timeline: ProfileDetails(
                phone_numbers=("0912 345 678",),
                phone_sources=("facebook:post_text",),
            ),
        }
    )

    details = ProfileEnricher(
        BrowserSettings(),
        parser,  # type: ignore[arg-type]
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        content_ready_func=lambda browser, timeout, route: True,
    ).enrich(browser, record(), (ProfileField.PHONE,))

    assert browser.get_calls == [personal, timeline]
    assert details.phone_numbers == ("0912 345 678",)
    assert details.phone_sources == ("facebook:post_text",)
    assert dict(details.field_sources)["phone"] == "facebook:post_text"


def test_profile_enricher_marks_unrendered_section_unavailable() -> None:
    work = work_route()
    browser = Browser({work: "work"})
    parser = Parser({work: ProfileDetails()})

    details = ProfileEnricher(
        BrowserSettings(),
        parser,  # type: ignore[arg-type]
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        content_ready_func=lambda browser, timeout, route: False,
    ).enrich(browser, record(), (ProfileField.WORKPLACE,))

    assert dict(details.field_status)["workplace"] == "section_unavailable"


class ScrollingBrowser(Browser):
    def __init__(self, personal: str, timeline_pages: list[str], heights: list[int]):
        timeline = "https://www.facebook.com/synthetic.user"
        super().__init__({routes()[0]: personal, timeline: timeline_pages[0]})
        self.timeline_pages = timeline_pages
        self.heights = heights
        self.page_index = 0
        self.scroll_calls = 0

    def execute_script(self, script: str, *args):
        if "scrollTo" in script:
            self.scroll_calls += 1
            self.page_index = min(
                self.page_index + 1,
                len(self.timeline_pages) - 1,
            )
            self.page_source = self.timeline_pages[self.page_index]
            return None

        if "scrollHeight" in script:
            return self.heights[min(self.page_index, len(self.heights) - 1)]

        return True


def test_phone_timeline_scrolling_collects_each_loaded_post_with_evidence() -> None:
    personal, _ = routes()
    browser = ScrollingBrowser(
        "<main><h1>Synthetic User</h1></main>",
        [
            "<main><h1>Synthetic User</h1></main>",
            """
            <main><div role="article">
              <a href="/synthetic.user/posts/1">Post 1</a>
              <div data-ad-preview="message">Call 0912 345 678</div>
            </div></main>
            """,
            """
            <main><div role="article">
              <a href="/synthetic.user/posts/2">Post 2</a>
              <div data-ad-preview="message">Call 0987 654 321</div>
            </div></main>
            """,
        ],
        [100, 200, 300],
    )

    details = ProfileEnricher(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        content_ready_func=lambda browser, timeout, route: True,
        sleep_func=lambda seconds: None,
        jitter_func=lambda start, end: 0,
    ).enrich(
        browser,
        record(),
        (ProfileField.PHONE,),
        phone_post_steps=2,
        phone_post_delay_seconds=0,
    )

    assert browser.get_calls == [
        personal,
        "https://www.facebook.com/synthetic.user",
    ]
    assert browser.scroll_calls == 2
    assert details.phone_numbers == ("0912 345 678", "0987 654 321")
    assert [item.source_url for item in details.phone_evidence] == [
        "https://www.facebook.com/synthetic.user/posts/1",
        "https://www.facebook.com/synthetic.user/posts/2",
    ]


def test_phone_timeline_scrolling_stops_when_height_is_stable() -> None:
    browser = ScrollingBrowser(
        "<main><h1>Synthetic User</h1></main>",
        ["<main></main>", "<main></main>"],
        [100, 100],
    )

    ProfileEnricher(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        content_ready_func=lambda browser, timeout, route: True,
        sleep_func=lambda seconds: None,
        jitter_func=lambda start, end: 0,
    ).enrich(
        browser,
        record(),
        (ProfileField.PHONE,),
        phone_post_steps=5,
        phone_post_delay_seconds=0,
    )

    assert browser.scroll_calls == 1


def test_phone_timeline_cancellation_after_first_progress_has_no_second_scroll() -> None:
    personal, _ = routes()
    timeline = "https://www.facebook.com/synthetic.user"
    browser = ScrollingBrowser(
        "<main><h1>Synthetic User</h1></main>",
        ["<main></main>", "<main></main>", "<main></main>"],
        [100, 200, 300],
    )
    parser = Parser(
        {
            personal: ProfileDetails(),
            timeline: ProfileDetails(),
        }
    )

    class CancelAfterProgress:
        def __init__(self) -> None:
            self.cancelled = False
            self.events: list[tuple[str, dict[str, int]]] = []

        def is_cancel_requested(self):
            return self.cancelled

        def emit(self, event_type, *, counters=None, safe_message=""):
            self.events.append((event_type, dict(counters or {})))
            self.cancelled = True

        def check_account_safety(self, browser):
            return None

    control = CancelAfterProgress()
    with pytest.raises(CrawlCancelled):
        ProfileEnricher(
            BrowserSettings(),
            parser,  # type: ignore[arg-type]
            control=control,
            authenticated_func=lambda browser: True,
            ready_func=lambda browser, timeout: None,
            content_ready_func=lambda browser, timeout, route: True,
            sleep_func=lambda seconds: None,
            jitter_func=lambda start, end: 0,
        ).enrich(
            browser,
            record(),
            (ProfileField.PHONE,),
            phone_post_steps=2,
            phone_post_delay_seconds=0,
        )

    assert browser.scroll_calls == 1
    assert control.events == [("target_progress", {"steps_completed": 1})]


def test_phone_timeline_first_scroll_full_guard_stops_before_scroll_or_progress() -> None:
    personal, _ = routes()
    timeline = "https://www.facebook.com/synthetic.user"
    browser = ScrollingBrowser(
        "<main><h1>Synthetic User</h1></main>",
        ["<main></main>", "<main></main>"],
        [100, 200],
    )
    parser = Parser(
        {
            personal: ProfileDetails(),
            timeline: ProfileDetails(),
        }
    )

    class UnsafeAtFirstScrollGuard:
        def __init__(self) -> None:
            self.safety_checks = 0
            self.events: list[tuple[str, dict[str, int]]] = []

        def is_cancel_requested(self):
            return False

        def emit(self, event_type, *, counters=None, safe_message=""):
            self.events.append((event_type, dict(counters or {})))

        def check_account_safety(self, browser):
            self.safety_checks += 1
            if self.safety_checks == 6:
                return SafetySignal(
                    SafetyCode.CHECKPOINT,
                    "Facebook checkpoint requires manual review.",
                    True,
                )
            return None

    control = UnsafeAtFirstScrollGuard()
    with pytest.raises(AccountSafetyStop):
        ProfileEnricher(
            BrowserSettings(),
            parser,  # type: ignore[arg-type]
            control=control,
            authenticated_func=lambda browser: True,
            ready_func=lambda browser, timeout: None,
            content_ready_func=lambda browser, timeout, route: True,
            sleep_func=lambda seconds: None,
            jitter_func=lambda start, end: 0,
        ).enrich(
            browser,
            record(),
            (ProfileField.PHONE,),
            phone_post_steps=1,
            phone_post_delay_seconds=0,
        )

    assert control.safety_checks == 6
    assert browser.scroll_calls == 0
    assert control.events == []
    assert [call[1] for call in parser.calls] == [personal, timeline]


def test_phone_timeline_one_step_has_exact_full_guard_and_browser_trace() -> None:
    personal, _ = routes()
    timeline = "https://www.facebook.com/synthetic.user"
    trace: list[str] = []

    class TraceBrowser:
        title = "Facebook"

        def __init__(self) -> None:
            self.current_url = "https://www.facebook.com/"
            self._page_source = ""
            self.scroll_calls = 0
            self._outcomes = {personal: "personal", timeline: "timeline"}
            self._heights = iter((100, 200))

        def get(self, url: str) -> None:
            trace.append(f"navigation:{url}")
            self.current_url = url
            self._page_source = self._outcomes[url]

        @property
        def page_source(self) -> str:
            trace.append("source")
            return self._page_source

        def execute_script(self, script: str, *args):
            if "scrollTo" in script:
                trace.append("scroll")
                self.scroll_calls += 1
                self._page_source = "scrolled"
                return None
            if "scrollHeight" in script:
                trace.append("height")
                return next(self._heights)
            return True

    class TraceParser:
        def parse(self, html: str, *, source_url: str, requested_fields=()):
            trace.append(f"parse:{source_url}")
            return ProfileDetails()

    class RecordingControl:
        def __init__(self) -> None:
            self.safety_checks = 0
            self.events: list[tuple[str, dict[str, int]]] = []

        def is_cancel_requested(self):
            return False

        def emit(self, event_type, *, counters=None, safe_message=""):
            event = (event_type, dict(counters or {}))
            self.events.append(event)
            trace.append(f"progress:{event[0]}:{event[1]}")

        def check_account_safety(self, browser):
            self.safety_checks += 1
            trace.append(f"safety:{self.safety_checks}")
            return None

    browser = TraceBrowser()
    control = RecordingControl()
    ProfileEnricher(
        BrowserSettings(),
        TraceParser(),  # type: ignore[arg-type]
        control=control,
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        content_ready_func=lambda browser, timeout, route: True,
        sleep_func=lambda seconds: None,
        jitter_func=lambda start, end: 0,
    ).enrich(
        browser,
        record(),
        (ProfileField.PHONE,),
        phone_post_steps=1,
        phone_post_delay_seconds=0,
    )

    assert trace == [
        f"navigation:{personal}",
        "safety:1",
        "safety:2",
        "source",
        f"parse:{personal}",
        f"navigation:{timeline}",
        "safety:3",
        "safety:4",
        "source",
        f"parse:{timeline}",
        "safety:5",
        "height",
        "safety:6",
        "scroll",
        "progress:target_progress:{'steps_completed': 1}",
        "safety:7",
        "source",
        f"parse:{timeline}",
        "safety:8",
        "height",
    ]
    assert browser.scroll_calls == 1
    assert control.events == [("target_progress", {"steps_completed": 1})]


def test_phone_timeline_scrolling_accepts_a_duration_only_budget() -> None:
    clock = iter((0.0, 0.0, 31.0))
    browser = ScrollingBrowser(
        "<main><h1>Synthetic User</h1></main>",
        [
            "<main></main>",
            """
            <main><div role="article">
              <a href="/synthetic.user/posts/1">Post 1</a>
              <div data-ad-preview="message">Call 0912 345 678</div>
            </div></main>
            """,
        ],
        [100, 200],
    )

    details = ProfileEnricher(
        BrowserSettings(),
        authenticated_func=lambda browser: True,
        ready_func=lambda browser, timeout: None,
        content_ready_func=lambda browser, timeout, route: True,
        sleep_func=lambda seconds: None,
        jitter_func=lambda start, end: 0,
        monotonic_func=lambda: next(clock),
    ).enrich(
        browser,
        record(),
        (ProfileField.PHONE,),
        phone_post_duration_seconds=30,
        phone_post_delay_seconds=0,
    )

    assert browser.scroll_calls == 1
    assert details.phone_numbers == ("0912 345 678",)
