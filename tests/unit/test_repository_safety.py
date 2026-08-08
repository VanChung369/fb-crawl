import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_and_secret_paths_are_ignored() -> None:
    for relative in (
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
