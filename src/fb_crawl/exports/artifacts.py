from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
import os
from pathlib import Path
from uuid import UUID, uuid4

from fb_crawl.core.atomic import atomic_output_path
from fb_crawl.core.exceptions import ValidationError
from fb_crawl.exports.formatters import write_history_csv, write_history_xlsx
from fb_crawl.exports.models import ExportFormat
from fb_crawl.history.models import HistoryItem


class ExportArtifactStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()

    def write(
        self,
        job_id: UUID,
        format_name: ExportFormat,
        writer: Callable[[Path], object],
    ) -> str:
        relative = Path(job_id.hex) / f"{uuid4().hex}.{format_name.value}"
        destination = self.resolve(relative.as_posix())
        suffix = ".tmp.xlsx" if format_name is ExportFormat.XLSX else ".tmp"
        with atomic_output_path(destination, temporary_suffix=suffix) as temporary:
            writer(temporary)
            os.chmod(temporary, 0o600)
        return relative.as_posix()

    def write_history(
        self,
        job_id: UUID,
        format_name: ExportFormat,
        rows: Iterable[HistoryItem],
    ) -> str:
        writer = (
            write_history_csv
            if format_name is ExportFormat.CSV
            else write_history_xlsx
        )
        return self.write(job_id, format_name, lambda path: writer(rows, path))

    def resolve(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValidationError("Invalid export artifact path.")
        supplied = Path(relative_path)
        if supplied.is_absolute() or supplied.drive:
            raise ValidationError("Invalid export artifact path.")
        candidate = (self.root / supplied).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValidationError("Invalid export artifact path.")
        return candidate

    def delete(self, relative_path: str) -> bool:
        path = self.resolve(relative_path)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return True

    def purge_older_than(self, before: datetime) -> tuple[str, ...]:
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValidationError("Invalid export artifact retention time.")
        if not self.root.exists():
            return ()
        removed: list[str] = []
        for candidate in self.root.rglob("*"):
            try:
                resolved = candidate.resolve()
                if (
                    not resolved.is_relative_to(self.root)
                    or not candidate.is_file()
                    or candidate.stat().st_mtime > before.timestamp()
                ):
                    continue
                relative = candidate.relative_to(self.root).as_posix()
                candidate.unlink()
                removed.append(relative)
                self._remove_empty_parents(candidate.parent)
            except (FileNotFoundError, OSError):
                continue
        return tuple(sorted(removed))

    def _remove_empty_parents(self, directory: Path) -> None:
        while directory != self.root and directory.is_relative_to(self.root):
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent
