from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import ConfigurationError
from fb_crawl.cli import app
from fb_crawl.cli.worker import _require_saved_session


def test_worker_run_parser_builds_only_the_run_command() -> None:
    """Break caught: the process entry point is absent or exposes another command."""
    args = app.build_parser().parse_args(["worker", "run"])

    assert args.mode == "worker"
    assert args.worker_command == "run"
    assert args.kind == "crawl"
    assert args.once is False


def test_worker_parser_supports_single_poll_export_mode() -> None:
    args = app.build_parser().parse_args(
        ["worker", "run", "--kind", "export", "--once"]
    )

    assert args.kind == "export"
    assert args.once is True


def test_worker_run_help_exposes_no_safety_or_process_bypass(capsys) -> None:
    """Break caught: operators can relax server safety or daemonize from the CLI."""
    with pytest.raises(SystemExit) as captured:
        app.build_parser().parse_args(["worker", "run", "--help"])

    output = capsys.readouterr().out

    assert captured.value.code == 0
    assert "usage: fb-crawl worker run" in output
    for forbidden in (
        "--unsafe",
        "--cooldown",
        "--navigation-interval",
        "--max-duration",
        "--session-path",
        "--daemon",
        "--reload",
    ):
        assert forbidden not in output


def test_main_parser_builds_without_importing_selenium() -> None:
    """Break caught: displaying worker help starts importing browser extras."""
    script = """
import importlib.abc
import sys

class BlockSelenium(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'selenium' or fullname.startswith('selenium.'):
            raise ModuleNotFoundError('selenium intentionally unavailable')
        return None

sys.meta_path.insert(0, BlockSelenium())
from fb_crawl.cli.app import build_parser
args = build_parser().parse_args(['worker', 'run'])
assert (args.mode, args.worker_command) == ('worker', 'run')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[3],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_module_entrypoint_renders_worker_run_help() -> None:
    """Break caught: ``python -m`` imports app.py but never invokes main()."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fb_crawl.cli.app",
            "worker",
            "run",
            "--help",
        ],
        cwd=Path(__file__).parents[3],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage: fb-crawl worker run" in result.stdout


def test_main_dispatches_worker_run(monkeypatch) -> None:
    """Break caught: the parser accepts worker mode but main never executes it."""
    seen: list[str] = []

    def execute(args) -> int:
        seen.append(args.worker_command)
        return 17

    monkeypatch.setattr(app, "execute_worker", execute)

    assert app.main(["worker", "run"]) == 17
    assert seen == ["run"]


def test_worker_requires_exact_configured_session_without_pool_autocopy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: the worker silently copies another account's cookies."""

    monkeypatch.chdir(tmp_path)
    pool_session = tmp_path / "runtime" / "sessions" / "other-account.json"
    pool_session.parent.mkdir(parents=True)
    pool_session.write_text("[]", encoding="utf-8")
    configured = tmp_path / "runtime" / "session.json"

    with pytest.raises(ConfigurationError, match="existing saved Facebook session"):
        _require_saved_session(BrowserSettings(session_path=configured))

    assert not configured.exists()
