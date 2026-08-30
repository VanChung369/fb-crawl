from __future__ import annotations

import argparse
import builtins
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from fb_crawl.api.config import ApiSettings
from fb_crawl.auth.config import AuthSettings
from fb_crawl.cli import app
from fb_crawl.core.exceptions import ConfigurationError
from fb_data_pipeline.config import PipelineSettings
from zoneinfo import ZoneInfo


API_KEY = "a" * 32


def _valid_auth_settings() -> AuthSettings:
    return AuthSettings(
        jwt_secret="j" * 32,
        token_hmac_secret="h" * 32,
        public_base_url="https://leads.example.com",
        product_timezone=ZoneInfo("Asia/Ho_Chi_Minh"),
    )


def _serve_args(*, host: str = "127.0.0.1", port: int = 8000) -> argparse.Namespace:
    return argparse.Namespace(api_command="serve", host=host, port=port)


def test_execute_api_validates_configuration_then_runs_built_app(monkeypatch) -> None:
    """Break caught: Uvicorn gets an import string or starts before validated composition."""
    from fb_crawl.cli import api as api_cli

    events: list[object] = []
    pipeline = PipelineSettings(database_url="postgresql://configured")
    settings = ApiSettings(api_key=API_KEY)
    auth_settings = AuthSettings(
        jwt_secret="j" * 32,
        token_hmac_secret="h" * 32,
        public_base_url="https://leads.example.com",
        product_timezone=ZoneInfo("Asia/Ho_Chi_Minh"),
    )
    built_app = object()

    monkeypatch.setattr(
        api_cli,
        "load_pipeline_settings",
        lambda: events.append("pipeline_settings") or pipeline,
    )
    monkeypatch.setattr(
        api_cli,
        "load_api_settings",
        lambda values: events.append(("api_settings", values is os.environ)) or settings,
    )
    monkeypatch.setattr(
        api_cli,
        "load_auth_settings",
        lambda values: events.append(("auth_settings", values is os.environ))
        or auth_settings,
    )

    def compose(pipeline_settings, api_settings, product_auth_settings):
        events.append(
            ("compose", pipeline_settings, api_settings, product_auth_settings)
        )
        return built_app

    monkeypatch.setattr(api_cli, "_compose_api", compose)

    def run(application, **options):
        events.append(("uvicorn", application, options))

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=run))

    assert api_cli.execute_api(_serve_args(host="127.0.0.9", port=8123)) == 0
    assert events == [
        "pipeline_settings",
        ("api_settings", True),
        ("auth_settings", True),
        ("compose", pipeline, settings, auth_settings),
        (
            "uvicorn",
            built_app,
            {"host": "127.0.0.9", "port": 8123, "reload": False},
        ),
    ]


def test_missing_database_returns_safe_exit_five_before_framework_start(
    monkeypatch,
    capsys,
) -> None:
    """Break caught: missing PostgreSQL config is exit 2 or reaches the server."""
    from fb_crawl.cli import api as api_cli

    monkeypatch.setattr(
        api_cli,
        "load_pipeline_settings",
        lambda: PipelineSettings(database_url=""),
    )
    monkeypatch.setattr(
        api_cli,
        "load_api_settings",
        lambda _values: (_ for _ in ()).throw(
            AssertionError("API settings loaded before database validation")
        ),
    )
    monkeypatch.setattr(
        api_cli,
        "_compose_api",
        lambda *_args: (_ for _ in ()).throw(AssertionError("API composed")),
    )

    assert app.main(["api", "serve"]) == 5
    error = capsys.readouterr().err
    assert error.strip() == "DATABASE_URL is required."
    assert "postgresql://" not in error


def test_missing_api_key_returns_safe_exit_two_before_framework_start(
    monkeypatch,
    capsys,
) -> None:
    """Break caught: an unprotected API server starts without its shared key."""
    from fb_crawl.cli import api as api_cli

    monkeypatch.setattr(
        api_cli,
        "load_pipeline_settings",
        lambda: PipelineSettings(database_url="postgresql://configured"),
    )
    monkeypatch.setattr(
        api_cli,
        "load_api_settings",
        lambda _values: (_ for _ in ()).throw(
            ConfigurationError(
                "FB_CRAWL_API_KEY must contain at least 32 nonblank characters."
            )
        ),
    )
    monkeypatch.setattr(
        api_cli,
        "_compose_api",
        lambda *_args: (_ for _ in ()).throw(AssertionError("API composed")),
    )

    assert app.main(["api", "serve"]) == 2
    assert capsys.readouterr().err.strip() == (
        "FB_CRAWL_API_KEY must contain at least 32 nonblank characters."
    )


