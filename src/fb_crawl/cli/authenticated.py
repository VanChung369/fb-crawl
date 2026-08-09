from __future__ import annotations

import argparse
import getpass
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from fb_crawl.config import (
    BrowserSettings,
    load_browser_settings,
    validate_checkpoint_path,
)
from fb_crawl.core.exceptions import (
    ConfigurationError,
    ValidationError,
)
from fb_crawl.core.models import (
    AuthenticatedAction,
    ProfileField,
    ScrapeMode,
    ScrapeRequest,
)

DEFAULT_OUTPUTS = {
    AuthenticatedAction.MEMBERS: "members",
    AuthenticatedAction.COMMENTS: "comments",
    AuthenticatedAction.PROFILE: "profile",
    AuthenticatedAction.FRIENDS: "friends",
    AuthenticatedAction.FOLLOWERS: "followers",
    AuthenticatedAction.REACTIONS: "reactions",
    AuthenticatedAction.ENGAGEMENT: "engagement",
    AuthenticatedAction.MESSAGES: "messages",
    AuthenticatedAction.INSPECT: "inspect",
    AuthenticatedAction.BATCH: "batch",
}


def _common(
    parser: argparse.ArgumentParser,
    *,
    profile_options: bool = True,
) -> None:
    parser.add_argument(
        "--steps",
        type=int,
        default=5,
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--proxy")
    parser.add_argument(
        "--session-path",
        type=Path,
    )
    parser.add_argument(
        "--browser-timeout",
        type=float,
    )
    parser.add_argument(
        "--verification-timeout",
        type=float,
    )
    parser.add_argument(
        "--output",
        type=Path,
    )
    parser.add_argument(
        "--format",
        choices=("csv", "json", "txt", "xlsx"),
        default="csv",
    )
    checkpoint_mode = parser.add_mutually_exclusive_group()
    checkpoint_mode.add_argument(
        "--resume",
        action="store_true",
        help="Resume completed targets from an atomic runtime checkpoint",
    )
    checkpoint_mode.add_argument(
        "--incremental",
        action="store_true",
        help="Emit only identities not already known in the checkpoint",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help="Checkpoint JSON path; defaults under runtime/checkpoints",
    )
    if profile_options:
        parser.add_argument(
            "--enrich-profiles",
            action="store_true",
        )
        parser.add_argument(
            "--profile-fields",
            help=(
                "Comma-separated: phone,website,address,current_city,"
                "hometown,birth_date,bio,workplace,education,gender,"
                "languages,relationship_status"
            ),
        )
        parser.add_argument(
            "--profile-limit",
            type=int,
            default=20,
        )
        parser.add_argument(
            "--profile-delay",
            type=float,
            default=3.0,
        )
    else:
        parser.set_defaults(
            enrich_profiles=False,
            profile_fields=None,
            profile_limit=20,
            profile_delay=3.0,
        )


def add_authenticated_parser(
    mode_subparsers,
) -> None:
    authenticated = mode_subparsers.add_parser(
        "authenticated",
        help="Use a validated Facebook browser session",
    )

    actions = authenticated.add_subparsers(
        dest="action",
        required=True,
    )

    members = actions.add_parser(
        "members",
        help="Collect visible group members",
    )
    members.add_argument(
        "urls",
        nargs="+",
    )
    _common(members)

    comments = actions.add_parser(
        "comments",
        help="Collect visible post commenters",
    )
    comments.add_argument(
        "urls",
        nargs="+",
    )
    _common(comments)

    profile = actions.add_parser(
        "profile",
        help="Collect visible details from one or more profiles",
    )
    profile.add_argument("urls", nargs="+")
    _common(profile)

    friends = actions.add_parser(
        "friends",
        help="Collect a profile's visible friends",
    )
    friends.add_argument("urls", nargs="+")
    _common(friends)

    followers = actions.add_parser(
        "followers",
        help="Collect a profile's visible followers",
    )
    followers.add_argument("urls", nargs="+")
    _common(followers)

    reactions = actions.add_parser(
        "reactions",
        help="Collect users from a post's visible reactions dialog",
    )
    reactions.add_argument("urls", nargs="+")
    _common(reactions)

    engagement = actions.add_parser(
        "engagement",
        help="Collect visible commenters and reacting users together",
    )
    engagement.add_argument("urls", nargs="+")
    _common(engagement)

    messages = actions.add_parser(
        "messages",
        help="Collect visible text from explicit conversation URLs",
    )
    messages.add_argument("urls", nargs="+")
    _common(messages, profile_options=False)

    inspect = actions.add_parser(
        "inspect",
        help="Emit sanitized browser and selector diagnostics",
    )
    inspect.add_argument("urls", nargs="+")
    _common(inspect, profile_options=False)

    batch = actions.add_parser(
        "batch",
        help="Classify and collect URLs from a file",
    )
    batch.add_argument(
        "--input",
        type=Path,
        required=True,
    )
    _common(batch)


def _read_batch(
    path: Path,
) -> tuple[str, ...]:
    try:
        lines = path.read_text(
            encoding="utf-8",
        ).splitlines()

    except (OSError, UnicodeError) as error:
        raise ValidationError(
            f"Cannot read authenticated batch input {path}."
        ) from error

    return tuple(
        stripped
        for line in lines
        if (stripped := line.strip()) and not stripped.startswith("#")
    )


def _profile_fields(value: str | None) -> tuple[ProfileField, ...]:
    if value is None:
        return ()

    raw_fields = [item.strip() for item in value.split(",")]

    if not raw_fields or any(not item for item in raw_fields):
        raise ValidationError("Profile fields must be a non-empty comma list.")

    try:
        fields = tuple(ProfileField(item) for item in raw_fields)
    except ValueError as error:
        raise ValidationError(
            "An unsupported profile field was provided."
        ) from error

    return tuple(dict.fromkeys(fields))


def request_from_authenticated_args(
    args: argparse.Namespace,
) -> ScrapeRequest:
    action = AuthenticatedAction(args.action)

    targets = (
        _read_batch(args.input)
        if action is AuthenticatedAction.BATCH
        else tuple(args.urls)
    )

    if not targets:
        raise ValidationError("At least one authenticated target is required.")

    profile_fields = _profile_fields(args.profile_fields)

    enrich_profiles = (
        args.enrich_profiles or action is AuthenticatedAction.PROFILE
    )

    if action is AuthenticatedAction.MESSAGES and (
        enrich_profiles or profile_fields
    ):
        raise ValidationError(
            "Profile enrichment options are not supported by messages."
        )

    if profile_fields and not enrich_profiles:
        raise ValidationError("Profile fields require --enrich-profiles.")

    if args.checkpoint is not None and not (args.resume or args.incremental):
        raise ValidationError(
            "--checkpoint requires --resume or --incremental."
        )

    checkpoint = (
        args.checkpoint
        or Path("runtime/checkpoints") / f"{action.value}.json"
        if args.resume or args.incremental
        else None
    )

    return ScrapeRequest(
        mode=ScrapeMode.AUTHENTICATED,
        action=action,
        targets=targets,
        steps=args.steps,
        delay_seconds=args.delay,
        enrich_profiles=enrich_profiles,
        profile_fields=profile_fields,
        profile_limit=args.profile_limit,
        profile_delay_seconds=args.profile_delay,
        resume=args.resume,
        incremental=args.incremental,
        checkpoint_path=str(checkpoint) if checkpoint is not None else None,
    )


class ServicePort(Protocol):
    def validate(
        self,
        request: ScrapeRequest,
    ) -> None: ...

    def run(
        self,
        request: ScrapeRequest,
        browser,
    ): ...


@dataclass(frozen=True, slots=True)
class AuthenticatedRuntime:
    create_browser: Callable[
        [BrowserSettings],
        object,
    ]
    create_service: Callable[
        [
            BrowserSettings,
            Callable[[], tuple[str, str]],
        ],
        ServicePort,
    ]
    ensure_format: Callable[[str], None]
    write_result: Callable[
        [object, Path, str],
        bool,
    ]


def _load_runtime() -> AuthenticatedRuntime:
    try:
        from fb_crawl.adapters.browser.comments import (
            CommentsCollector,
        )
        from fb_crawl.adapters.browser.driver import (
            create_firefox_driver,
        )
        from fb_crawl.adapters.browser.login import (
            SessionManager,
        )
        from fb_crawl.adapters.browser.members import (
            MembersCollector,
        )
        from fb_crawl.adapters.browser.inspect import (
            BrowserInspector,
        )
        from fb_crawl.adapters.browser.message_parser import (
            MessageParser,
        )
        from fb_crawl.adapters.browser.messages import (
            MessagesCollector,
        )
        from fb_crawl.adapters.browser.profile_parser import (
            ProfileParser,
        )
        from fb_crawl.adapters.browser.reactions import (
            ReactionsCollector,
        )
        from fb_crawl.adapters.browser.reaction_parser import (
            ReactionParser,
        )
        from fb_crawl.adapters.browser.relationships import (
            RelationshipCollector,
        )
        from fb_crawl.adapters.browser.profiles import (
            ProfileEnricher,
        )
        from fb_crawl.adapters.browser.profile_uid import (
            ProfileUidResolver,
        )
        from fb_crawl.adapters.browser.session import (
            SessionStore,
        )
        from fb_crawl.adapters.browser.user_parser import (
            UserParser,
        )
        from fb_crawl.exporters.authenticated import (
            ensure_authenticated_format_available,
            write_authenticated,
        )
        from fb_crawl.services.authenticated import (
            AuthenticatedService,
        )
        from fb_crawl.services.checkpoint import (
            CheckpointingService,
        )

    except ModuleNotFoundError as error:
        dependency = str(error.name)

        if dependency == "selenium" or dependency.startswith("selenium."):
            raise ConfigurationError(
                "Authenticated mode requires: " 'python -m pip install -e ".[browser]"'
            ) from error

        if dependency == "bs4" or dependency.startswith("bs4."):
            raise ConfigurationError(
                "Authenticated mode requires: " 'python -m pip install -e ".[browser]"'
            ) from error

        raise

    def create_service(
        settings,
        credentials_provider,
    ):
        service = AuthenticatedService(
            SessionManager(
                SessionStore(settings.session_path),
                settings,
                credentials_provider,
            ),
            MembersCollector(settings),
            CommentsCollector(settings),
            UserParser(),
            ProfileEnricher(settings, ProfileParser()),
            relationships=RelationshipCollector(settings),
            reactions=ReactionsCollector(settings),
            relationship_parser=UserParser(allow_plain_profile_links=True),
            reaction_parser=ReactionParser(),
            uid_resolver=ProfileUidResolver(settings),
            messages=MessagesCollector(settings),
            message_parser=MessageParser(),
            inspector=BrowserInspector(settings),
        )
        return CheckpointingService(service)

    return AuthenticatedRuntime(
        create_browser=create_firefox_driver,
        create_service=create_service,
        ensure_format=ensure_authenticated_format_available,
        write_result=write_authenticated,
    )


def _credentials_provider() -> tuple[str, str]:
    email = input("Facebook email: ")
    password = getpass.getpass("Facebook password: ")

    return email, password


def execute_authenticated(
    args: argparse.Namespace,
) -> int:
    request = request_from_authenticated_args(args)

    if request.checkpoint_path is not None:
        checkpoint_path = validate_checkpoint_path(
            Path(request.checkpoint_path),
            repository_root=Path.cwd(),
        )
        request = replace(
            request,
            checkpoint_path=str(checkpoint_path),
        )

    settings = load_browser_settings(
        headless=args.headless,
        proxy=args.proxy,
        session_path=args.session_path,
        browser_timeout_seconds=args.browser_timeout,
        verification_timeout_seconds=(args.verification_timeout),
        repository_root=Path.cwd(),
    )

    runtime = _load_runtime()
    runtime.ensure_format(args.format)

    browser = None

    try:
        service = runtime.create_service(
            settings,
            _credentials_provider,
        )

        # Target phải hợp lệ trước khi khởi động Firefox.
        service.validate(request)

        browser = runtime.create_browser(settings)
        result = service.run(request, browser)

        action = AuthenticatedAction(args.action)
        output = args.output or (
            Path("runtime/output") / f"{DEFAULT_OUTPUTS[action]}.{args.format}"
        )

        written = runtime.write_result(
            result,
            output,
            args.format,
        )
        output_status = output if written else "unchanged"

        summary = (
            f"requested={result.stats.requested} "
            f"discovered={result.stats.discovered} "
            f"succeeded={result.stats.succeeded} "
            f"failed={result.stats.failed} "
            f"output={output_status}"
        )

        if action is AuthenticatedAction.BATCH:
            user_records = result.user_result.records
        elif action in {
            AuthenticatedAction.MESSAGES,
            AuthenticatedAction.INSPECT,
        }:
            user_records = ()
        else:
            user_records = result.records

        if user_records:
            uid_resolved = sum(
                record.user_id.isdigit() for record in user_records
            )
            summary += (
                f" uid_resolved={uid_resolved}"
                f" uid_unresolved={len(user_records) - uid_resolved}"
            )

        if result.enrichment is not None:
            summary += (
                f" enrichment_selected={result.enrichment.selected}"
                f" enrichment_succeeded={result.enrichment.succeeded}"
                f" enrichment_failed={result.enrichment.failed}"
                f" phone_found={result.enrichment.phone_found}"
                f" current_city_found={result.enrichment.current_city_found}"
                f" birth_year_found={result.enrichment.birth_year_found}"
            )

        print(summary)

        return 1 if result.has_failures else 0

    finally:
        if browser is not None:
            try:
                browser.quit()
            except Exception:
                pass
