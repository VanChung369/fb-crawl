import pytest

from pathlib import Path

from fb_crawl.cli.app import main
from fb_crawl.cli.authenticated import (
    AuthenticatedPersistenceRuntime,
    AuthenticatedRuntime,
    IdentityRepairRuntime,
    _load_persistence_runtime,
    _pipeline_user_result,
)
from fb_crawl.core.exceptions import (
    ConfigurationError,
    ExportError,
    SessionError,
    ValidationError,
)
from fb_crawl.core.models import (
    AuthenticatedAction,
    AuthenticatedBatchResult,
    EnrichmentStats,
    IdentityRepairResult,
    IdentityRepairStats,
    InspectRecord,
    MessageRecord,
    RetryStats,
    ScrapeResult,
    ScrapeStats,
    UserRecord,
)
from fb_data_pipeline.repositories.errors import DatabaseError
from fb_data_pipeline.services.ingestion import IngestionReport
from fb_data_pipeline.services.persistence import (
    PersistenceFailure,
    PersistenceReport,
)
from fb_data_pipeline.services.pipeline import PipelineReport


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


def ingestion_report(
    *,
    provider_found: int = 1,
    provider_not_found: int = 0,
    provider_failed: int = 0,
    provider_retries_required: int = 0,
    database_failure: bool = False,
) -> IngestionReport:
    failures = (
        (
            PersistenceFailure(
                aliases=("uid:100",),
                error_code="database_identity_conflict",
            ),
        )
        if database_failure
        else ()
    )
    persisted = 0 if database_failure else 1
    return IngestionReport(
        pipeline=PipelineReport(
            users=1,
            input_records=1,
            skipped_records=0,
            invalid_crawler_phones=0,
            phone_1_found=provider_found,
            phone_2_found=0,
            provider_found=provider_found,
            provider_not_found=provider_not_found,
            provider_failed=provider_failed,
        ),
        persistence=PersistenceReport(
            intended=1,
            persisted=persisted,
            provider_retries_required=provider_retries_required,
            user_ids=(41,) if persisted else (),
            failures=failures,
        ),
    )


def empty_ingestion_report() -> IngestionReport:
    return IngestionReport(
        pipeline=PipelineReport(
            users=0,
            input_records=0,
            skipped_records=0,
            invalid_crawler_phones=0,
            phone_1_found=0,
            phone_2_found=0,
            provider_found=0,
            provider_not_found=0,
            provider_failed=0,
        ),
        persistence=PersistenceReport(
            intended=0,
            persisted=0,
            provider_retries_required=0,
            user_ids=(),
        ),
    )


def test_pipeline_user_result_routes_regular_and_batch_results() -> None:
    regular = ScrapeResult(
        records=(
            UserRecord(
                user_id="100",
                name="Synthetic User",
                profile_url="https://www.facebook.com/synthetic.user",
                source="profile",
                source_url="https://www.facebook.com/synthetic.user",
            ),
        ),
        issues=(),
        stats=ScrapeStats(1, 1, 1, 0),
    )
    message_result = ScrapeResult(
        records=(
            MessageRecord(
                message_id="message-1",
                sender_name="Sender",
                sender_profile_url=None,
                text="Visible message",
                sent_at=None,
                thread_url="https://www.facebook.com/messages/t/1",
            ),
        ),
        issues=(),
        stats=ScrapeStats(1, 1, 1, 0),
    )
    inspect_result = ScrapeResult(
        records=(
            InspectRecord(
                target_url="https://www.facebook.com/synthetic.user",
                target_action="inspect",
                session_valid=True,
                document_ready=True,
                main_found=True,
                dialog_count=0,
                visible_profile_links=1,
                message_rows=0,
                profile_field_labels=0,
            ),
        ),
        issues=(),
        stats=ScrapeStats(1, 1, 1, 0),
    )
    batch = AuthenticatedBatchResult(
        user_result=regular,
        message_result=message_result,
        inspect_result=inspect_result,
        stats=ScrapeStats(3, 3, 3, 0),
        issues=(),
    )

    assert _pipeline_user_result(AuthenticatedAction.PROFILE, regular) is regular
    assert (
        _pipeline_user_result(AuthenticatedAction.ENGAGEMENT, regular)
        is regular
    )
    assert _pipeline_user_result(AuthenticatedAction.BATCH, batch) is regular


