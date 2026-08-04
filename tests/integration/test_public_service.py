from fb_crawl.core.exceptions import FetchError

from fb_crawl.core.models import (
    PageRecord,
    PublicAction,
    ScrapeMode,
    ScrapeRequest,
    TargetKind,
)

from fb_crawl.services.public import PublicService


class FakeClient:
    def __init__(
        self,
        pages: dict[str, str | Exception],
    ) -> None:
        self.pages = pages

    def get_text(
        self,
        url: str,
        *,
        headers=None,
    ) -> str:
        value = self.pages[url]

        if isinstance(value, Exception):
            raise value

        return value


class FakeDiscovery:
    def __init__(
        self,
        links: dict[str, list[str]],
    ) -> None:
        self.links = links

    def search(
        self,
        keyword: str,
        target: TargetKind,
        limit: int,
    ) -> list[str]:
        return self.links[keyword][:limit]

    def from_html(
        self,
        html: str,
        *,
        base_url: str,
        target: TargetKind,
        limit: int,
    ) -> list[str]:
        return self.links.get(
            base_url,
            [],
        )[:limit]


class FakeParser:
    def parse(
        self,
        html: str,
        canonical_url: str,
    ) -> PageRecord:
        return PageRecord(
            canonical_url=canonical_url,
            page_name=html,
        )


class FakeEnricher:
    def enrich(
        self,
        record: PageRecord,
        facebook_html: str,
    ):
        return record, ()


def build_service(
    pages: dict[str, str | Exception],
    links: dict[str, list[str]],
) -> PublicService:
    return PublicService(
        FakeClient(pages),
        FakeDiscovery(links),
        FakeParser(),
        FakeEnricher(),
    )


def test_direct_batch_keeps_success_when_another_target_fails() -> None:
    good = "https://www.facebook.com/good"
    bad = "https://www.facebook.com/bad"

    result = build_service(
        {
            good: "Good Page",
            bad: FetchError(
                "Public fetch failed.",
                target=bad,
            ),
        },
        {},
    ).run(
        ScrapeRequest(
            mode=ScrapeMode.PUBLIC,
            action=PublicAction.PAGE,
            targets=(good, bad),
            target_kind=TargetKind.ALL,
            limit=2,
            max_nodes=2,
        )
    )

    assert [record.page_name for record in result.records] == [
        "Good Page",
    ]

    assert result.stats.succeeded == 1
    assert result.stats.failed == 1
    assert result.issues[0].target == bad


def test_crawl_is_breadth_first_deduplicated_and_depth_bounded() -> None:
    seed = "https://www.facebook.com/seed"
    next_url = "https://www.facebook.com/next"
    too_deep = "https://www.facebook.com/too-deep"

    result = build_service(
        {
            seed: "Seed",
            next_url: "Next",
        },
        {
            seed: [
                next_url,
                seed,
            ],
            next_url: [
                too_deep,
            ],
        },
    ).run(
        ScrapeRequest(
            mode=ScrapeMode.PUBLIC,
            action=PublicAction.CRAWL,
            targets=(seed,),
            target_kind=TargetKind.ALL,
            depth=1,
            max_nodes=10,
            limit=10,
        )
    )

    assert [(item.canonical_url, item.depth) for item in result.records] == [
        (seed, 0),
        (next_url, 1),
    ]


def test_crawl_accepts_public_group_as_a_discovery_seed() -> None:
    group = "https://www.facebook.com/groups/pythonvn"
    member = "https://www.facebook.com/" "profile.php?id=100013976614656"

    result = build_service(
        {
            group: "Group HTML",
            member: "Member",
        },
        {
            group: [
                member,
            ],
        },
    ).run(
        ScrapeRequest(
            mode=ScrapeMode.PUBLIC,
            action=PublicAction.CRAWL,
            targets=(group,),
            target_kind=TargetKind.ALL,
            depth=0,
            max_nodes=10,
            limit=10,
        )
    )

    assert [
        (
            item.canonical_url,
            item.discovery_source,
        )
        for item in result.records
    ] == [
        (
            member,
            group,
        ),
    ]
