from __future__ import annotations

import argparse
from pathlib import Path
import signal
from uuid import UUID

import pytest

from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import ConfigurationError


class PipelineSettingsDouble:
    database_url = "postgresql://configured"
    database_statement_timeout_seconds = 7.5

    def __init__(self, events: list[str], *, fail: str | None = None) -> None:
        self.events = events
        self.fail = fail

    def require_database(self) -> None:
        self.events.append("require_database")
        if self.fail == "database":
            raise ConfigurationError("DATABASE_URL is required.")

    def require_fb_number(self) -> None:
        self.events.append("require_fb_number")
        if self.fail == "fb_number":
            raise ConfigurationError("FB_NUMBER_API_TOKEN is required.")


class RepositoryDouble:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def recover_stale_jobs(self) -> tuple[object, ...]:
        self.events.append("recover_stale")
        return ()


class WorkerDouble:
    def __init__(self, events: list[str], results: list[bool | BaseException]) -> None:
        self.events = events
        self.results = list(results)

    def run_once(self) -> bool:
        self.events.append("run_once")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _run_args() -> argparse.Namespace:
    return argparse.Namespace(worker_command="run")


def _configured_startup(monkeypatch, worker_cli, events: list[str]):
    pipeline = PipelineSettingsDouble(events)
    browser = BrowserSettings(session_path=Path(__file__))
    monkeypatch.setattr(worker_cli, "load_pipeline_settings", lambda: pipeline)
    monkeypatch.setattr(worker_cli, "load_browser_settings", lambda: browser)
    return pipeline, browser


@pytest.mark.parametrize("failed_requirement", ["database", "fb_number"])
def test_worker_validates_pipeline_before_loading_browser_runtime(
    monkeypatch,
    failed_requirement: str,
) -> None:
    """Break caught: invalid DB/provider config reaches browser/runtime imports."""
    from fb_crawl.cli import worker as worker_cli

    events: list[str] = []
    pipeline = PipelineSettingsDouble(events, fail=failed_requirement)
    monkeypatch.setattr(worker_cli, "load_pipeline_settings", lambda: pipeline)
    monkeypatch.setattr(
        worker_cli,
        "load_browser_settings",
        lambda: (_ for _ in ()).throw(AssertionError("browser settings loaded")),
    )
    monkeypatch.setattr(
        worker_cli,
        "_compose_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("runtime dependencies loaded")
        ),
    )

    with pytest.raises(ConfigurationError):
        worker_cli.execute_worker(_run_args(), sleep=lambda _seconds: None)

    expected = ["require_database"]
    if failed_requirement == "fb_number":
        expected.append("require_fb_number")
    assert events == expected


def test_worker_rejects_missing_saved_session_before_runtime_imports(
    monkeypatch,
) -> None:
    """Break caught: Selenium starts before the saved-session path is validated."""
    from fb_crawl.cli import worker as worker_cli

    events: list[str] = []
    pipeline = PipelineSettingsDouble(events)
    browser = BrowserSettings(
        session_path=Path(__file__).with_name("missing-worker-session.json")
    )
    monkeypatch.setattr(worker_cli, "load_pipeline_settings", lambda: pipeline)
    monkeypatch.setattr(worker_cli, "load_browser_settings", lambda: browser)
    monkeypatch.setattr(
        worker_cli,
        "_compose_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("runtime dependencies loaded")
        ),
    )

    with pytest.raises(ConfigurationError, match="saved Facebook session"):
        worker_cli.execute_worker(_run_args(), sleep=lambda _seconds: None)

    assert events == ["require_database", "require_fb_number"]


def test_worker_recovers_once_and_only_sleeps_after_empty_poll(
    monkeypatch,
) -> None:
    """Break caught: stale recovery repeats or completed work incurs poll delay."""
    from fb_crawl.cli import worker as worker_cli

    events: list[str] = []
    pipeline, browser = _configured_startup(monkeypatch, worker_cli, events)
    repository = RepositoryDouble(events)
    worker = WorkerDouble(events, [False, True, KeyboardInterrupt()])

    def compose(pipeline_settings, browser_settings, *, worker_id: str):
        events.append("compose")
        assert pipeline_settings is pipeline
        assert browser_settings is browser
        assert worker_id
        return repository, worker

    monkeypatch.setattr(worker_cli, "_compose_worker", compose)

    def sleep(seconds: float) -> None:
        events.append(f"sleep:{seconds:g}")

    original_handler = signal.getsignal(signal.SIGINT)
    result = worker_cli.execute_worker(_run_args(), sleep=sleep)

    assert result == 130
    assert signal.getsignal(signal.SIGINT) is original_handler
    assert events == [
        "require_database",
        "require_fb_number",
        "compose",
        "recover_stale",
        "run_once",
        "sleep:5",
        "run_once",
        "run_once",
    ]


