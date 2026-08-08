from __future__ import annotations

import argparse
import getpass
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fb_crawl.config import (
    BrowserSettings,
    load_browser_settings,
)
from fb_crawl.core.exceptions import (
    ConfigurationError,
    ValidationError,
)
from fb_crawl.core.models import (
    AuthenticatedAction,
    ScrapeMode,
    ScrapeRequest,
)

DEFAULT_OUTPUTS = {
    AuthenticatedAction.MEMBERS: "members",
    AuthenticatedAction.COMMENTS: "comments",
    AuthenticatedAction.BATCH: "batch",
}


def _common(
    parser: argparse.ArgumentParser,
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

    return ScrapeRequest(
        mode=ScrapeMode.AUTHENTICATED,
        action=action,
        targets=targets,
        steps=args.steps,
        delay_seconds=args.delay,
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
        from fb_crawl.adapters.browser.session import (
            SessionStore,
        )
        from fb_crawl.adapters.browser.user_parser import (
            UserParser,
        )
        from fb_crawl.exporters.users import (
            ensure_user_format_available,
            write_users,
        )
        from fb_crawl.services.authenticated import (
            AuthenticatedService,
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
        return AuthenticatedService(
            SessionManager(
                SessionStore(settings.session_path),
                settings,
                credentials_provider,
            ),
            MembersCollector(settings),
            CommentsCollector(settings),
            UserParser(),
        )

    return AuthenticatedRuntime(
        create_browser=create_firefox_driver,
        create_service=create_service,
        ensure_format=ensure_user_format_available,
        write_result=write_users,
    )


def _credentials_provider() -> tuple[str, str]:
    email = input("Facebook email: ")
    password = getpass.getpass("Facebook password: ")

    return email, password


def execute_authenticated(
    args: argparse.Namespace,
) -> int:
    request = request_from_authenticated_args(args)

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

        print(
            f"requested={result.stats.requested} "
            f"discovered={result.stats.discovered} "
            f"succeeded={result.stats.succeeded} "
            f"failed={result.stats.failed} "
            f"output={output_status}"
        )

        return 1 if result.has_failures else 0

    finally:
        if browser is not None:
            try:
                browser.quit()
            except Exception:
                pass
