import pytest

from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    SessionError,
    ValidationError,
)
from fb_crawl.core.models import (
    AuthenticatedAction,
    ProfileDetails,
    ProfileField,
    ScrapeMode,
    ScrapeRequest,
    UserRecord,
)
from fb_crawl.services.authenticated import (
    AuthenticatedService,
)


class Session:
    def __init__(self) -> None:
        self.ensure_calls = 0
        self.assert_calls = 0
        self.fail_assertion = False

    def ensure_authenticated(
        self,
        browser,
    ) -> None:
        self.ensure_calls += 1

    def assert_authenticated(
        self,
        browser,
    ) -> None:
        self.assert_calls += 1

        if self.fail_assertion:
            raise SessionError(
                "The authenticated Facebook " "session is no longer valid."
            )


class Collector:
    def __init__(
        self,
        pages: dict[str, str | Exception],
    ) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def collect(
        self,
        browser,
        url: str,
        *,
        steps: int,
        delay_seconds: float,
    ):
        self.calls.append(url)
        value = self.pages[url]

        if isinstance(value, Exception):
            raise value

        return value, steps


class Parser:
    def parse(
        self,
        html: str,
        *,
        source: str,
        source_url: str,
    ):
        user_id, name = html.split(
            ":",
            1,
        )

        return (
            UserRecord(
                user_id=user_id,
                name=name or None,
                profile_url=("https://www.facebook.com/" f"profile.php?id={user_id}"),
                source=source,
                source_url=source_url,
            ),
        )


def request(
    action: AuthenticatedAction,
    *targets: str,
    **overrides,
) -> ScrapeRequest:
    return ScrapeRequest(
        mode=ScrapeMode.AUTHENTICATED,
        action=action,
        targets=targets,
        steps=2,
        delay_seconds=0,
        **overrides,
    )


def test_members_service_normalizes_targets_and_counts_users() -> None:
    target = "https://www.facebook.com/" "groups/1/members"

    session = Session()
    members = Collector(
        {
            target: "100:Member",
        }
    )

    service = AuthenticatedService(
        session,
        members,
        Collector({}),
        Parser(),
    )

    result = service.run(
        request(
            AuthenticatedAction.MEMBERS,
            "https://facebook.com/groups/1",
        ),
        object(),
    )

    assert [record.user_id for record in result.records] == ["100"]

    assert result.stats.requested == 1
    assert result.stats.discovered == 1
    assert result.stats.succeeded == 1
    assert result.stats.failed == 0

    assert session.ensure_calls == 1
    assert members.calls == [target]


def test_explicit_invalid_target_fails_before_session() -> None:
    session = Session()

    service = AuthenticatedService(
        session,
        Collector({}),
        Collector({}),
        Parser(),
    )

    with pytest.raises(
        ValidationError,
        match="members target",
    ):
        service.run(
            request(
                AuthenticatedAction.MEMBERS,
                "https://facebook.com/acme",
            ),
            object(),
        )

    assert session.ensure_calls == 0


def test_enrichment_without_runtime_fails_before_session() -> None:
    session = Session()
    service = AuthenticatedService(
        session,
        Collector({}),
        Collector({}),
        Parser(),
    )

    with pytest.raises(ValidationError, match="enrichment runtime"):
        service.run(
            request(
                AuthenticatedAction.MEMBERS,
                "https://facebook.com/groups/1",
                enrich_profiles=True,
            ),
            object(),
        )

    assert session.ensure_calls == 0


def test_batch_preserves_success_when_another_target_fails() -> None:
    members_url = "https://www.facebook.com/" "groups/1/members"

    comments_url = "https://www.facebook.com/" "acme/posts/2"

    service = AuthenticatedService(
        Session(),
        Collector(
            {
                members_url: "100:Member",
            }
        ),
        Collector(
            {
                comments_url: (
                    BrowserNavigationError(
                        "Authenticated comments " "navigation failed.",
                        target=comments_url,
                    )
                ),
            }
        ),
        Parser(),
    )

    result = service.run(
        request(
            AuthenticatedAction.BATCH,
            "https://facebook.com/groups/1",
            "https://facebook.com/acme/posts/2",
            ("https://facebook.com/" "places/Nowhere/3" "?access_token=do-not-store"),
        ),
        object(),
    )

    assert [record.user_id for record in result.records] == ["100"]

    assert [issue.code for issue in result.issues] == [
        "validation_error",
        "authenticated_navigation_failed",
    ]

    assert result.issues[0].target == ("https://facebook.com/" "places/Nowhere/3")

    assert "access_token" not in result.issues[0].target

    assert result.stats.requested == 3
    assert result.stats.succeeded == 1
    assert result.stats.failed == 2


