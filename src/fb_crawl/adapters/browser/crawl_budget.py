from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True, slots=True)
class CrawlCollection:
    """Backward-compatible two-value collection result plus completion state."""

    html: str
    attempts: int
    budget_exhausted: bool = False

    def __iter__(self) -> Iterator[str | int]:
        yield self.html
        yield self.attempts


class CrawlBudget:
    def __init__(
        self,
        *,
        steps: int | None,
        max_duration_seconds: float | None,
        monotonic_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self._steps = steps
        self._monotonic = monotonic_func
        self._deadline = (
            monotonic_func() + max_duration_seconds
            if max_duration_seconds is not None
            else None
        )

    @property
    def deadline_monotonic(self) -> float | None:
        return self._deadline

    def allows(self, attempts: int) -> bool:
        if self._steps is not None and attempts >= self._steps:
            return False

        return self._deadline is None or self._monotonic() < self._deadline

    def wait_timeout(self, default: float) -> float:
        if self._deadline is None:
            return default

        remaining = self._deadline - self._monotonic()
        return max(0.001, min(default, remaining))

    def exhausted(self, attempts: int) -> bool:
        return (
            (self._steps is not None and attempts >= self._steps)
            or (self._deadline is not None and self._monotonic() >= self._deadline)
        )
