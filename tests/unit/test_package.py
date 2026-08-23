from email.parser import Parser
import importlib.util
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

import fb_crawl


ROOT = Path(__file__).resolve().parents[2]


def test_package_exposes_version() -> None:
    assert fb_crawl.__version__ == "0.1.0"


@pytest.fixture(scope="module")
def built_wheel() -> Path:
    """Build the distributable so assertions exercise the installed artifact."""

    build_spec = importlib.util.find_spec("build")
    if build_spec is None or build_spec.origin is None:
        pytest.skip("the optional development build frontend is not installed")

    output = ROOT / "runtime" / "task15-wheel-check"
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(output),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = output / "fb_crawl-0.1.0-py3-none-any.whl"
    assert wheel.is_file()
    return wheel


def test_wheel_declares_api_extra_with_pydantic(built_wheel: Path) -> None:
    """Break caught: API installs omit the schema runtime dependency."""

    with ZipFile(built_wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = Parser().parsestr(
            archive.read(metadata_name).decode("utf-8")
        )

    assert "api" in metadata.get_all("Provides-Extra", [])
    api_requirements = [
        value
        for value in metadata.get_all("Requires-Dist", [])
        if value.lower().startswith("pydantic")
    ]
    assert any('extra == "api"' in value for value in api_requirements)


def test_wheel_contains_job_orchestration_migration(built_wheel: Path) -> None:
    """Break caught: a packaged install cannot migrate the job API schema."""

    with ZipFile(built_wheel) as archive:
        names = set(archive.namelist())

    assert "fb_data_pipeline/migrations/003_job_orchestration.sql" in names