def test_session_loss_aborts_instead_of_becoming_target_issue() -> None:
    session = Session()
    session.fail_assertion = True

    service = AuthenticatedService(
        session,
        Collector({}),
        Collector({}),
        Parser(),
    )

    with pytest.raises(SessionError):
        service.run(
            request(
                AuthenticatedAction.MEMBERS,
                "https://facebook.com/groups/1",
            ),
            object(),
        )


def test_service_merges_duplicate_users_and_counts_raw_discovery() -> None:
    first = "https://www.facebook.com/" "acme/posts/1"

    second = "https://www.facebook.com/" "acme/posts/2"

    comments = Collector(
        {
            first: "100:",
            second: "100:Filled Name",
        }
    )

    service = AuthenticatedService(
        Session(),
        Collector({}),
        comments,
        Parser(),
    )

    result = service.run(
        request(
            AuthenticatedAction.COMMENTS,
            first,
            second,
        ),
        object(),
    )

    assert len(result.records) == 1
    assert result.records[0].name == "Filled Name"

    # Two raw discoveries, but only one unique user after deduplication.
    assert result.stats.discovered == 2

    # One unique user after deduplication.
    assert result.stats.succeeded == 1


class EmptyParser:
    def parse(
        self,
        html: str,
        *,
        source: str,
        source_url: str,
    ):
        return ()


def test_empty_parsed_target_is_success_not_failure() -> None:
    target = "https://www.facebook.com/" "groups/1/members"

    service = AuthenticatedService(
        Session(),
        Collector(
            {
                target: "empty",
            }
        ),
        Collector({}),
        EmptyParser(),
    )

    result = service.run(
        request(
            AuthenticatedAction.MEMBERS,
            target,
        ),
        object(),
    )

    assert result.records == ()
    assert result.issues == ()

    assert result.stats.discovered == 0
    assert result.stats.succeeded == 0
    assert result.stats.failed == 0


class ProfileEnricher:
    def __init__(self, outcomes: dict[str, ProfileDetails | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, tuple[ProfileField, ...]]] = []

    def enrich(self, browser, record: UserRecord, fields):
        self.calls.append((record.user_id, fields))
        outcome = self.outcomes[record.user_id]

        if isinstance(outcome, Exception):
            raise outcome

        return outcome


def test_enrichment_runs_once_after_global_dedup_and_merges_details() -> None:
    first = "https://www.facebook.com/acme/posts/1"
    second = "https://www.facebook.com/acme/posts/2"
    profiles = ProfileEnricher(
        {
            "100": ProfileDetails(
                phone_numbers=("+1 202-555-0147",),
                phone_sources=("facebook:profile_contact",),
                current_city="Synthetic City",
                birth_date="1990-01-02",
                birth_year=1990,
            )
        }
    )
    service = AuthenticatedService(
        Session(),
        Collector({}),
        Collector({first: "100:", second: "100:Filled Name"}),
        Parser(),
        profiles,
        sleep_func=lambda seconds: None,
    )
    fields = (ProfileField.PHONE, ProfileField.BIRTH_DATE)

    result = service.run(
        request(
            AuthenticatedAction.COMMENTS,
            first,
            second,
            enrich_profiles=True,
            profile_fields=fields,
            profile_delay_seconds=0,
        ),
        object(),
    )

    assert profiles.calls == [("100", fields)]
    assert result.records[0].name == "Filled Name"
    assert result.records[0].phone_numbers == ("+1 202-555-0147",)
    assert result.records[0].current_city == "Synthetic City"
    assert result.records[0].birth_year == 1990
    assert result.enrichment is not None
    assert result.enrichment.selected == 1
    assert result.enrichment.succeeded == 1
    assert result.enrichment.phone_found == 1
    assert result.enrichment.birth_year_found == 1


