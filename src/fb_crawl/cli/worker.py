"""Thin, non-interactive entry point for the durable crawl worker."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import os
from pathlib import Path
import signal
import socket
import time
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fb_crawl.config import BrowserSettings, load_browser_settings
from fb_crawl.core.exceptions import ConfigurationError
from fb_data_pipeline.config import PipelineSettings, load_pipeline_settings


EMPTY_QUEUE_POLL_SECONDS = 5.0


class _JobRepository(Protocol):
    def recover_stale_jobs(self) -> tuple[object, ...]: ...


class _CrawlWorker(Protocol):
    def run_once(self) -> bool: ...


class _WorkerPolicy(Protocol): ...


def add_worker_parser(
    modes: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    parser = modes.add_parser(
        "worker",
        help="Run the PostgreSQL-backed authenticated crawl worker.",
    )
    commands = parser.add_subparsers(
        dest="worker_command",
        required=True,
    )
    run_parser = commands.add_parser(
        "run",
        help="Poll and execute authenticated crawl jobs.",
    )
    run_parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=1,
        help="Number of concurrent worker processes to run (default: 1)",
    )
    run_parser.add_argument(
        "--kind",
        choices=("crawl", "export"),
        default="crawl",
        help="Queue to process (default: crawl)",
    )
    run_parser.add_argument(
        "--once",
        action="store_true",
        help="Perform one queue poll and exit",
    )
    return parser


def build_worker_id() -> str:
    """Return an opaque process-instance owner without user or path data."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"


def _require_saved_session(settings: BrowserSettings) -> None:
    session_path = Path(settings.session_path)
    try:
        valid = session_path.is_file()
    except OSError:
        valid = False

    if not valid:
        raise ConfigurationError(
            "FB_CRAWL_SESSION_PATH must reference an existing saved Facebook "
            "session file."
        )


