from __future__ import annotations

from pathlib import Path

from fb_crawl.core.atomic import atomic_output_path, atomic_text_writer
from fb_crawl.exporters.atomic import (
    atomic_output_path as exporter_output_path,
    atomic_text_writer as exporter_text_writer,
)


def test_exporter_atomic_imports_are_compatibility_aliases(tmp_path: Path) -> None:
    """Break caught: moving atomic I/O changes exporter-visible behavior."""
    destination = tmp_path / "result.txt"

    with exporter_text_writer(destination, encoding="utf-8") as file:
        file.write("stable")

    with exporter_output_path(tmp_path / "other.txt") as temporary:
        temporary.write_text("other", encoding="utf-8")

    assert exporter_text_writer is atomic_text_writer
    assert exporter_output_path is atomic_output_path
    assert destination.read_text(encoding="utf-8") == "stable"
    assert (tmp_path / "other.txt").read_text(encoding="utf-8") == "other"
