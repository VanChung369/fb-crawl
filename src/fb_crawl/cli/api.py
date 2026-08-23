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
    return parser


def _require_database(settings: PipelineSettings) -> None:
    if not settings.database_url:
        raise DatabaseError("DATABASE_URL is required.")


def _raise_optional_dependency(error: ModuleNotFoundError) -> NoReturn:
    if error.name not in API_EXTRA_ROOTS:
        raise error
    raise ConfigurationError(API_EXTRA_MESSAGE) from error


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
    job_repository = JobRepository(
        pipeline_settings.database_url,
        statement_timeout_seconds=statement_timeout,
    )
    user_repository = UserQueryRepository(
        pipeline_settings.database_url,
        statement_timeout_seconds=statement_timeout,
    )
    migration_runner = MigrationRunner(pipeline_settings.database_url)
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
        pipeline_settings = load_pipeline_settings()
        _require_database(pipeline_settings)
        api_settings = load_api_settings(os.environ)
        try:
            application = _compose_api(pipeline_settings, api_settings)
        except ModuleNotFoundError as error:
            _raise_optional_dependency(error)

        # The optional server dependency is deliberately last: configuration
        # and the already-built application are both ready before it is loaded.
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
