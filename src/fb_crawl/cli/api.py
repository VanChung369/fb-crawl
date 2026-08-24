"""Thin, lazy entry point for the PostgreSQL-backed HTTP API process."""

from __future__ import annotations

import argparse
import os
from typing import NoReturn

from fb_crawl.api.config import ApiSettings, load_api_settings
from fb_crawl.core.exceptions import ConfigurationError
from fb_data_pipeline.config import PipelineSettings, load_pipeline_settings
from fb_data_pipeline.repositories.errors import DatabaseError


API_EXTRA_ROOTS = frozenset({"fastapi", "pydantic", "uvicorn"})
API_EXTRA_MESSAGE = 'API mode requires: python -m pip install -e ".[api]"'


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be 1 through 65535")
    return port


def add_api_parser(
    modes: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    parser = modes.add_parser(
        "api",
        help="Serve the PostgreSQL-backed crawl job API.",
    )
    commands = parser.add_subparsers(
        dest="api_command",
        required=True,
    )
    serve = commands.add_parser(
        "serve",
        help="Serve job and persisted-user endpoints.",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=_port, default=8000)
    serve.add_argument("--dev", action="store_true", help="Run in standalone dev/mock mode without requiring PostgreSQL")
    return parser


def _require_database(settings: PipelineSettings) -> None:
    if not settings.database_url:
        raise DatabaseError("DATABASE_URL is required.")


def _raise_optional_dependency(error: ModuleNotFoundError) -> NoReturn:
    if error.name not in API_EXTRA_ROOTS:
        raise error
    raise ConfigurationError(API_EXTRA_MESSAGE) from error


def _compose_dev_api():
    """Build a standalone dev API application with mock repositories."""
    from unittest.mock import MagicMock
    from fb_crawl.api.app import create_app
    from fb_crawl.core.jobs import Page

    user_repo = MagicMock()
    user_repo.list_users.return_value = Page(items=[], next_cursor=None)
    job_repo = MagicMock()
    job_repo.list_jobs.return_value = Page(items=[], next_cursor=None)

    dev_settings = ApiSettings(
        api_key=os.environ.get("API_KEY", "dev-api-key-1234567890123456789012"),
        docs_enabled=True,
    )
    return create_app(
        dev_settings,
        job_service=MagicMock(),
        job_repository=job_repo,
        user_repository=user_repo,
        readiness=lambda m: True,
    )


def _compose_api(
    pipeline_settings: PipelineSettings,
    api_settings: ApiSettings,
):
    """Build real API dependencies without importing any browser runtime."""
    from fb_crawl.api.app import create_app
    from fb_crawl.services.jobs import JobService
    from fb_data_pipeline.repositories.jobs import JobRepository
    from fb_data_pipeline.repositories.migrations import MigrationRunner
    from fb_data_pipeline.repositories.users import UserQueryRepository

    statement_timeout = pipeline_settings.database_statement_timeout_seconds
    migration_runner = MigrationRunner(pipeline_settings.database_url)
    migration_runner.apply()

    job_repository = JobRepository(
        pipeline_settings.database_url,
        statement_timeout_seconds=statement_timeout,
    )
    user_repository = UserQueryRepository(
        pipeline_settings.database_url,
        statement_timeout_seconds=statement_timeout,
    )
    return create_app(
        api_settings,
        JobService(job_repository),
        job_repository,
        user_repository,
        migration_runner.is_applied,
    )


def execute_api(args: argparse.Namespace) -> int:
    if args.api_command != "serve":
        raise ValueError(f"Unsupported API command: {args.api_command}")

    try:
        if getattr(args, "dev", False):
            print(f"[INFO] Running in DEV / MOCK mode at http://{args.host}:{args.port}")
            print(f"[INFO] Default API Key: {os.environ.get('API_KEY', 'dev-api-key-1234567890123456789012')}")
            application = _compose_dev_api()
        else:
            pipeline_settings = load_pipeline_settings()
            _require_database(pipeline_settings)
            api_settings = load_api_settings(os.environ)
            try:
                application = _compose_api(pipeline_settings, api_settings)
            except ModuleNotFoundError as error:
                _raise_optional_dependency(error)

        try:
            import uvicorn
        except ModuleNotFoundError as error:
            _raise_optional_dependency(error)

        uvicorn.run(
            application,
            host=args.host,
            port=args.port,
            reload=False,
        )
        return 0
    except KeyboardInterrupt:
        return 130


__all__ = ["add_api_parser", "execute_api"]
