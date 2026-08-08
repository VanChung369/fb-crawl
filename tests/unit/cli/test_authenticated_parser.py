from pathlib import Path

import pytest

from fb_crawl.cli.app import build_parser
from fb_crawl.cli.authenticated import (
    request_from_authenticated_args,
)
from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.models import (
    AuthenticatedAction,
    ProfileField,
    ScrapeMode,
)


def test_members_parser_builds_explicit_authenticated_request() -> None:
    args = build_parser().parse_args(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--steps",
            "7",
            "--delay",
            "1.5",
            "--headless",
        ]
    )

    request = request_from_authenticated_args(args)

    assert request.mode is ScrapeMode.AUTHENTICATED
    assert request.action is AuthenticatedAction.MEMBERS
    assert request.targets == ("https://www.facebook.com/groups/1",)
    assert request.steps == 7
    assert request.delay_seconds == 1.5
    assert args.headless is True


def test_batch_reader_ignores_blank_and_comment_lines(
    tmp_path: Path,
) -> None:
    batch = tmp_path / "targets.txt"
    batch.write_text(
        "# synthetic input\n"
        "\n"
        "https://www.facebook.com/groups/1\n"
        "  https://www.facebook.com/acme/posts/2  \n",
        encoding="utf-8",
    )

    args = build_parser().parse_args(
        [
            "authenticated",
            "batch",
            "--input",
            str(batch),
        ]
    )

    request = request_from_authenticated_args(args)

    assert request.action is AuthenticatedAction.BATCH
    assert request.targets == (
        "https://www.facebook.com/groups/1",
        "https://www.facebook.com/acme/posts/2",
    )


def test_profile_enrichment_flags_build_typed_request() -> None:
    args = build_parser().parse_args(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--enrich-profiles",
            "--profile-fields",
            "phone,birth_date,phone,current_city",
            "--profile-limit",
            "7",
            "--profile-delay",
            "1.5",
        ]
    )

    request = request_from_authenticated_args(args)

    assert request.enrich_profiles is True
    assert request.profile_fields == (
        ProfileField.PHONE,
        ProfileField.BIRTH_DATE,
        ProfileField.CURRENT_CITY,
    )
    assert request.profile_limit == 7
    assert request.profile_delay_seconds == 1.5


@pytest.mark.parametrize(
    "arguments",
    [
        ["--profile-fields", "phone"],
        ["--enrich-profiles", "--profile-fields", "unknown"],
        ["--enrich-profiles", "--profile-fields", "phone,"],
    ],
)
def test_invalid_profile_fields_fail_before_runtime(arguments: list[str]) -> None:
    args = build_parser().parse_args(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            *arguments,
        ]
    )

    with pytest.raises((ValidationError, ValueError)):
        request_from_authenticated_args(args)