def test_worker_id_contains_only_host_pid_and_random_instance(monkeypatch) -> None:
    """Break caught: worker ownership IDs leak a username or filesystem path."""
    from fb_crawl.cli import worker as worker_cli

    monkeypatch.setattr(worker_cli.socket, "gethostname", lambda: "crawl-host")
    monkeypatch.setattr(worker_cli.os, "getpid", lambda: 4321)
    monkeypatch.setattr(
        worker_cli,
        "uuid4",
        lambda: UUID("01234567-89ab-cdef-0123-456789abcdef"),
    )

    assert (
        worker_cli.build_worker_id()
        == "crawl-host:4321:0123456789abcdef0123456789abcdef"
    )


def test_worker_exits_after_service_cleanup_swallows_keyboard_interrupt(
    monkeypatch,
) -> None:
    """Break caught: Ctrl+C cancels one job but the process claims another job."""
    from fb_crawl.cli import worker as worker_cli

    events: list[str] = []
    _configured_startup(monkeypatch, worker_cli, events)
    repository = RepositoryDouble(events)

    class CleanupAwareWorker:
        calls = 0

        def run_once(self) -> bool:
            self.calls += 1
            handler = signal.getsignal(signal.SIGINT)
            assert callable(handler)
            try:
                handler(signal.SIGINT, None)
            except KeyboardInterrupt:
                events.append("cleanup_finished")
                return True
            raise AssertionError("SIGINT handler did not interrupt active work")

    worker = CleanupAwareWorker()
    monkeypatch.setattr(
        worker_cli,
        "_compose_worker",
        lambda *_args, **_kwargs: (repository, worker),
    )

    original_handler = signal.getsignal(signal.SIGINT)
    result = worker_cli.execute_worker(_run_args(), sleep=lambda _seconds: None)

    assert result == 130
    assert worker.calls == 1
    assert events[-1] == "cleanup_finished"
    assert signal.getsignal(signal.SIGINT) is original_handler


