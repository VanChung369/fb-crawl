import pytest

from fb_crawl.core.exceptions import (
    BrowserNavigationError,
    SessionError,
    ValidationError,
)
from fb_crawl.core.models import (
    AuthenticatedAction,
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
) -> ScrapeRequest:
    return ScrapeRequest(
        mode=ScrapeMode.AUTHENTICATED,
        action=action,
        targets=targets,
        steps=2,
        delay_seconds=0,
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
