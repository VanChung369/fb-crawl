from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import getpass
import os
from typing import Any

from fb_crawl.accounts.models import Account
from fb_crawl.core.exceptions import ConfigurationError, ValidationError


def add_admin_parser(
    modes: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    parser = modes.add_parser(
        "admin",
        help="Bootstrap and maintain Lead Finder administrators.",
    )
    commands = parser.add_subparsers(dest="admin_command", required=True)
    bootstrap = commands.add_parser(
        "bootstrap",
        help="Create the first verified administrator.",
    )
    bootstrap.add_argument("--email", required=True)
    commands.add_parser(
        "purge-deleted-accounts",
        help="Purge accounts whose recovery window has elapsed.",
    )
    return parser


class AdminCommand:
    def __init__(self, repository: Any, password_hasher: Any) -> None:
        self._repository = repository
        self._password_hasher = password_hasher

    def bootstrap(
        self, email: str, password: str, now: datetime
    ) -> Account:
        normalized, display = _normalized_email(email)
        _validate_password(password)
        return self._repository.bootstrap_admin(
            normalized,
            display,
            self._password_hasher.hash(password),
            now,
        )

    def purge_deleted(self, *, now: datetime, recovery_days: int) -> int:
        if (
            isinstance(recovery_days, bool)
            or not isinstance(recovery_days, int)
            or not 1 <= recovery_days <= 3660
        ):
            raise ValueError("recovery_days must be from 1 to 3660")
        purged = self._repository.purge_due_deleted_accounts(
            now - timedelta(days=recovery_days)
        )
        return len(purged)


def execute_admin(
    args: argparse.Namespace,
    *,
    env: Mapping[str, str] | None = None,
    clock=lambda: datetime.now(UTC),
) -> int:
    from fb_crawl.accounts.postgres import PostgresAccountRepository
    from fb_crawl.auth.passwords import PasswordHasher
    from fb_data_pipeline.config import load_pipeline_settings
    from fb_data_pipeline.repositories.migrations import MigrationRunner

    values = os.environ if env is None else env
    pipeline_settings = load_pipeline_settings(values)
    pipeline_settings.require_database()
    MigrationRunner(pipeline_settings.database_url).apply()
    repository = PostgresAccountRepository(
        pipeline_settings.database_url,
        statement_timeout_seconds=(
            pipeline_settings.database_statement_timeout_seconds
        ),
    )
    command = AdminCommand(repository, PasswordHasher())

    if args.admin_command == "bootstrap":
        password = getpass.getpass("Admin password: ")
        confirmation = getpass.getpass("Confirm admin password: ")
        if password != confirmation:
            raise ValidationError("Passwords do not match.")
        admin = command.bootstrap(args.email, password, clock())
        print(f"Created administrator #{admin.id}: {admin.display_email}")
        return 0

    if args.admin_command == "purge-deleted-accounts":
        recovery_days = _recovery_days(
            values.get("LEAD_FINDER_ACCOUNT_RECOVERY_DAYS", "30")
        )
        count = command.purge_deleted(
            now=clock(), recovery_days=recovery_days
        )
        print(f"Purged {count} deleted account(s).")
        return 0

    raise ValueError(f"Unsupported admin command: {args.admin_command}")


def _normalized_email(value: object) -> tuple[str, str]:
    from email_validator import EmailNotValidError, validate_email

    if not isinstance(value, str):
        raise ValidationError("A valid email address is required.")
    try:
        validated = validate_email(value.strip(), check_deliverability=False)
    except EmailNotValidError as error:
        raise ValidationError("A valid email address is required.") from error
    display = validated.normalized
    return display.casefold(), display


def _validate_password(value: object) -> None:
    if not isinstance(value, str) or not 12 <= len(value) <= 128:
        raise ValidationError("Password must contain 12 to 128 characters.")


def _recovery_days(value: object) -> int:
    if not isinstance(value, str):
        raise ConfigurationError(
            "LEAD_FINDER_ACCOUNT_RECOVERY_DAYS must be an integer."
        )
    try:
        parsed = int(value)
    except ValueError as error:
        raise ConfigurationError(
            "LEAD_FINDER_ACCOUNT_RECOVERY_DAYS must be an integer."
        ) from error
    if not 1 <= parsed <= 3660:
        raise ConfigurationError(
            "LEAD_FINDER_ACCOUNT_RECOVERY_DAYS must be from 1 to 3660."
        )
    return parsed
