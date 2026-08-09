import pytest

from fb_crawl.core.exceptions import ConfigurationError
from fb_data_pipeline.config import load_pipeline_settings


def test_pipeline_settings_load_fbnumber_contract() -> None:
    settings = load_pipeline_settings(
        {
            "PIPELINE_DEFAULT_COUNTRY_CODE": "+84",
            "FB_NUMBER_API_URL": "https://api.example.test/phone",
            "FB_NUMBER_API_TOKEN": "secret",
            "FB_NUMBER_AUTH_HEADER": "X-Api-Key",
            "FB_NUMBER_AUTH_SCHEME": "",
            "FB_NUMBER_TIMEOUT_SECONDS": "9",
            "FB_NUMBER_MAX_RETRIES": "1",
        }
    )

    assert settings.default_country_code == "84"
    assert settings.fb_number_api_token == "secret"
    assert settings.fb_number_auth_header == "X-Api-Key"
    assert settings.fb_number_auth_scheme == ""
    assert settings.fb_number_timeout_seconds == 9
    assert settings.fb_number_max_retries == 1


def test_fbnumber_token_is_required_only_when_provider_is_started() -> None:
    settings = load_pipeline_settings({})

    with pytest.raises(ConfigurationError, match="FB_NUMBER_API_TOKEN"):
        settings.require_fb_number()


def test_pipeline_settings_load_database_contract() -> None:
    settings = load_pipeline_settings(
        {
            "DATABASE_URL": "postgresql://app:password@localhost/fb_pipeline",
            "DATABASE_STATEMENT_TIMEOUT_SECONDS": "7.5",
        }
    )

    assert settings.database_url == (
        "postgresql://app:password@localhost/fb_pipeline"
    )
    assert settings.database_statement_timeout_seconds == 7.5


def test_database_url_is_required_only_when_database_is_started() -> None:
    settings = load_pipeline_settings({})

    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        settings.require_database()


def test_pipeline_settings_reject_invalid_database_timeout() -> None:
    with pytest.raises(
        ConfigurationError,
        match="DATABASE_STATEMENT_TIMEOUT_SECONDS",
    ):
        load_pipeline_settings(
            {"DATABASE_STATEMENT_TIMEOUT_SECONDS": "0"}
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FB_NUMBER_TIMEOUT_SECONDS", "0"),
        ("FB_NUMBER_MAX_RETRIES", "-1"),
        ("PIPELINE_DEFAULT_COUNTRY_CODE", "none"),
    ],
)
def test_pipeline_settings_reject_invalid_numbers(name: str, value: str) -> None:
    with pytest.raises(ConfigurationError, match=name):
        load_pipeline_settings({name: value})
