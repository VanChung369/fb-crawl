from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from fb_crawl.cli.public import (
    add_public_parser,
    execute_public,
)
from fb_crawl.core.exceptions import FbCrawlError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fb-crawl")

    modes = parser.add_subparsers(
        dest="mode",
        required=True,
    )

    add_public_parser(modes)

    return parser


def main(
    argv: Sequence[str] | None = None,
) -> int:
    parser = build_parser()

    try:
        args = parser.parse_args(argv)

        if args.mode == "public":
            return execute_public(args)

        parser.error(f"Unsupported mode: {args.mode}")

    except FbCrawlError as error:
        print(
            error.safe_message,
            file=sys.stderr,
        )
        return error.exit_code

    except ValueError as error:
        print(
            str(error),
            file=sys.stderr,
        )
        return 2

    return 2
