from __future__ import annotations

from datetime import timedelta

import pytest

from fb_crawl.cli.app import main
from fb_data_pipeline.repositories.errors import DatabaseError
from fb_data_pipeline.services.retry import RetryReport


class Settings:
    database_url = "postgresql://user:secret@localhost/database"
    database_statement_timeout_seconds = 7.5

    def __init__(self) -> None:
        self.require_calls = 0
        self.fb_number_require_calls = 0

    def require_database(self) -> None:
        self.require_calls += 1

    def require_fb_number(self) -> None:
        self.fb_number_require_calls += 1


@pytest.mark.parametrize(
    ("applied", "expected"),
    [
        (("001_initial",), "applied=001_initial\n"),
        ((), "applied=none\n"),
    ],
)
def test_pipeline_migrate_delegates_and_reports_applied_names(
    applied: tuple[str, ...],
    expected: str,
    monkeypatch,
    capsys,
) -> None:
    settings = Settings()
    runner_calls: list[str] = []

    class Runner:
        def __init__(self, database_url: str) -> None:
            runner_calls.append(database_url)

        def apply(self) -> tuple[str, ...]:
            return applied

    monkeypatch.setattr(
        "fb_crawl.cli.pipeline.load_pipeline_settings",
        lambda: settings,
    )
    monkeypatch.setattr("fb_crawl.cli.pipeline.MigrationRunner", Runner)

    assert main(["pipeline", "migrate"]) == 0
    assert settings.require_calls == 1
    assert runner_calls == [settings.database_url]
    assert capsys.readouterr().out == expected


def test_pipeline_migrate_hides_connection_details(
    monkeypatch,
    capsys,
) -> None:
    settings = Settings()

    class FailingRunner:
        def __init__(self, _database_url: str) -> None:
            pass

        def apply(self) -> tuple[str, ...]:
            raise OSError(
                "could not connect with "
                "postgresql://user:secret@localhost/database"
            )

    monkeypatch.setattr(
        "fb_crawl.cli.pipeline.load_pipeline_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "fb_crawl.cli.pipeline.MigrationRunner",
        FailingRunner,
    )

    assert main(["pipeline", "migrate"]) == 5
    captured = capsys.readouterr()
    assert captured.err == "Database operation failed.\n"
    assert "secret" not in captured.err
    assert "postgresql://" not in captured.err


def test_pipeline_retry_dry_run_does_not_construct_provider(
    monkeypatch,
    capsys,
) -> None:
    settings = Settings()
    repository_calls: list[tuple[str, float]] = []
    service_calls: list[tuple[object, object, object]] = []
    run_calls: list[dict[str, object]] = []

    class Repository:
        def __init__(
            self,
            database_url: str,
            *,
            statement_timeout_seconds: float,
        ) -> None:
            repository_calls.append(
                (database_url, statement_timeout_seconds)
            )

    class Service:
        def __init__(
            self,
            source,
            enrichment=None,
            persistence=None,
        ) -> None:
            service_calls.append((source, enrichment, persistence))

        def run(self, **kwargs) -> RetryReport:
            run_calls.append(kwargs)
            return RetryReport(selected=2, dry_run=True)

    class UnexpectedProvider:
        @classmethod
        def from_settings(cls, _settings):
            raise AssertionError("provider must not be created for dry-run")

    monkeypatch.setattr(
        "fb_crawl.cli.pipeline.load_pipeline_settings",
        lambda: settings,
    )
    monkeypatch.setattr("fb_crawl.cli.pipeline.PostgresRepository", Repository)
    monkeypatch.setattr("fb_crawl.cli.pipeline.FBNumberRetryService", Service)
    monkeypatch.setattr(
        "fb_crawl.cli.pipeline.FBNumberProvider",
        UnexpectedProvider,
    )

    assert main(["pipeline", "retry", "--dry-run"]) == 0
    assert settings.require_calls == 1
    assert settings.fb_number_require_calls == 0
    assert repository_calls == [(settings.database_url, 7.5)]
    assert service_calls[0][1:] == (None, None)
    assert run_calls == [
        {
            "limit": 20,
            "cooldown": timedelta(hours=24),
            "force": False,
            "dry_run": True,
        }
    ]
    assert capsys.readouterr().out == (
        "selected=2 persisted=0 found=0 not_found=0 failed=0 "
        "rate_limited=0 retry_pending=0 database_failures=0 "
        "dry_run=true worker_busy=false\n"
    )


