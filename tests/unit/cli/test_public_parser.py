import sys
import subprocess

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


def test_building_public_cli_does_not_import_browser_extras() -> None:
    code = """
from fb_crawl.cli.app import build_parser

build_parser().parse_args(
    [
        "public",
        "page",
        "https://www.facebook.com/example",
    ]
)

import sys

assert not any(
    name == "selenium"
    or name.startswith("selenium.")
    for name in sys.modules
)

assert not any(
    name == "bs4"
    or name.startswith("bs4.")
    for name in sys.modules
)
"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
