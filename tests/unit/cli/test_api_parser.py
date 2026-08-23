from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from fb_crawl.cli import app


def test_api_serve_parser_has_loopback_defaults_and_bounded_overrides() -> None:
    """Break caught: API defaults become externally exposed or ignore operators."""
    defaults = app.build_parser().parse_args(["api", "serve"])
    overridden = app.build_parser().parse_args(
        ["api", "serve", "--host", "10.0.0.7", "--port", "9010"]
    )

    assert (defaults.mode, defaults.api_command) == ("api", "serve")
    assert (defaults.host, defaults.port) == ("127.0.0.1", 8000)
    assert (overridden.host, overridden.port) == ("10.0.0.7", 9010)


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
def test_api_parser_rejects_invalid_ports(port: str) -> None:
    """Break caught: an invalid TCP port reaches Uvicorn startup."""
    with pytest.raises(SystemExit) as captured:
        app.build_parser().parse_args(["api", "serve", "--port", port])

    assert captured.value.code == 2


def test_api_help_exposes_no_worker_browser_or_safety_controls(capsys) -> None:
    """Break caught: the API command gains browser ownership or safety bypasses."""
    with pytest.raises(SystemExit) as captured:
        app.build_parser().parse_args(["api", "serve", "--help"])

    output = capsys.readouterr().out

    assert captured.value.code == 0
    assert "usage: fb-crawl api serve" in output
    for forbidden in (
        "--reload",
        "--workers",
        "--session-path",
        "--proxy",
        "--unsafe",
        "--cooldown",
        "--navigation-interval",
        "--max-duration",
    ):
        assert forbidden not in output


def test_api_parser_and_help_import_no_framework_or_browser_dependencies() -> None:
    """Break caught: CLI discovery requires optional API/browser dependencies."""
    script = r'''
import importlib.abc
import sys

BLOCKED = (
    "fastapi",
    "pydantic",
    "uvicorn",
    "selenium",
    "fb_crawl.adapters.browser",
)

class BlockOptionalRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in BLOCKED):
            raise AssertionError(f"optional runtime import attempted: {fullname}")
        return None

sys.meta_path.insert(0, BlockOptionalRuntime())
from fb_crawl.cli.app import build_parser
args = build_parser().parse_args(["api", "serve"])
assert (args.host, args.port) == ("127.0.0.1", 8000)
try:
    build_parser().parse_args(["api", "serve", "--help"])
except SystemExit as error:
    assert error.code == 0
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[3],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_main_dispatches_api_serve(monkeypatch) -> None:
    """Break caught: the parser accepts API mode but main never serves it."""
    seen: list[tuple[str, str, int]] = []

    def execute(args) -> int:
        seen.append((args.api_command, args.host, args.port))
        return 29

    monkeypatch.setattr(app, "execute_api", execute)

    assert app.main(["api", "serve", "--host", "127.0.0.2", "--port", "8123"]) == 29
    assert seen == [("serve", "127.0.0.2", 8123)]