def test_pipeline_retry_normal_run_composes_services_and_closes_provider(
    monkeypatch,
    capsys,
) -> None:
    settings = Settings()
    provider_instances: list[object] = []
    run_calls: list[dict[str, object]] = []

    class Repository:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    class Provider:
        def __init__(self) -> None:
            self.close_calls = 0

        @classmethod
        def from_settings(cls, received_settings):
            assert received_settings is settings
            instance = cls()
            provider_instances.append(instance)
            return instance

        def close(self) -> None:
            self.close_calls += 1

    class Enrichment:
        def __init__(self, provider) -> None:
            assert provider is provider_instances[0]

    class Persistence:
        def __init__(self, repository) -> None:
            assert isinstance(repository, Repository)

    class Service:
        def __init__(self, source, enrichment, persistence) -> None:
            assert isinstance(source, Repository)
            assert isinstance(enrichment, Enrichment)
            assert isinstance(persistence, Persistence)

        def run(self, **kwargs) -> RetryReport:
            run_calls.append(kwargs)
            return RetryReport(
                selected=3,
                persisted=3,
                found=1,
                not_found=1,
                failed=1,
            )

    monkeypatch.setattr(
        "fb_crawl.cli.pipeline.load_pipeline_settings",
        lambda: settings,
    )
    monkeypatch.setattr("fb_crawl.cli.pipeline.PostgresRepository", Repository)
    monkeypatch.setattr("fb_crawl.cli.pipeline.FBNumberProvider", Provider)
    monkeypatch.setattr("fb_crawl.cli.pipeline.EnrichmentPipeline", Enrichment)
    monkeypatch.setattr(
        "fb_crawl.cli.pipeline.PipelinePersistenceService",
        Persistence,
    )
    monkeypatch.setattr("fb_crawl.cli.pipeline.FBNumberRetryService", Service)

    assert main(
        [
            "pipeline",
            "retry",
            "--limit",
            "3",
            "--cooldown-hours",
            "2",
            "--force",
        ]
    ) == 1
    assert settings.require_calls == 1
    assert settings.fb_number_require_calls == 1
    assert run_calls == [
        {
            "limit": 3,
            "cooldown": timedelta(hours=2),
            "force": True,
            "dry_run": False,
        }
    ]
    assert provider_instances[0].close_calls == 1
    assert "retry_pending=1" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("raised", "expected_exit"),
    [
        (DatabaseError("Database operation failed."), 5),
        (KeyboardInterrupt(), 130),
    ],
)
def test_pipeline_retry_closes_provider_when_execution_stops(
    raised: BaseException,
    expected_exit: int,
    monkeypatch,
) -> None:
    settings = Settings()
    provider_instances: list[object] = []

    class Repository:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    class Provider:
        close_calls = 0

        @classmethod
        def from_settings(cls, _settings):
            instance = cls()
            provider_instances.append(instance)
            return instance

        def close(self) -> None:
            self.close_calls += 1

    class Service:
        def __init__(self, *_args) -> None:
            pass

        def run(self, **_kwargs):
            raise raised

    monkeypatch.setattr(
        "fb_crawl.cli.pipeline.load_pipeline_settings",
        lambda: settings,
    )
    monkeypatch.setattr("fb_crawl.cli.pipeline.PostgresRepository", Repository)
    monkeypatch.setattr("fb_crawl.cli.pipeline.FBNumberProvider", Provider)
    monkeypatch.setattr(
        "fb_crawl.cli.pipeline.EnrichmentPipeline",
        lambda provider: provider,
    )
    monkeypatch.setattr(
        "fb_crawl.cli.pipeline.PipelinePersistenceService",
        lambda repository: repository,
    )
    monkeypatch.setattr("fb_crawl.cli.pipeline.FBNumberRetryService", Service)

    assert main(["pipeline", "retry"]) == expected_exit
    assert provider_instances[0].close_calls == 1


@pytest.mark.parametrize(
    "arguments",
    [
        ["--limit", "0"],
        ["--limit", "-1"],
        ["--cooldown-hours", "-1"],
    ],
)
def test_pipeline_retry_rejects_invalid_controls_before_runtime(
    arguments: list[str],
    monkeypatch,
    capsys,
) -> None:
    def unexpected_settings():
        raise AssertionError("settings must not load for invalid CLI values")

    monkeypatch.setattr(
        "fb_crawl.cli.pipeline.load_pipeline_settings",
        unexpected_settings,
    )

    assert main(["pipeline", "retry", *arguments]) == 2
    assert "retry" in capsys.readouterr().err.casefold()