def test_persistence_runtime_composes_empty_in_memory_ingestion(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://pipeline:secret@localhost/pipeline",
    )
    monkeypatch.setenv("FB_NUMBER_API_TOKEN", "provider-secret")
    runtime = _load_persistence_runtime()
    result = ScrapeResult(
        records=(),
        issues=(),
        stats=ScrapeStats(
            requested=0,
            discovered=0,
            succeeded=0,
            failed=0,
        ),
    )

    try:
        report = runtime.ingest_result(result)
    finally:
        runtime.close()

    assert isinstance(runtime, AuthenticatedPersistenceRuntime)
    assert report.pipeline.users == 0
    assert report.persistence.persisted == 0


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
    assert "uid_numeric=1" in output
    assert "uid_unresolved=0" in output
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


def test_identity_repair_command_reads_writes_and_quits_browser(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    browser = Browser()
    source = tmp_path / "friends.csv"
    source.write_text(
        "user_id,name,username,profile_url\n"
        "123,174 friends,,https://www.facebook.com/profile.php?id=123\n",
        encoding="utf-8",
    )
    output = tmp_path / "friends-fixed.csv"
    calls = []
    writes = []
    fieldnames = (
        "user_id",
        "name",
        "username",
        "profile_url",
        "identity_status",
    )
    repaired_row = {
        "user_id": "123",
        "name": "Synthetic User",
        "username": "",
        "profile_url": "https://www.facebook.com/profile.php?id=123",
        "identity_status": "repaired",
    }

    class RepairService:
        def run(self, *args, **kwargs):
            calls.append((args, kwargs))
            result = IdentityRepairResult(
                fieldnames=fieldnames,
                rows=(repaired_row,),
                stats=IdentityRepairStats(
                    rows=1,
                    eligible=1,
                    attempted=1,
                    repaired=1,
                    verified=0,
                    failed=0,
                    skipped=0,
                    pending=0,
                ),
            )
            kwargs["progress_func"](result)
            return result

    def write_result(result, path: Path) -> bool:
        writes.append(path)
        path.write_text(result.rows[0]["name"], encoding="utf-8")
        return True

    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_repair_runtime",
        lambda: IdentityRepairRuntime(
            create_browser=lambda settings: browser,
            create_service=lambda settings, credentials: RepairService(),
            read_rows=lambda path: (fieldnames, (repaired_row,)),
            write_result=write_result,
        ),
    )

    exit_code = main(
        [
            "authenticated",
            "repair",
            str(source),
            "--output",
            str(output),
            "--limit",
            "1",
            "--delay",
            "0",
            "--headless",
        ]
    )

    summary = capsys.readouterr().out
    assert exit_code == 0
    assert output.read_text(encoding="utf-8") == "Synthetic User"
    assert calls[0][1]["limit"] == 1
    assert calls[0][1]["delay_seconds"] == 0
    assert calls[0][1]["max_retries"] == 2
    assert calls[0][1]["retry_backoff_seconds"] == 5
    assert calls[0][1]["retry_jitter_seconds"] == 1
    assert writes == [output, output]
    assert "repaired=1" in summary
    assert "retried=0" in summary
    assert browser.quit_calls == 1