def test_enrichment_limit_delay_and_empty_success_are_bounded() -> None:
    target = "https://www.facebook.com/groups/1/members"

    class MultiParser:
        def parse(self, html, *, source, source_url):
            return tuple(
                UserRecord(
                    user_id=user_id,
                    name=f"User {user_id}",
                    profile_url=f"https://www.facebook.com/profile.php?id={user_id}",
                    source=source,
                    source_url=source_url,
                )
                for user_id in ("100", "200", "300")
            )

    profiles = ProfileEnricher(
        {
            "100": ProfileDetails(),
            "200": ProfileDetails(address="123 Synthetic Street"),
        }
    )
    sleeps: list[float] = []
    service = AuthenticatedService(
        Session(),
        Collector({target: "ignored"}),
        Collector({}),
        MultiParser(),
        profiles,
        sleep_func=sleeps.append,
    )

    result = service.run(
        request(
            AuthenticatedAction.MEMBERS,
            target,
            enrich_profiles=True,
            profile_limit=2,
            profile_delay_seconds=1.5,
        ),
        object(),
    )

    assert [item[0] for item in profiles.calls] == ["100", "200"]
    assert sleeps == [1.5]
    assert result.records[2].user_id == "300"
    assert result.enrichment is not None
    assert result.enrichment.selected == 2
    assert result.enrichment.succeeded == 2
    assert result.enrichment.address_found == 1


def test_profile_failure_preserves_base_record_and_continues() -> None:
    target = "https://www.facebook.com/groups/1/members"

    class TwoUsers:
        def parse(self, html, *, source, source_url):
            return tuple(
                UserRecord(
                    user_id=user_id,
                    name=f"User {user_id}",
                    profile_url=f"https://www.facebook.com/profile.php?id={user_id}",
                    source=source,
                    source_url=source_url,
                )
                for user_id in ("100", "200")
            )

    profiles = ProfileEnricher(
        {
            "100": BrowserNavigationError(
                "Authenticated profile navigation failed.",
                target="https://www.facebook.com/profile.php?id=100",
            ),
            "200": ProfileDetails(current_city="Synthetic City"),
        }
    )
    service = AuthenticatedService(
        Session(),
        Collector({target: "ignored"}),
        Collector({}),
        TwoUsers(),
        profiles,
        sleep_func=lambda seconds: None,
    )

    result = service.run(
        request(
            AuthenticatedAction.MEMBERS,
            target,
            enrich_profiles=True,
            profile_delay_seconds=0,
        ),
        object(),
    )

    assert [record.user_id for record in result.records] == ["100", "200"]
    assert result.records[0].current_city is None
    assert result.records[1].current_city == "Synthetic City"
    assert result.issues[-1].action == "profile_enrichment"
    assert result.enrichment is not None
    assert result.enrichment.failed == 1
    assert result.enrichment.succeeded == 1


def test_profile_session_loss_stops_later_profiles() -> None:
    target = "https://www.facebook.com/groups/1/members"

    class TwoUsers:
        def parse(self, html, *, source, source_url):
            return tuple(
                UserRecord(
                    user_id=user_id,
                    name=None,
                    profile_url=f"https://www.facebook.com/profile.php?id={user_id}",
                    source=source,
                    source_url=source_url,
                )
                for user_id in ("100", "200")
            )

    profiles = ProfileEnricher(
        {
            "100": SessionError("Session unavailable."),
            "200": ProfileDetails(),
        }
    )
    service = AuthenticatedService(
        Session(),
        Collector({target: "ignored"}),
        Collector({}),
        TwoUsers(),
        profiles,
        sleep_func=lambda seconds: None,
    )

    with pytest.raises(SessionError):
        service.run(
            request(
                AuthenticatedAction.MEMBERS,
                target,
                enrich_profiles=True,
                profile_delay_seconds=0,
            ),
            object(),
        )

    assert [item[0] for item in profiles.calls] == ["100"]
