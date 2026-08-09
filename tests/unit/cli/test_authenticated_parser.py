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


def test_authenticated_defaults_to_exhaustion_without_time_or_steps() -> None:
    args = build_parser().parse_args(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
        ]
    )

    request = request_from_authenticated_args(args)

    assert request.steps is None
    assert request.max_duration_seconds is None


def test_relationship_limits_build_depth_time_and_user_bounds() -> None:
    args = build_parser().parse_args(
        [
            "authenticated",
            "friends",
            "https://www.facebook.com/synthetic.user",
            "--depth",
            "3",
            "--max-users",
            "250",
            "--max-duration",
            "90",
        ]
    )

    request = request_from_authenticated_args(args)

    assert request.depth == 3
    assert request.max_nodes == 250
    assert request.max_duration_seconds == 90


def test_force_requests_fresh_uid_resolution() -> None:
    args = build_parser().parse_args(
        [
            "authenticated",
            "friends",
            "https://www.facebook.com/synthetic.user",
            "--force",
        ]
    )

    assert request_from_authenticated_args(args).force_uid_refresh is True


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


def test_direct_profile_implicitly_enables_enrichment() -> None:
    args = build_parser().parse_args(
        [
            "authenticated",
            "profile",
            "https://www.facebook.com/synthetic.user",
            "--profile-fields",
            "phone,current_city,birth_date",
        ]
    )

    request = request_from_authenticated_args(args)

    assert request.action is AuthenticatedAction.PROFILE
    assert request.enrich_profiles is True
    assert request.profile_fields == (
        ProfileField.PHONE,
        ProfileField.CURRENT_CITY,
        ProfileField.BIRTH_DATE,
    )


@pytest.mark.parametrize(
    "action",
    [
        AuthenticatedAction.FRIENDS,
        AuthenticatedAction.FOLLOWERS,
        AuthenticatedAction.REACTIONS,
        AuthenticatedAction.ENGAGEMENT,
        AuthenticatedAction.MESSAGES,
        AuthenticatedAction.INSPECT,
    ],
)
def test_new_authenticated_actions_build_typed_requests(
    action: AuthenticatedAction,
) -> None:
    target = (
        "https://www.facebook.com/messages/t/123"
        if action is AuthenticatedAction.MESSAGES
        else "https://www.facebook.com/synthetic.user"
        if action is AuthenticatedAction.INSPECT
        else "https://www.facebook.com/acme/posts/1"
        if action in {
            AuthenticatedAction.REACTIONS,
            AuthenticatedAction.ENGAGEMENT,
        }
        else "https://www.facebook.com/synthetic.user"
    )
    args = build_parser().parse_args(
        ["authenticated", action.value, target, "--steps", "3"]
    )

    request = request_from_authenticated_args(args)

    assert request.action is action
    assert request.targets == (target,)
    assert request.steps == 3


def test_messages_help_does_not_offer_profile_enrichment_options() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "authenticated",
                "messages",
                "https://www.facebook.com/messages/t/123",
                "--enrich-profiles",
            ]
        )


@pytest.mark.parametrize("flag", ["--resume", "--incremental"])
def test_checkpoint_modes_get_a_runtime_default_path(flag: str) -> None:
    args = build_parser().parse_args(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            flag,
        ]
    )
    request = request_from_authenticated_args(args)

    assert request.checkpoint_path == "runtime\\checkpoints\\members.json"
    assert request.resume is (flag == "--resume")
    assert request.incremental is (flag == "--incremental")


def test_checkpoint_path_requires_a_checkpoint_mode() -> None:
    args = build_parser().parse_args(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--checkpoint",
            "runtime/checkpoints/custom.json",
        ]
    )

    with pytest.raises(ValidationError, match="requires"):
        request_from_authenticated_args(args)


def test_repair_parser_builds_bounded_identity_repair_arguments() -> None:
    args = build_parser().parse_args(
        [
            "authenticated",
            "repair",
            "runtime/output/friends.csv",
            "--output",
            "runtime/output/friends-repaired.csv",
            "--limit",
            "7",
            "--delay",
            "1.5",
            "--retry-failed",
            "--force",
            "--headless",
        ]
    )

    assert args.action == "repair"
    assert args.input == Path("runtime/output/friends.csv")
    assert args.output == Path("runtime/output/friends-repaired.csv")
    assert args.limit == 7
    assert args.delay == 1.5
    assert args.retry_failed is True
    assert args.force is True
    assert args.headless is True