@pytest.mark.parametrize(
    "extra",
    [
        ["--limit", "0"],
        ["--delay", "-1"],
        ["--max-retries", "-1"],
        ["--retry-backoff", "-1"],
        ["--retry-jitter", "-1"],
    ],
)
def test_invalid_identity_repair_reliability_options_fail_before_runtime(
    monkeypatch,
    extra: list[str],
) -> None:
    runtime_loads = []
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_repair_runtime",
        lambda: runtime_loads.append(True),
    )

    exit_code = main(
        [
            "authenticated",
            "repair",
            "runtime/output/friends.csv",
            *extra,
        ]
    )

    assert exit_code == 2
    assert runtime_loads == []


@pytest.mark.parametrize(
    ("stat_name", "expected_exit"),
    [("session_failed", 3), ("interrupted", 130)],
)
def test_identity_repair_uses_distinct_session_and_interrupt_exit_codes(
    tmp_path: Path,
    monkeypatch,
    stat_name: str,
    expected_exit: int,
) -> None:
    browser = Browser()
    fieldnames = ("user_id", "name", "username", "profile_url")
    row = {
        "user_id": "123",
        "name": "Synthetic User",
        "username": "",
        "profile_url": "https://www.facebook.com/profile.php?id=123",
    }
    values = {"session_failed": 0, "interrupted": 0}
    values[stat_name] = 1
    result = IdentityRepairResult(
        fieldnames=fieldnames,
        rows=(row,),
        stats=IdentityRepairStats(
            rows=1,
            eligible=1,
            attempted=1,
            repaired=0,
            verified=0,
            failed=0,
            skipped=0,
            pending=1,
            session_failed=values["session_failed"],
            interrupted=values["interrupted"],
        ),
    )

    class RepairService:
        def run(self, *args, **kwargs):
            return result

    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_repair_runtime",
        lambda: IdentityRepairRuntime(
            create_browser=lambda settings: browser,
            create_service=lambda settings, credentials: RepairService(),
            read_rows=lambda path: (fieldnames, (row,)),
            write_result=lambda result, path: True,
        ),
    )

    exit_code = main(
        [
            "authenticated",
            "repair",
            str(tmp_path / "friends.csv"),
            "--headless",
        ]
    )

    assert exit_code == expected_exit
    assert browser.quit_calls == 1


def test_authenticated_interruption_summary_returns_130_and_keeps_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    browser = Browser()

    class InterruptedService(Service):
        def run(self, request, browser):
            result = super().run(request, browser)
            return ScrapeResult(
                records=result.records,
                issues=result.issues,
                stats=result.stats,
                retry=RetryStats(
                    attempted_targets=2,
                    retried=1,
                    rate_limited=0,
                    pending=1,
                    interrupted=1,
                ),
            )

    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: runtime(browser, InterruptedService()),
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

    summary = capsys.readouterr().out
    assert exit_code == 130
    assert output.exists()
    assert "targets_attempted=2" in summary
    assert "retried=1" in summary
    assert "pending=1" in summary
    assert "interrupted=1" in summary
    assert browser.quit_calls == 1


@pytest.mark.parametrize(
    "extra",
    [
        ["--max-retries", "-1"],
        ["--retry-backoff", "-1"],
        ["--retry-jitter", "-1"],
    ],
)
def test_invalid_authenticated_retry_options_fail_before_runtime(
    monkeypatch,
    extra: list[str],
) -> None:
    runtime_loads = []
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