@pytest.mark.parametrize("stage", ["compose", "serve"])
def test_ctrl_c_during_api_startup_or_server_returns_130(
    monkeypatch,
    stage: str,
) -> None:
    """Break caught: Ctrl+C escapes the API process boundary with a traceback."""
    from fb_crawl.cli import api as api_cli

    monkeypatch.setattr(
        api_cli,
        "load_pipeline_settings",
        lambda: PipelineSettings(database_url="postgresql://configured"),
    )
    monkeypatch.setattr(
        api_cli,
        "load_api_settings",
        lambda _values: ApiSettings(api_key=API_KEY),
    )
    monkeypatch.setattr(
        api_cli, "load_auth_settings", lambda _values: _valid_auth_settings()
    )
    if stage == "compose":
        monkeypatch.setattr(
            api_cli,
            "_compose_api",
            lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
    else:
        monkeypatch.setattr(api_cli, "_compose_api", lambda *_args: object())
        monkeypatch.setitem(
            sys.modules,
            "uvicorn",
            SimpleNamespace(
                run=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    KeyboardInterrupt()
                )
            ),
        )

    assert api_cli.execute_api(_serve_args()) == 130


def test_invalid_api_command_stops_before_configuration(monkeypatch) -> None:
    """Break caught: unsupported API commands initialize database resources."""
    from fb_crawl.cli import api as api_cli

    monkeypatch.setattr(
        api_cli,
        "load_pipeline_settings",
        lambda: (_ for _ in ()).throw(AssertionError("configuration loaded")),
    )

    with pytest.raises(ValueError, match="Unsupported API command"):
        api_cli.execute_api(argparse.Namespace(api_command="other"))


@pytest.mark.parametrize("missing_root", ["fastapi", "pydantic"])
def test_missing_api_framework_root_returns_safe_exit_two(
    monkeypatch,
    capsys,
    missing_root: str,
) -> None:
    """Break caught: a missing API extra root escapes with a traceback."""
    from fb_crawl.cli import api as api_cli

    monkeypatch.setattr(
        api_cli,
        "load_pipeline_settings",
        lambda: PipelineSettings(database_url="postgresql://configured"),
    )
    monkeypatch.setattr(
        api_cli,
        "load_api_settings",
        lambda _values: ApiSettings(api_key=API_KEY),
    )
    monkeypatch.setattr(
        api_cli, "load_auth_settings", lambda _values: _valid_auth_settings()
    )
    missing = ModuleNotFoundError(f"No module named '{missing_root}'")
    missing.name = missing_root
    monkeypatch.setattr(
        api_cli,
        "_compose_api",
        lambda *_args: (_ for _ in ()).throw(missing),
    )

    assert app.main(["api", "serve"]) == 2
    error = capsys.readouterr().err
    assert error.strip() == (
        'API mode requires: python -m pip install -e ".[api]"'
    )
    assert "postgresql://" not in error


def test_missing_uvicorn_root_returns_safe_exit_two(
    monkeypatch,
    capsys,
) -> None:
    """Break caught: a missing server extra escapes after app composition."""
    from fb_crawl.cli import api as api_cli

    monkeypatch.setattr(
        api_cli,
        "load_pipeline_settings",
        lambda: PipelineSettings(database_url="postgresql://configured"),
    )
    monkeypatch.setattr(
        api_cli,
        "load_api_settings",
        lambda _values: ApiSettings(api_key=API_KEY),
    )
    monkeypatch.setattr(
        api_cli, "load_auth_settings", lambda _values: _valid_auth_settings()
    )
    monkeypatch.setattr(api_cli, "_compose_api", lambda *_args: object())
    original_import = builtins.__import__

    def import_without_uvicorn(name, *args, **kwargs):
        if name == "uvicorn":
            missing = ModuleNotFoundError("No module named 'uvicorn'")
            missing.name = "uvicorn"
            raise missing
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_uvicorn)

    assert app.main(["api", "serve"]) == 2
    error = capsys.readouterr().err
    assert error.strip() == (
        'API mode requires: python -m pip install -e ".[api]"'
    )
    assert "postgresql://" not in error


