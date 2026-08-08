from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

from fb_crawl.core.exceptions import ExportError


@contextmanager
def atomic_text_writer(
    destination: Path,
    *,
    encoding: str,
    newline: str | None = None,
) -> Iterator[TextIO]:
    destination = Path(destination)

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = destination.with_name(destination.name + ".tmp")

    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )

        with os.fdopen(
            descriptor,
            "w",
            encoding=encoding,
            newline=newline,
        ) as file:
            yield file
            file.flush()
            os.fsync(file.fileno())

        os.replace(
            temporary,
            destination,
        )

    except OSError as error:
        raise ExportError(
            ("Cannot write output file " f"{destination}."),
            target=str(destination),
        ) from error

    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def atomic_output_path(
    destination: Path,
    *,
    temporary_suffix: str = ".tmp",
) -> Iterator[Path]:
    destination = Path(destination)
    temporary = destination.with_name(destination.name + temporary_suffix)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)

        yield temporary

        with temporary.open("rb+") as file:
            os.fsync(file.fileno())

        os.replace(temporary, destination)

    except OSError as error:
        raise ExportError(
            f"Cannot write output file {destination}.",
            target=str(destination),
        ) from error

    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