@pytest.mark.parametrize(
    "extra",
    [
        ["--keep-output"],
        [
            "--persist",
            "--output",
            "runtime/output/explicit.csv",
        ],
    ],
)
def test_invalid_persistence_output_flags_fail_before_runtime(
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


def test_persist_ingests_in_memory_without_export(
    monkeypatch,
    capsys,
) -> None:
    browser = Browser()
    service = Service()
    exported: list[object] = []
    ingested: list[object] = []
    closed: list[bool] = []
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: AuthenticatedRuntime(
            create_browser=lambda settings: browser,
            create_service=lambda settings, credentials: service,
            ensure_format=lambda format_name: None,
            write_result=lambda *args: exported.append(args) or True,
        ),
    )
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_persistence_runtime",
        lambda: AuthenticatedPersistenceRuntime(
            ingest_result=(
                lambda result: ingested.append(result) or ingestion_report()
            ),
            close=lambda: closed.append(True),
        ),
    )

    exit_code = main(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--persist",
            "--headless",
        ]
    )

    summary = capsys.readouterr().out
    assert exit_code == 0
    assert len(ingested) == 1
    assert ingested[0].records[0].user_id == "100"
    assert exported == []
    assert closed == [True]
    assert browser.quit_calls == 1
    assert "output=not_requested" in summary
    assert "pipeline_users=1" in summary
    assert "persisted=1" in summary
    assert "db_failed=0" in summary
    assert "provider_found=1" in summary
    assert "provider_not_found=0" in summary
    assert "provider_failed=0" in summary
    assert "provider_retries_required=0" in summary


def test_keep_output_exports_before_ingestion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    browser = Browser()
    service = Service()
    events: list[str] = []
    output = tmp_path / "members.csv"

    def write_result(result, path: Path, format_name: str) -> bool:
        events.append("export")
        path.write_text("saved", encoding="utf-8")
        return True

    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: AuthenticatedRuntime(
            create_browser=lambda settings: browser,
            create_service=lambda settings, credentials: service,
            ensure_format=lambda format_name: None,
            write_result=write_result,
        ),
    )
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_persistence_runtime",
        lambda: AuthenticatedPersistenceRuntime(
            ingest_result=(
                lambda result: events.append("ingest") or ingestion_report()
            ),
            close=lambda: events.append("close"),
        ),
    )

    exit_code = main(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--persist",
            "--keep-output",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert output.read_text(encoding="utf-8") == "saved"
    assert events == ["export", "ingest", "close"]


@pytest.mark.parametrize(
    ("report", "expected_exit"),
    [
        (
            ingestion_report(
                provider_found=0,
                provider_not_found=1,
            ),
            0,
        ),
        (
            ingestion_report(
                provider_found=0,
                provider_failed=1,
                provider_retries_required=1,
            ),
            1,
        ),
        (ingestion_report(database_failure=True), 5),
    ],
)
def test_persistence_report_controls_exit_code(
    monkeypatch,
    report: IngestionReport,
    expected_exit: int,
) -> None:
    browser = Browser()
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: runtime(browser, Service()),
    )
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_persistence_runtime",
        lambda: AuthenticatedPersistenceRuntime(
            ingest_result=lambda result: report,
            close=lambda: None,
        ),
    )

    exit_code = main(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--persist",
        ]
    )

    assert exit_code == expected_exit


def test_interruption_takes_precedence_over_pipeline_failures(
    monkeypatch,
) -> None:
    browser = Browser()

    class InterruptedService(Service):
        def run(self, request, browser):
            result = super().run(request, browser)
            return ScrapeResult(
                records=result.records,
                issues=result.issues,
                stats=result.stats,
                retry=RetryStats(
                    attempted_targets=1,
                    retried=0,
                    rate_limited=0,
                    pending=1,
                    interrupted=1,
                ),
            )

    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: runtime(browser, InterruptedService()),
    )
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_persistence_runtime",
        lambda: AuthenticatedPersistenceRuntime(
            ingest_result=lambda result: ingestion_report(
                provider_found=0,
                provider_failed=1,
                provider_retries_required=1,
                database_failure=True,
            ),
            close=lambda: None,
        ),
    )

    exit_code = main(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--persist",
        ]
    )

    assert exit_code == 130


def test_database_failure_closes_browser_and_pipeline_runtime(
    monkeypatch,
) -> None:
    browser = Browser()
    closed: list[bool] = []
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: runtime(browser, Service()),
    )
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_persistence_runtime",
        lambda: AuthenticatedPersistenceRuntime(
            ingest_result=lambda result: (_ for _ in ()).throw(
                DatabaseError("Database operation failed.")
            ),
            close=lambda: closed.append(True),
        ),
    )

    exit_code = main(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--persist",
        ]
    )

    assert exit_code == 5
    assert closed == [True]
    assert browser.quit_calls == 1


