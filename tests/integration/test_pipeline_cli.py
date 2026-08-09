from __future__ import annotations

import pytest

from fb_crawl.cli.app import main


class Settings:
    database_url = "postgresql://user:secret@localhost/database"

    def __init__(self) -> None:
        self.require_calls = 0

    def require_database(self) -> None:
        self.require_calls += 1


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