def _safe_database_label(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if not parsed.scheme or not parsed.hostname:
        return "[configured]"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    database = parsed.path.rsplit("/", 1)[-1] if parsed.path else ""
    path = f"/{database}" if database else ""
    return urlunsplit((parsed.scheme, f"{host}{port}", path, "", ""))



def load_worker_policy() -> _WorkerPolicy:
    from fb_crawl.api.routes.settings import read_env_file_values
    from fb_crawl.services.worker import WorkerPolicy

    env_file = read_env_file_values()
    cooldown_str = os.environ.get("CRAWL_WORKER_COOLDOWN_SECONDS") or env_file.get("CRAWL_WORKER_COOLDOWN_SECONDS")
    delay_str = os.environ.get("CRAWL_WORKER_NAVIGATION_DELAY_SECONDS") or env_file.get("CRAWL_WORKER_NAVIGATION_DELAY_SECONDS")
    timeout_str = os.environ.get("CRAWL_WORKER_JOB_TIMEOUT_SECONDS") or env_file.get("CRAWL_WORKER_JOB_TIMEOUT_SECONDS")
    rate_limit_cooldown_str = os.environ.get("CRAWL_WORKER_RATE_LIMIT_COOLDOWN_SECONDS") or env_file.get("CRAWL_WORKER_RATE_LIMIT_COOLDOWN_SECONDS")

    cooldown = int(cooldown_str) if cooldown_str and cooldown_str.isdigit() else 3600
    delay = max(8, int(delay_str)) if delay_str and delay_str.isdigit() else 8
    timeout = min(1800, max(1, int(timeout_str))) if timeout_str and timeout_str.isdigit() else 1800
    rate_limit = int(rate_limit_cooldown_str) if rate_limit_cooldown_str and rate_limit_cooldown_str.isdigit() else 21600

    return WorkerPolicy(
        normal_cooldown_seconds=max(0, cooldown),
        navigation_interval_seconds=delay,
        job_timeout_seconds=timeout,
        rate_limit_cooldown_seconds=max(0, rate_limit),
    )


def _compose_worker(
    pipeline_settings: PipelineSettings,
    browser_settings: BrowserSettings,
    *,
    worker_id: str,
) -> tuple[_JobRepository, _CrawlWorker]:
    # Browser-facing composition stays behind validated process configuration.
    # The composition module itself is lazy; Selenium is imported only when a
    # claimed job opens its authenticated runtime.
    from fb_crawl.composition.authenticated import open_authenticated_job_session
    from fb_crawl.services.worker import CrawlWorker
    from fb_data_pipeline.repositories.jobs import JobRepository
    from fb_data_pipeline.repositories.migrations import MigrationRunner

    MigrationRunner(pipeline_settings.database_url).apply()

    repository = JobRepository(
        pipeline_settings.database_url,
        statement_timeout_seconds=(
            pipeline_settings.database_statement_timeout_seconds
        ),
    )

    try:
        with repository._connect() as cursor:
            cursor.execute(
                """
                INSERT INTO crawler_account_state (account_key, status)
                VALUES ('default', 'ready')
                ON CONFLICT (account_key) DO UPDATE SET status = 'ready';
                """
            )
            cursor.connection.commit()
    except Exception:
        pass

    def runtime_factory(control, navigation_pacer):
        return open_authenticated_job_session(
            browser_settings,
            pipeline_settings,
            control,
            navigation_pacer,
        )

    policy = load_worker_policy()
    result_sink = None
    if (
        pipeline_settings.fb_number_api_token
        and os.environ.get("LEAD_FINDER_JWT_SECRET", "").strip()
        and os.environ.get("LEAD_FINDER_TOKEN_HMAC_SECRET", "").strip()
    ):
        from fb_crawl.auth.config import load_auth_settings
        from fb_crawl.composition.product import compose_product_services
        from fb_crawl.product_jobs.results import ProductJobResultSink

        product = compose_product_services(
            pipeline_settings.database_url,
            load_auth_settings(os.environ),
            os.environ,
            statement_timeout_seconds=(
                pipeline_settings.database_statement_timeout_seconds
            ),
            pipeline_settings=pipeline_settings,
        )
        if (
            product.product_crawl_repository is not None
            and product.contact_lookup_service is not None
        ):
            result_sink = ProductJobResultSink(
                product.product_crawl_repository,
                product.account_repository,
                product.contact_lookup_service,
            )
    worker = CrawlWorker(
        repository,
        runtime_factory,
        worker_id=worker_id,
        policy=policy,
        result_sink=result_sink,
    )
    return repository, worker


def _compose_export_worker(
    pipeline_settings: PipelineSettings,
    *,
    worker_id: str,
) -> _CrawlWorker:
    from fb_crawl.entitlements.quota import (
        ContactQuotaService,
        PostgresContactQuotaRepository,
    )
    from fb_crawl.exports.artifacts import ExportArtifactStore
    from fb_crawl.exports.postgres import PostgresExportRepository
    from fb_crawl.exports.service import ExportWorker, HistoryExportSource
    from fb_crawl.history.postgres import PostgresHistoryRepository
    from fb_crawl.history.service import HistoryService
    from fb_data_pipeline.repositories.migrations import MigrationRunner

    MigrationRunner(pipeline_settings.database_url).apply()
    timeout = pipeline_settings.database_statement_timeout_seconds
    try:
        product_timezone = ZoneInfo(
            os.environ.get(
                "LEAD_FINDER_TIMEZONE", "Asia/Ho_Chi_Minh"
            ).strip()
        )
    except ZoneInfoNotFoundError as error:
        raise ConfigurationError(
            "LEAD_FINDER_TIMEZONE must name a valid IANA timezone."
        ) from error
    repository = PostgresExportRepository(
        pipeline_settings.database_url,
        statement_timeout_seconds=timeout,
    )
    history_repository = PostgresHistoryRepository(
        pipeline_settings.database_url,
        statement_timeout_seconds=timeout,
    )
    quota = ContactQuotaService(
        PostgresContactQuotaRepository(
            pipeline_settings.database_url,
            statement_timeout_seconds=timeout,
        ),
        product_timezone,
    )
    history = HistoryService(history_repository, quota)
    artifacts = ExportArtifactStore(
        os.environ.get(
            "LEAD_FINDER_EXPORT_DIR", "runtime/lead-finder-exports"
        )
    )
    return ExportWorker(
        repository,
        HistoryExportSource(history),
        artifacts,
        worker_id=worker_id,
    )


def execute_worker(
    args: argparse.Namespace,
    *,
    sleep: Callable[[float], None] | None = None,
) -> int:
    if args.worker_command != "run":
        raise ValueError(
            f"Unsupported worker command: {args.worker_command}"
        )

    concurrency = getattr(args, "concurrency", 1) or 1
    kind = getattr(args, "kind", "crawl") or "crawl"
    once = bool(getattr(args, "once", False))
    if concurrency > 1:
        return _execute_multiprocess_workers(
            concurrency, kind=kind, once=once, sleep=sleep
        )

    try:
        return _execute_worker_process(sleep=sleep, kind=kind, once=once)
    except KeyboardInterrupt:
        return 130


def _execute_multiprocess_workers(
    concurrency: int,
    *,
    kind: str = "crawl",
    once: bool = False,
    sleep: Callable[[float], None] | None = None,
) -> int:
    import multiprocessing
    print(f"[INFO] Khởi chạy {concurrency} Worker tiến trình song song (Concurrency = {concurrency})...")
    processes: list[multiprocessing.Process] = []

    for i in range(concurrency):
        p = multiprocessing.Process(
            target=_execute_worker_process,
            kwargs={"sleep": sleep, "kind": kind, "once": once},
            name=f"WorkerProcess-{i+1}",
        )
        p.start()
        processes.append(p)

    try:
        for p in processes:
            p.join()
        return 0
    except KeyboardInterrupt:
        print("\n[INFO] Đang dừng tất cả các Worker song song...")
        for p in processes:
            p.terminate()
            p.join(timeout=2.0)
        return 130


def _execute_worker_process(
    *,
    sleep: Callable[[float], None] | None,
    kind: str = "crawl",
    once: bool = False,
) -> int:
    pipeline_settings = load_pipeline_settings()
    pipeline_settings.require_database()
    if kind == "export":
        repository = None
        worker = _compose_export_worker(
            pipeline_settings,
            worker_id=build_worker_id(),
        )
    elif kind == "crawl":
        pipeline_settings.require_fb_number()
        browser_settings = load_browser_settings()
        _require_saved_session(browser_settings)
        repository, worker = _compose_worker(
            pipeline_settings,
            browser_settings,
            worker_id=build_worker_id(),
        )
    else:
        raise ValueError(f"Unsupported worker kind: {kind}")
    wait = time.sleep if sleep is None else sleep

    interrupt_requested = False
    previous_handler: object | None = None

    def handle_interrupt(_signum: int, _frame: object) -> None:
        nonlocal interrupt_requested
        interrupt_requested = True
        raise KeyboardInterrupt

    try:
        previous_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, handle_interrupt)
    except (OSError, ValueError):
        # Signal handlers are restricted to the main interpreter thread. The
        # normal KeyboardInterrupt catch still covers embedded/test runners.
        previous_handler = None

    try:
        print(
            f"[INFO] Background {kind.title()} Worker started successfully.",
            flush=True,
        )
        print(
            f"[INFO] Connected to Database: {_safe_database_label(pipeline_settings.database_url)}",
            flush=True,
        )
        print(
            f"[INFO] Polling for {kind} jobs in the background "
            "(Press Ctrl+C to stop)...",
            flush=True,
        )
        if repository is not None:
            repository.recover_stale_jobs()
        while True:
            processed = worker.run_once()
            if interrupt_requested:
                return 130
            if once:
                return 0
            if not processed:
                wait(EMPTY_QUEUE_POLL_SECONDS)
    finally:
        if previous_handler is not None:
            signal.signal(signal.SIGINT, previous_handler)


__all__ = [
    "add_worker_parser",
    "build_worker_id",
    "execute_worker",
]
