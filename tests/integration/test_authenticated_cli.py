import pytest

from pathlib import Path

from fb_crawl.cli.app import main
from fb_crawl.cli.authenticated import (
    AuthenticatedRuntime,
)
from fb_crawl.core.exceptions import (
    ConfigurationError,
    ExportError,
    SessionError,
    ValidationError,
)
from fb_crawl.core.models import (
    EnrichmentStats,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)


class Browser:
    def __init__(self) -> None:
        self.quit_calls = 0

    def quit(self) -> None:
        self.quit_calls += 1


class Service:
    def __init__(
        self,
        failure: Exception | None = None,
    ) -> None:
        self.failure = failure

    def validate(self, request) -> None:
        return None

    def run(self, request, browser):
        if self.failure:
            raise self.failure

        return ScrapeResult(
            records=(
                UserRecord(
                    user_id="100",
                    name="Synthetic User",
                    profile_url=("https://www.facebook.com/" "profile.php?id=100"),
                    source="members",
                    source_url=request.targets[0],
                ),
            ),
            issues=(),
            stats=ScrapeStats(
                requested=1,
                discovered=1,
                succeeded=1,
                failed=0,
            ),
            enrichment=(
                EnrichmentStats(
                    selected=1,
                    attempted=1,
                    succeeded=1,
                    failed=0,
                    phone_found=1,
                    address_found=0,
                    current_city_found=1,
                    hometown_found=0,
                    birth_year_found=1,
                )
                if request.enrich_profiles
                else None
            ),
        )


def runtime(
    browser: Browser,
    service: Service,
) -> AuthenticatedRuntime:
    def write_result(
        result,
        path: Path,
        format_name: str,
    ) -> bool:
        path.write_text(
            result.records[0].user_id,
            encoding="utf-8",
        )
        return True

    return AuthenticatedRuntime(
        create_browser=lambda settings: browser,
        create_service=(lambda settings, credentials_provider: service),
        ensure_format=lambda format_name: None,
        write_result=write_result,
    )


def test_authenticated_command_writes_output_and_quits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    browser = Browser()

    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: runtime(
            browser,
            Service(),
        ),
    )

    output = tmp_path / "members.csv"

    exit_code = main(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--output",
            str(output),
            "--headless",
        ]
    )

    assert exit_code == 0
    assert output.read_text(encoding="utf-8") == "100"
    assert browser.quit_calls == 1


def test_session_failure_returns_three_and_still_quits(
    monkeypatch,
) -> None:
    browser = Browser()

    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: runtime(
            browser,
            Service(SessionError("Session unavailable.")),
        ),
    )

    exit_code = main(
        [
            "authenticated",
            "comments",
            "https://www.facebook.com/acme/posts/1",
            "--headless",
        ]
    )

    assert exit_code == 3
    assert browser.quit_calls == 1


def test_explicit_invalid_target_returns_two_before_browser_creation(
    monkeypatch,
) -> None:
    browser_creations: list[object] = []

    class InvalidService(Service):
        def validate(self, request) -> None:
            raise ValidationError("An unsupported members target was provided.")

    fake_runtime = runtime(
        Browser(),
        InvalidService(),
    )

    fake_runtime = AuthenticatedRuntime(
        create_browser=(lambda settings: browser_creations.append(settings)),
        create_service=fake_runtime.create_service,
        ensure_format=fake_runtime.ensure_format,
        write_result=fake_runtime.write_result,
    )

    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: fake_runtime,
    )

    exit_code = main(
        [
            "authenticated",
            "members",
            "https://facebook.com/acme",
        ]
    )

    assert exit_code == 2
    assert browser_creations == []


def test_export_failure_returns_four_and_still_quits(
    monkeypatch,
    tmp_path: Path,
) -> None:
    browser = Browser()
    broken = runtime(browser, Service())

    broken = AuthenticatedRuntime(
        create_browser=broken.create_browser,
        create_service=broken.create_service,
        ensure_format=broken.ensure_format,
        write_result=(
            lambda result, path, format_name: (_ for _ in ()).throw(
                ExportError("Cannot write output file.")
            )
        ),
    )

    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: broken,
    )

    exit_code = main(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--output",
            str(tmp_path / "users.csv"),
        ]
    )

    assert exit_code == 4
    assert browser.quit_calls == 1


def test_missing_browser_extra_is_sanitized(
    monkeypatch,
) -> None:
    import builtins
    import sys

    from fb_crawl.cli import authenticated

    real_import = builtins.__import__

    for name in tuple(sys.modules):
        if (
            name == "selenium"
            or name.startswith("selenium.")
            or name.startswith("fb_crawl.adapters.browser.")
        ):
            monkeypatch.delitem(
                sys.modules,
                name,
                raising=False,
            )

    def blocked_import(
        name,
        *args,
        **kwargs,
    ):
        if name == "selenium" or name.startswith("selenium."):
            error = ModuleNotFoundError("No module named selenium")
            error.name = "selenium"
            raise error

        return real_import(
            name,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        builtins,
        "__import__",
        blocked_import,
    )

    with pytest.raises(
        ConfigurationError,
        match="browser",
    ):
        authenticated._load_runtime()


def test_enrichment_summary_is_printed_and_browser_quits(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    browser = Browser()
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: runtime(browser, Service()),
    )

    exit_code = main(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--enrich-profiles",
            "--profile-fields",
            "phone,current_city,birth_date",
            "--output",
            str(tmp_path / "members.csv"),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "enrichment_selected=1" in output
    assert "phone_found=1" in output
    assert "current_city_found=1" in output
    assert "birth_year_found=1" in output
    assert browser.quit_calls == 1


@pytest.mark.parametrize(
    "extra",
    [
        ["--profile-fields", "phone"],
        ["--enrich-profiles", "--profile-fields", "unknown"],
        ["--enrich-profiles", "--profile-limit", "0"],
        ["--enrich-profiles", "--profile-delay", "-1"],
    ],
)
def test_invalid_enrichment_options_fail_before_runtime(
    monkeypatch,
    extra: list[str],
) -> None:
    runtime_loads: list[bool] = []
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: runtime_loads.append(True),
    )

    exit_code = main(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            *extra,
        ]
    )

    assert exit_code == 2
    assert runtime_loads == []