def test_keyboard_interrupt_while_loading_configuration_returns_130(
    monkeypatch,
) -> None:
    """Break caught: Ctrl+C before runtime composition escapes the CLI boundary."""
    from fb_crawl.cli import worker as worker_cli

    original_handler = signal.getsignal(signal.SIGINT)
    monkeypatch.setattr(
        worker_cli,
        "load_pipeline_settings",
        lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = worker_cli.execute_worker(_run_args(), sleep=lambda _seconds: None)

    assert result == 130
    assert signal.getsignal(signal.SIGINT) is original_handler


def test_keyboard_interrupt_while_composing_worker_returns_130(
    monkeypatch,
) -> None:
    """Break caught: Ctrl+C during lazy dependency composition escapes startup."""
    from fb_crawl.cli import worker as worker_cli

    events: list[str] = []
    _configured_startup(monkeypatch, worker_cli, events)
    monkeypatch.setattr(
        worker_cli,
        "_compose_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    original_handler = signal.getsignal(signal.SIGINT)

    result = worker_cli.execute_worker(_run_args(), sleep=lambda _seconds: None)

    assert result == 130
    assert signal.getsignal(signal.SIGINT) is original_handler


def test_keyboard_interrupt_during_stale_recovery_returns_130_and_restores_signal(
    monkeypatch,
) -> None:
    """Break caught: startup recovery interrupts leak the temporary SIGINT handler."""
    from fb_crawl.cli import worker as worker_cli

    events: list[str] = []
    _configured_startup(monkeypatch, worker_cli, events)

    class InterruptedRepository(RepositoryDouble):
        def recover_stale_jobs(self) -> tuple[object, ...]:
            raise KeyboardInterrupt

    repository = InterruptedRepository(events)
    worker = WorkerDouble(events, [True])
    monkeypatch.setattr(
        worker_cli,
        "_compose_worker",
        lambda *_args, **_kwargs: (repository, worker),
    )
    original_handler = signal.getsignal(signal.SIGINT)

    result = worker_cli.execute_worker(_run_args(), sleep=lambda _seconds: None)

    assert result == 130
    assert signal.getsignal(signal.SIGINT) is original_handler


def test_keyboard_interrupt_during_empty_queue_sleep_returns_130_and_restores_signal(
    monkeypatch,
) -> None:
    """Break caught: Ctrl+C during the five-second poll wait escapes cleanup."""
    from fb_crawl.cli import worker as worker_cli

    events: list[str] = []
    _configured_startup(monkeypatch, worker_cli, events)
    repository = RepositoryDouble(events)
    worker = WorkerDouble(events, [False])
    monkeypatch.setattr(
        worker_cli,
        "_compose_worker",
        lambda *_args, **_kwargs: (repository, worker),
    )
    original_handler = signal.getsignal(signal.SIGINT)

    result = worker_cli.execute_worker(
        _run_args(),
        sleep=lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    assert result == 130
    assert signal.getsignal(signal.SIGINT) is original_handler


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("startup failed"), SystemExit(19)],
)
def test_unexpected_startup_failures_propagate_without_changing_signal_handler(
    monkeypatch,
    failure: BaseException,
) -> None:
    """Break caught: graceful interrupt handling swallows non-interrupt startup failures."""
    from fb_crawl.cli import worker as worker_cli

    events: list[str] = []
    _configured_startup(monkeypatch, worker_cli, events)
    monkeypatch.setattr(
        worker_cli,
        "_compose_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )
    original_handler = signal.getsignal(signal.SIGINT)

    with pytest.raises(type(failure)) as captured:
        worker_cli.execute_worker(_run_args(), sleep=lambda _seconds: None)

    assert captured.value is failure
    assert signal.getsignal(signal.SIGINT) is original_handler


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("loop failed"), SystemExit(23)],
)
def test_unexpected_loop_failures_propagate_and_restore_signal_handler(
    monkeypatch,
    failure: BaseException,
) -> None:
    """Break caught: loop cleanup masks ordinary exceptions or SystemExit."""
    from fb_crawl.cli import worker as worker_cli

    events: list[str] = []
    _configured_startup(monkeypatch, worker_cli, events)
    repository = RepositoryDouble(events)
    worker = WorkerDouble(events, [failure])
    monkeypatch.setattr(
        worker_cli,
        "_compose_worker",
        lambda *_args, **_kwargs: (repository, worker),
    )
    original_handler = signal.getsignal(signal.SIGINT)

    with pytest.raises(type(failure)) as captured:
        worker_cli.execute_worker(_run_args(), sleep=lambda _seconds: None)

    assert captured.value is failure
    assert signal.getsignal(signal.SIGINT) is original_handler


def test_worker_rejects_unknown_command_before_configuration(monkeypatch) -> None:
    """Break caught: an unsupported worker command can initialize resources."""
    from fb_crawl.cli import worker as worker_cli

    monkeypatch.setattr(
        worker_cli,
        "load_pipeline_settings",
        lambda: (_ for _ in ()).throw(AssertionError("configuration loaded")),
    )

    with pytest.raises(ValueError, match="Unsupported worker command"):
        worker_cli.execute_worker(
            argparse.Namespace(worker_command="other"),
            sleep=lambda _seconds: None,
        )


def test_export_worker_mode_requires_no_provider_browser_or_saved_session(
    monkeypatch,
) -> None:
    from fb_crawl.cli import worker as worker_cli

    events: list[str] = []
    pipeline = PipelineSettingsDouble(events)
    worker = WorkerDouble(events, [False])
    monkeypatch.setattr(worker_cli, "load_pipeline_settings", lambda: pipeline)
    monkeypatch.setattr(
        worker_cli,
        "load_browser_settings",
        lambda: (_ for _ in ()).throw(AssertionError("browser loaded")),
    )
    monkeypatch.setattr(
        worker_cli,
        "_compose_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("crawl composition loaded")
        ),
    )

    def compose(settings, *, worker_id):
        events.append("compose_export")
        assert settings is pipeline
        assert worker_id
        return worker

    monkeypatch.setattr(worker_cli, "_compose_export_worker", compose)

    result = worker_cli.execute_worker(
        argparse.Namespace(
            worker_command="run",
            concurrency=1,
            kind="export",
            once=True,
        ),
        sleep=lambda _seconds: None,
    )

    assert result == 0
    assert events == ["require_database", "compose_export", "run_once"]
