from __future__ import annotations

import argparse
from pathlib import Path

from fb_crawl.adapters.http.client import (
    CurlHttpClient,
)
from fb_crawl.adapters.http.contact_parser import (
    ContactEnricher,
)
from fb_crawl.adapters.http.discovery import (
    PublicDiscovery,
)
from fb_crawl.adapters.http.page_parser import (
    PublicPageParser,
)
from fb_crawl.config import (
    Settings,
    load_settings,
)
from fb_crawl.core.models import (
    PublicAction,
    ScrapeMode,
    ScrapeRequest,
    TargetKind,
)
from fb_crawl.exporters.csv import write_csv
from fb_crawl.exporters.json import write_json
from fb_crawl.services.public import PublicService

DEFAULT_FILENAMES = {
    PublicAction.PAGE: "pages",
    PublicAction.SEARCH: "pages",
    PublicAction.CRAWL: "pages",
}


def _common(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--target",
        choices=[item.value for item in TargetKind],
        default="pages",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
    )

    parser.add_argument(
        "--output",
        type=Path,
    )

    parser.add_argument(
        "--format",
        choices=(
            "csv",
            "json",
        ),
        default="csv",
    )

    parser.add_argument(
        "--timeout",
        type=float,
    )

    parser.add_argument(
        "--max-retries",
        type=int,
    )


def add_public_parser(
    mode_subparsers,
) -> None:
    public_parser = mode_subparsers.add_parser(
        "public",
        help="Use public HTTP scraping",
    )

    actions = public_parser.add_subparsers(
        dest="action",
        required=True,
    )

    page = actions.add_parser(
        "page",
        help=("Scrape direct public " "page/profile URLs"),
    )
    page.add_argument(
        "urls",
        nargs="+",
    )
    _common(page)

    search = actions.add_parser(
        "search",
        help="Discover and scrape by keyword",
    )
    search.add_argument(
        "--keyword",
        required=True,
    )
    _common(search)

    crawl = actions.add_parser(
        "crawl",
        help=("Breadth-first crawl page/profile " "targets or a public group seed"),
    )
    crawl.add_argument(
        "urls",
        nargs="+",
    )
    crawl.add_argument(
        "--depth",
        type=int,
        default=1,
    )
    crawl.add_argument(
        "--max-nodes",
        type=int,
    )
    _common(crawl)


def request_from_args(
    args: argparse.Namespace,
) -> ScrapeRequest:
    action = PublicAction(args.action)

    urls = tuple(
        getattr(
            args,
            "urls",
            (),
        )
    )

    return ScrapeRequest(
        mode=ScrapeMode.PUBLIC,
        action=action,
        targets=urls,
        keyword=getattr(
            args,
            "keyword",
            None,
        ),
        target_kind=TargetKind(args.target),
        limit=args.limit,
        depth=getattr(
            args,
            "depth",
            0,
        ),
        max_nodes=(
            getattr(
                args,
                "max_nodes",
                None,
            )
            or args.limit
        ),
        delay_seconds=args.delay,
    )


def build_public_service(
    settings: Settings,
) -> PublicService:
    client = CurlHttpClient(settings)

    return PublicService(
        client,
        PublicDiscovery(client),
        PublicPageParser(),
        ContactEnricher(client),
    )


def execute_public(
    args: argparse.Namespace,
) -> int:
    settings = load_settings(
        timeout_seconds=args.timeout,
        max_retries=args.max_retries,
    )

    request = request_from_args(args)
    action = PublicAction(args.action)

    result = build_public_service(settings).run(request)

    output = args.output or (
        settings.output_dir / (f"{DEFAULT_FILENAMES[action]}" f".{args.format}")
    )

    if args.format == "csv":
        written = write_csv(
            result,
            output,
        )
    else:
        written = write_json(
            result,
            output,
        )

    output_status = output if written else "unchanged"

    print(
        f"requested={result.stats.requested} "
        f"succeeded={result.stats.succeeded} "
        f"failed={result.stats.failed} "
        f"output={output_status}"
    )

    return 1 if result.has_failures else 0
