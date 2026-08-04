import sys

from fb_crawl.cli.app import build_parser
from fb_crawl.cli.public import request_from_args
from fb_crawl.core.models import (
    PublicAction,
    TargetKind,
)


def test_page_command_builds_explicit_public_request() -> None:
    args = build_parser().parse_args(
        [
            "public",
            "page",
            "https://www.facebook.com/example",
            "--limit",
            "5",
        ]
    )

    request = request_from_args(args)

    assert request.action is PublicAction.PAGE

    assert request.targets == ("https://www.facebook.com/example",)

    assert request.limit == 5
    assert request.target_kind is TargetKind.PAGES


def test_importing_public_cli_does_not_import_selenium() -> None:
    assert "selenium" not in sys.modules
