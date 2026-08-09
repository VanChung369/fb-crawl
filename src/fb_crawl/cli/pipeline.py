from __future__ import annotations

import argparse
from datetime import timedelta

import psycopg

from fb_data_pipeline.config import load_pipeline_settings
from fb_data_pipeline.providers.fbnumber import FBNumberProvider
from fb_data_pipeline.repositories.errors import DatabaseError
from fb_data_pipeline.repositories.migrations import MigrationRunner
from fb_data_pipeline.repositories.postgres import PostgresRepository
from fb_data_pipeline.services.persistence import PipelinePersistenceService
from fb_data_pipeline.services.pipeline import EnrichmentPipeline
from fb_data_pipeline.services.retry import FBNumberRetryService, RetryReport


def add_pipeline_parser(
    modes: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    parser = modes.add_parser(
        "pipeline",
        help="Manage the PostgreSQL data pipeline.",
    )
    commands = parser.add_subparsers(
        dest="pipeline_command",
        required=True,
    )
    commands.add_parser(
        "migrate",
        help="Apply database migrations.",
    )
    retry = commands.add_parser(
        "retry",
        help="Retry durable FBNumber provider failures.",
    )
    retry.add_argument("--limit", type=int, default=20)
    retry.add_argument("--cooldown-hours", type=int, default=24)
    retry.add_argument("--force", action="store_true")
    retry.add_argument("--dry-run", action="store_true")
    return parser


def _execute_migrate() -> int:
    settings = load_pipeline_settings()
    settings.require_database()
    runner = MigrationRunner(settings.database_url)
    try:
        applied = runner.apply()
    except (psycopg.Error, OSError) as error:
        raise DatabaseError("Database operation failed.") from error

    names = ",".join(applied) if applied else "none"
    print(f"applied={names}")
    return 0


def _bool_text(value: bool) -> str:
    return str(value).lower()


def _print_retry_report(report: RetryReport) -> None:
    print(
        f"selected={report.selected} "
        f"persisted={report.persisted} "
        f"found={report.found} "
        f"not_found={report.not_found} "
        f"failed={report.failed} "
        f"rate_limited={report.rate_limited} "
        f"retry_pending={report.retry_pending} "
        f"database_failures={report.database_failures} "
        f"dry_run={_bool_text(report.dry_run)} "
        f"worker_busy={_bool_text(report.worker_busy)}"
    )


def _execute_retry(args: argparse.Namespace) -> int:
    if args.limit <= 0:
        raise ValueError("Pipeline retry limit must be positive.")
    if args.cooldown_hours < 0:
        raise ValueError(
            "Pipeline retry cooldown hours must be zero or greater."
        )

    settings = load_pipeline_settings()
    settings.require_database()
    repository = PostgresRepository(
        settings.database_url,
        statement_timeout_seconds=(
            settings.database_statement_timeout_seconds
        ),
    )
    run_options = {
        "limit": args.limit,
        "cooldown": timedelta(hours=args.cooldown_hours),
        "force": args.force,
        "dry_run": args.dry_run,
    }

    if args.dry_run:
        report = FBNumberRetryService(repository).run(**run_options)
        _print_retry_report(report)
        return report.exit_code

    settings.require_fb_number()
    provider = FBNumberProvider.from_settings(settings)
    try:
        service = FBNumberRetryService(
            repository,
            EnrichmentPipeline(provider),
            PipelinePersistenceService(repository),
        )
        try:
            report = service.run(**run_options)
        except KeyboardInterrupt:
            return 130
    finally:
        provider.close()

    _print_retry_report(report)
    return report.exit_code


def execute_pipeline(args: argparse.Namespace) -> int:
    if args.pipeline_command == "migrate":
        return _execute_migrate()
    if args.pipeline_command == "retry":
        return _execute_retry(args)
    raise ValueError(
        f"Unsupported pipeline command: {args.pipeline_command}"
    )
