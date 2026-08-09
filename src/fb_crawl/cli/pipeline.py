from __future__ import annotations

import argparse

import psycopg

from fb_data_pipeline.config import load_pipeline_settings
from fb_data_pipeline.repositories.errors import DatabaseError
from fb_data_pipeline.repositories.migrations import MigrationRunner


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
    return parser


def execute_pipeline(args: argparse.Namespace) -> int:
    if args.pipeline_command != "migrate":
        raise ValueError(
            f"Unsupported pipeline command: {args.pipeline_command}"
        )

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