def test_missing_pipeline_configuration_returns_five_before_browser(
    monkeypatch,
    capsys,
) -> None:
    browser_creations: list[object] = []
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("FB_NUMBER_API_TOKEN", raising=False)
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: AuthenticatedRuntime(
            create_browser=lambda settings: browser_creations.append(settings),
            create_service=lambda settings, credentials: Service(),
            ensure_format=lambda format_name: None,
            write_result=lambda *args: True,
        ),
    )

    exit_code = main(
        [
            "authenticated",
            "members",
            "https://www.facebook.com/groups/1",
            "--persist",
        ]
    )

    error = capsys.readouterr().err
    assert exit_code == 5
    assert browser_creations == []
    assert "Persistence pipeline configuration is incomplete." in error
    assert "DATABASE_URL" not in error


def test_batch_persistence_ingests_only_user_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    user_result = ScrapeResult(
        records=(
            UserRecord(
                user_id="100",
                name="Synthetic User",
                profile_url="https://www.facebook.com/synthetic.user",
                source="profile",
                source_url="https://www.facebook.com/synthetic.user",
            ),
        ),
        issues=(),
        stats=ScrapeStats(1, 1, 1, 0),
    )
    empty = ScrapeResult(
        records=(),
        issues=(),
        stats=ScrapeStats(0, 0, 0, 0),
    )
    batch_result = AuthenticatedBatchResult(
        user_result=user_result,
        message_result=empty,
        inspect_result=empty,
        stats=ScrapeStats(1, 1, 1, 0),
        issues=(),
    )

    class BatchService(Service):
        def run(self, request, browser):
            return batch_result

    targets = tmp_path / "targets.txt"
    targets.write_text(
        "profile:https://www.facebook.com/synthetic.user\n",
        encoding="utf-8",
    )
    browser = Browser()
    exported: list[object] = []
    ingested: list[object] = []
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: AuthenticatedRuntime(
            create_browser=lambda settings: browser,
            create_service=lambda settings, credentials: BatchService(),
            ensure_format=lambda format_name: None,
            write_result=lambda *args: exported.append(args) or True,
        ),
    )
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_persistence_runtime",
        lambda: AuthenticatedPersistenceRuntime(
            ingest_result=(
                lambda result: ingested.append(result) or ingestion_report()
            ),
            close=lambda: None,
        ),
    )

    exit_code = main(
        [
            "authenticated",
            "batch",
            "--input",
            str(targets),
            "--persist",
        ]
    )

    assert exit_code == 0
    assert ingested == [user_result]
    assert exported == []


@pytest.mark.parametrize(
    ("action", "target"),
    [
        ("profile", "https://www.facebook.com/synthetic.user"),
        ("engagement", "https://www.facebook.com/acme/posts/1"),
    ],
)
def test_profile_and_engagement_persist_exact_scrape_result(
    monkeypatch,
    action: str,
    target: str,
) -> None:
    browser = Browser()
    service = Service()
    ingested: list[object] = []
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: runtime(browser, service),
    )
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_persistence_runtime",
        lambda: AuthenticatedPersistenceRuntime(
            ingest_result=(
                lambda result: ingested.append(result) or ingestion_report()
            ),
            close=lambda: None,
        ),
    )

    exit_code = main(
        ["authenticated", action, target, "--persist"]
    )

    assert exit_code == 0
    assert len(ingested) == 1
    assert isinstance(ingested[0], ScrapeResult)
    assert ingested[0].records[0].user_id == "100"


