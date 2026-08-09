from pathlib import Path

import pytest

from fb_crawl.config import (
    load_browser_settings,
    load_settings,
    validate_checkpoint_path,
    validate_session_path,
)
from fb_crawl.core.exceptions import ConfigurationError


def test_cli_override_wins_over_environment_and_default() -> None:
    settings = load_settings(
        {
            "FB_CRAWL_TIMEOUT_SECONDS": "30",
            "FB_CRAWL_MAX_RETRIES": "4",
            "FB_CRAWL_OUTPUT_DIR": "env-output",
        },
        timeout_seconds=5,
        output_dir=Path("cli-output"),
    )

    assert settings.timeout_seconds == 5
    assert settings.max_retries == 4
    assert settings.output_dir == Path("cli-output")


def test_invalid_environment_value_is_a_safe_configuration_error() -> None:
    with pytest.raises(
        ConfigurationError,
        match="FB_CRAWL_MAX_RETRIES",
    ):
        load_settings({"FB_CRAWL_MAX_RETRIES": "many"})


def test_browser_settings_use_cli_then_environment_then_defaults(
    tmp_path: Path,
) -> None:
    settings = load_browser_settings(
        {
            "FB_CRAWL_HEADLESS": "true",
            "FB_CRAWL_PROXY": "socks5://127.0.0.1:9050",
            "FB_CRAWL_SESSION_PATH": "runtime/from-env.json",
            "FB_CRAWL_BROWSER_TIMEOUT_SECONDS": "41",
            "FB_CRAWL_VERIFICATION_TIMEOUT_SECONDS": "401",
        },
        headless=False,
        session_path=Path("runtime/from-cli.json"),
        repository_root=tmp_path,
    )

    assert settings.headless is False
    assert settings.proxy == "socks5://127.0.0.1:9050"
    assert settings.session_path == (tmp_path / "runtime/from-cli.json").resolve()
    assert settings.browser_timeout_seconds == 41.0
    assert settings.verification_timeout_seconds == 401.0


@pytest.mark.parametrize("value", ["sometimes", "2", ""])
def test_browser_settings_reject_malformed_boolean(
    value: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigurationError, match="FB_CRAWL_HEADLESS"):
        load_browser_settings(
            {"FB_CRAWL_HEADLESS": value},
            repository_root=tmp_path,
        )


def test_repo_local_session_must_stay_under_runtime(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigurationError, match="runtime"):
        validate_session_path(
            tmp_path / "session.json",
            repository_root=tmp_path,
        )

    external = tmp_path.parent / "mounted-secret/session.json"

    assert (
        validate_session_path(
            external,
            repository_root=tmp_path,
        )
        == external.resolve()
    )


def test_repo_local_checkpoint_must_stay_under_runtime(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="checkpoint path"):
        validate_checkpoint_path(
            tmp_path / "checkpoint.json",
            repository_root=tmp_path,
        )

    expected = (tmp_path / "runtime/checkpoints/members.json").resolve()
    assert validate_checkpoint_path(
        Path("runtime/checkpoints/members.json"),
        repository_root=tmp_path,
    ) == expected
