import ast
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

TEXT_SUFFIXES = frozenset(
    {
        "",
        ".cfg",
        ".csv",
        ".example",
        ".html",
        ".ini",
        ".json",
        ".md",
        ".ps1",
        ".py",
        ".sql",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
SECRET_ASSIGNMENT = re.compile(
    r"(?im)^[ \t]*(FB_CRAWL_API_KEY|FB_NUMBER_API_TOKEN)[ \t]*[:=][ \t]*"
    r"[\"']?([^\s\"',#]*)"
)
DIRECT_SESSION_COOKIE = re.compile(
    r"(?i)[\"'](?:c_user|xs)[\"']\s*:\s*[\"']([^\"']+)[\"']"
)
SELENIUM_SESSION_COOKIE = re.compile(
    r"(?is)[\"']name[\"']\s*:\s*[\"'](?:c_user|xs)[\"']"
    r"[^}\]]{0,512}?[\"']value[\"']\s*:\s*[\"']([^\"']+)[\"']"
)
COOKIE_HEADER_VALUE = re.compile(
    r"(?i)(?:^|[; \"'])(?:c_user|xs)=([^;\s\"']+)"
)
PLACEHOLDER_PARTS = (
    "example",
    "placeholder",
    "replace",
    "test",
    "dummy",
    "changeme",
    "<",
    "${",
    "$env:",
)


def test_runtime_and_secret_paths_are_ignored() -> None:
    for relative in (
        ".env",
        ".env.local",
        "runtime/output/pages.csv",
        "runtime/output/members.csv",
        "runtime/output/comments.json",
        "runtime/output/batch.xlsx",
        "runtime/session.json",
        "runtime/session.json.tmp",
        "runtime/geckodriver.log",
        "runtime/firefox.log",
    ):
        result = subprocess.run(
            [
                "git",
                "check-ignore",
                "-q",
                relative,
            ],
            cwd=ROOT,
            check=False,
        )

        assert result.returncode == 0, relative


def test_source_projects_remain_outside_new_repository() -> None:
    tracked = subprocess.run(
        [
            "git",
            "ls-files",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert ".facebook_session.json" not in tracked
    assert "results.csv" not in tracked
    assert ".ipynb" not in tracked
    assert "runtime/session.json" not in tracked
    assert "runtime/session.json.tmp" not in tracked
    assert "runtime/geckodriver.log" not in tracked
    assert "runtime/firefox.log" not in tracked


def _tracked_and_source_paths() -> tuple[Path, ...]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8").split("\0")
    paths = {
        (ROOT / relative).resolve()
        for relative in tracked
        if relative
    }
    paths.update(path.resolve() for path in (ROOT / "src").rglob("*") if path.is_file())
    return tuple(sorted(paths))


def _is_obvious_placeholder(value: str) -> bool:
    lowered = value.casefold()
    return (
        not value
        or len(value) < 16
        or any(part in lowered for part in PLACEHOLDER_PARTS)
        or len(set(value)) <= 2
    )


def _is_obvious_session_placeholder(value: str) -> bool:
    lowered = value.casefold()
    return (
        not value
        or any(part in lowered for part in PLACEHOLDER_PARTS)
        or len(set(value)) <= 2
        or (value.isdigit() and len(value) <= 7)
    )


def test_source_and_tracked_files_do_not_contain_real_tokens_or_session_cookies() -> None:
    """Break caught: a credential or reusable Facebook session is committed."""

    forbidden_paths = {
        (ROOT / ".env").resolve(),
        (ROOT / "runtime" / "session.json").resolve(),
    }
    findings: list[str] = []
    for path in _tracked_and_source_paths():
        if (
            path in forbidden_paths
            or "runtime" in path.relative_to(ROOT).parts
            or path.suffix.casefold() not in TEXT_SUFFIXES
            or not path.is_file()
        ):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(ROOT).as_posix()
        for match in SECRET_ASSIGNMENT.finditer(content):
            if not _is_obvious_placeholder(match.group(2)):
                findings.append(f"{relative}: non-placeholder {match.group(1)}")
        for pattern in (
            DIRECT_SESSION_COOKIE,
            SELENIUM_SESSION_COOKIE,
            COOKIE_HEADER_VALUE,
        ):
            for match in pattern.finditer(content):
                if not _is_obvious_session_placeholder(match.group(1)):
                    findings.append(f"{relative}: Facebook session cookie")

    assert findings == []


def _literal_string_set(path: Path, name: str) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset"
            and value.args
            and isinstance(value.args[0], ast.Set)
        ):
            break
        return frozenset(
            item.value
            for item in value.args[0].elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        )
    raise AssertionError(f"{name} must be a literal frozenset in {path.relative_to(ROOT)}")


def test_event_counter_producers_and_api_use_repository_allowlist() -> None:
    """Break caught: an event emits arbitrary data or the API exposes extra keys."""

    repository_allowlist = _literal_string_set(
        ROOT / "src" / "fb_data_pipeline" / "repositories" / "jobs.py",
        "_COUNTER_KEYS",
    )
    api_allowlist = _literal_string_set(
        ROOT / "src" / "fb_crawl" / "api" / "schemas.py",
        "EVENT_COUNTER_NAMES",
    )
    emitted: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            counters = next(
                (keyword.value for keyword in call.keywords if keyword.arg == "counters"),
                None,
            )
            if not isinstance(counters, ast.Dict):
                continue
            emitted.update(
                key.value
                for key in counters.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )

    assert api_allowlist == repository_allowlist
    assert emitted <= repository_allowlist


def test_base_cli_import_does_not_require_api_or_browser_extras() -> None:
    """Break caught: help/import starts an optional API or Selenium runtime."""

    script = r'''
import importlib.abc
import sys

BLOCKED = ("fastapi", "pydantic", "uvicorn", "selenium", "fb_crawl.adapters.browser")

class BlockOptionalRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in BLOCKED):
            raise AssertionError(f"optional runtime import attempted: {fullname}")
        return None

sys.meta_path.insert(0, BlockOptionalRuntime())
from fb_crawl.cli.app import build_parser
args = build_parser().parse_args(["api", "serve"])
assert (args.mode, args.api_command) == ("api", "serve")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_api_schema_import_does_not_require_selenium() -> None:
    """Break caught: the optional HTTP schema layer takes browser ownership."""

    if importlib.util.find_spec("pydantic") is None:
        pytest.skip("optional api extra is not installed; wheel metadata is tested separately")
    script = r'''
import importlib.abc
import sys

class BlockSelenium(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "selenium" or fullname.startswith("selenium."):
            raise AssertionError(f"Selenium import attempted: {fullname}")
        return None

sys.meta_path.insert(0, BlockSelenium())
import fb_crawl.api.schemas
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "database_url",
    (
        None,
        "postgresql://user:secret@db.example.test/fb_pipeline",
        "postgresql://user:secret@db.example.test/safe_test?dbname=fb_pipeline",
    ),
)
def test_user_query_integration_suite_skips_without_safe_test_database_before_connect(
    database_url: str | None,
) -> None:
    """Break caught: an offline test run connects to a hardcoded PostgreSQL DSN."""

    script = r'''
import os
import psycopg
import pytest

def forbidden_connect(*args, **kwargs):
    raise AssertionError("offline integration suite attempted a database connection")

psycopg.connect = forbidden_connect
raise SystemExit(pytest.main([
    "tests/integration/data_pipeline/test_user_query_repository.py",
    "-q",
    "-p",
    "no:cacheprovider",
]))
'''
    environment = os.environ.copy()
    environment.pop("TEST_DATABASE_URL", None)
    if database_url is not None:
        environment["TEST_DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipped" in result.stdout