def test_non_user_batch_persists_zero_users_without_export(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    empty_users = ScrapeResult(
        records=(),
        issues=(),
        stats=ScrapeStats(0, 0, 0, 0),
    )
    message_result = ScrapeResult(
        records=(
            MessageRecord(
                message_id="message-1",
                sender_name="Sender",
                sender_profile_url=None,
                text="Visible message",
                sent_at=None,
                thread_url="https://www.facebook.com/messages/t/1",
            ),
        ),
        issues=(),
        stats=ScrapeStats(1, 1, 1, 0),
    )
    empty_inspect = ScrapeResult(
        records=(),
        issues=(),
        stats=ScrapeStats(0, 0, 0, 0),
    )
    batch_result = AuthenticatedBatchResult(
        user_result=empty_users,
        message_result=message_result,
        inspect_result=empty_inspect,
        stats=ScrapeStats(1, 1, 1, 0),
        issues=(),
    )

    class BatchService(Service):
        def run(self, request, browser):
            return batch_result

    targets = tmp_path / "targets.txt"
    targets.write_text(
        "messages:https://www.facebook.com/messages/t/1\n",
        encoding="utf-8",
    )
    browser = Browser()
    exported: list[object] = []
    ingested: list[object] = []
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: AuthenticatedRuntime(
            create_browser=lambda settings: browser,
            create_service=lambda settings, credentials: BatchService(),
            ensure_format=lambda format_name: None,
            write_result=lambda *args: exported.append(args) or True,
        ),
    )
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_persistence_runtime",
        lambda: AuthenticatedPersistenceRuntime(
            ingest_result=(
                lambda result: ingested.append(result)
                or empty_ingestion_report()
            ),
            close=lambda: None,
        ),
    )

    exit_code = main(
        [
            "authenticated",
            "batch",
            "--input",
            str(targets),
            "--persist",
        ]
    )

    summary = capsys.readouterr().out
    assert exit_code == 0
    assert ingested == [empty_users]
    assert exported == []
    assert "pipeline_users=0" in summary
    assert "persisted=0" in summary


def test_batch_keep_output_exports_before_user_ingestion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    user_result = ScrapeResult(
        records=(
            UserRecord(
                user_id="100",
                name="Synthetic User",
                profile_url="https://www.facebook.com/synthetic.user",
                source="profile",
                source_url="https://www.facebook.com/synthetic.user",
            ),
        ),
        issues=(),
        stats=ScrapeStats(1, 1, 1, 0),
    )
    empty = ScrapeResult(
        records=(),
        issues=(),
        stats=ScrapeStats(0, 0, 0, 0),
    )
    batch_result = AuthenticatedBatchResult(
        user_result=user_result,
        message_result=empty,
        inspect_result=empty,
        stats=ScrapeStats(1, 1, 1, 0),
        issues=(),
    )

    class BatchService(Service):
        def run(self, request, browser):
            return batch_result

    targets = tmp_path / "targets.txt"
    targets.write_text(
        "profile:https://www.facebook.com/synthetic.user\n",
        encoding="utf-8",
    )
    output = tmp_path / "batch.csv"
    events: list[str] = []

    def write_result(result, path: Path, format_name: str) -> bool:
        events.append("export")
        path.write_text("saved", encoding="utf-8")
        return True

    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_runtime",
        lambda: AuthenticatedRuntime(
            create_browser=lambda settings: Browser(),
            create_service=lambda settings, credentials: BatchService(),
            ensure_format=lambda format_name: None,
            write_result=write_result,
        ),
    )
    monkeypatch.setattr(
        "fb_crawl.cli.authenticated._load_persistence_runtime",
        lambda: AuthenticatedPersistenceRuntime(
            ingest_result=(
                lambda result: events.append("ingest") or ingestion_report()
            ),
            close=lambda: events.append("close"),
        ),
    )

    exit_code = main(
        [
            "authenticated",
            "batch",
            "--input",
            str(targets),
            "--persist",
            "--keep-output",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert output.read_text(encoding="utf-8") == "saved"
    assert events == ["export", "ingest", "close"]