@pytest.mark.parametrize("stage", ["compose", "uvicorn"])
def test_internal_missing_module_is_not_mislabeled_as_missing_api_extra(
    monkeypatch,
    stage: str,
) -> None:
    """Break caught: a broken internal/transitive import is hidden as setup advice."""
    from fb_crawl.cli import api as api_cli

    monkeypatch.setattr(
        api_cli,
        "load_pipeline_settings",
        lambda: PipelineSettings(database_url="postgresql://configured"),
    )
    monkeypatch.setattr(
        api_cli,
        "load_api_settings",
        lambda _values: ApiSettings(api_key=API_KEY),
    )
    monkeypatch.setattr(
        api_cli, "load_auth_settings", lambda _values: _valid_auth_settings()
    )
    missing = ModuleNotFoundError("No module named 'internal_runtime_dependency'")
    missing.name = "internal_runtime_dependency"
    if stage == "compose":
        monkeypatch.setattr(
            api_cli,
            "_compose_api",
            lambda *_args: (_ for _ in ()).throw(missing),
        )
    else:
        monkeypatch.setattr(api_cli, "_compose_api", lambda *_args: object())
        original_import = builtins.__import__

        def import_broken_uvicorn(name, *args, **kwargs):
            if name == "uvicorn":
                raise missing
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", import_broken_uvicorn)

    with pytest.raises(ModuleNotFoundError) as captured:
        app.main(["api", "serve"])

    assert captured.value is missing


def test_missing_database_path_imports_no_optional_framework_or_browser() -> None:
    """Break caught: invalid startup configuration imports optional process owners."""
    script = r'''
import importlib.abc
import os

for name in ("DATABASE_URL", "FB_CRAWL_API_KEY"):
    os.environ.pop(name, None)

BLOCKED = ("fastapi", "pydantic", "uvicorn", "selenium", "fb_crawl.adapters.browser")

class BlockOptionalRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in BLOCKED):
            raise AssertionError(f"optional runtime import attempted: {fullname}")
        return None

import sys
sys.meta_path.insert(0, BlockOptionalRuntime())
from fb_crawl.cli.app import main
raise SystemExit(main(["api", "serve"]))
'''
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)
    environment.pop("FB_CRAWL_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 5
    assert result.stderr.strip() == "DATABASE_URL is required."


try:
    import fastapi  # noqa: F401
    import pydantic  # noqa: F401
except ModuleNotFoundError as error:
    if error.name not in {"fastapi", "pydantic"}:
        raise
    FASTAPI_AVAILABLE = False
else:
    FASTAPI_AVAILABLE = True


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_real_api_composition_uses_postgres_repositories_without_browser_imports(
    monkeypatch,
) -> None:
    """Break caught: production API composition owns a browser or fake repository."""
    from fb_crawl.cli.api import _compose_api
    from fb_crawl.services.jobs import JobService
    from fb_crawl.accounts.postgres import PostgresAccountRepository
    from fb_data_pipeline.repositories.jobs import JobRepository
    from fb_data_pipeline.repositories.migrations import MigrationRunner
    from fb_data_pipeline.repositories.users import UserQueryRepository

    monkeypatch.setattr(MigrationRunner, "apply", lambda _runner: ())
    built = _compose_api(
        PipelineSettings(database_url="postgresql://not-connected"),
        ApiSettings(api_key=API_KEY),
        AuthSettings(
            jwt_secret="j" * 32,
            token_hmac_secret="h" * 32,
            public_base_url="https://leads.example.com",
            product_timezone=ZoneInfo("Asia/Ho_Chi_Minh"),
        ),
    )

    assert isinstance(built.state.job_service, JobService)
    assert isinstance(built.state.job_repository, JobRepository)
    assert isinstance(built.state.user_repository, UserQueryRepository)
    assert isinstance(
        built.state.product_services.account_repository,
        PostgresAccountRepository,
    )
    code = (
        "import sys\n"
        "from fb_crawl.cli.api import _compose_api\n"
        "from fb_crawl.api.config import ApiSettings\n"
        "from fb_crawl.auth.config import AuthSettings\n"
        "from fb_data_pipeline.config import PipelineSettings\n"
        "from zoneinfo import ZoneInfo\n"
        "from fb_data_pipeline.repositories.migrations import MigrationRunner\n"
        "from unittest.mock import patch\n"
        "with patch.object(MigrationRunner, 'apply', return_value=()):\n"
        "    _compose_api(PipelineSettings(database_url='postgresql://not-connected'), ApiSettings(api_key='x'*32), AuthSettings(jwt_secret='j'*32, token_hmac_secret='h'*32, public_base_url='https://leads.example.com', product_timezone=ZoneInfo('Asia/Ho_Chi_Minh')))\n"
        "assert 'selenium' not in sys.modules\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"Selenium imported in fresh API process: {result.stderr}"
