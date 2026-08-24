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

from fb_crawl.config import BrowserSettings, load_browser_settings
from fb_crawl.core.exceptions import ConfigurationError
from fb_data_pipeline.config import PipelineSettings, load_pipeline_settings


EMPTY_QUEUE_POLL_SECONDS = 5.0


class _JobRepository(Protocol):
    def recover_stale_jobs(self) -> tuple[object, ...]: ...


class _CrawlWorker(Protocol):
    def run_once(self) -> bool: ...


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

    worker = CrawlWorker(
        repository,
        runtime_factory,
        worker_id=worker_id,
    )
    return repository, worker


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
    if concurrency > 1:
        return _execute_multiprocess_workers(concurrency, sleep=sleep)

    try:
        return _execute_worker_process(sleep=sleep)
    except KeyboardInterrupt:
        return 130


def _execute_multiprocess_workers(
    concurrency: int,
    *,
    sleep: Callable[[float], None] | None = None,
) -> int:
    import multiprocessing
    print(f"[INFO] Khởi chạy {concurrency} Worker tiến trình song song (Concurrency = {concurrency})...")
    processes: list[multiprocessing.Process] = []

    for i in range(concurrency):
        p = multiprocessing.Process(
            target=_execute_worker_process,
            kwargs={"sleep": sleep},
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
) -> int:
    pipeline_settings = load_pipeline_settings()
    pipeline_settings.require_database()
    pipeline_settings.require_fb_number()
    browser_settings = load_browser_settings()
    _require_saved_session(browser_settings)

    repository, worker = _compose_worker(
        pipeline_settings,
        browser_settings,
        worker_id=build_worker_id(),
    )
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
        print("[INFO] Background Crawl Worker started successfully.", flush=True)
        print(
            f"[INFO] Connected to Database: {_safe_database_label(pipeline_settings.database_url)}",
            flush=True,
        )
        print("[INFO] Polling for crawl jobs in the background (Press Ctrl+C to stop)...", flush=True)
        repository.recover_stale_jobs()
        while True:
            processed = worker.run_once()
            if interrupt_requested:
                return 130
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
