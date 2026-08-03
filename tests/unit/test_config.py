from pathlib import Path

import pytest

from fb_crawl.config import load_settings
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
