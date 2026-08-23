"""Narrow cooperative controls for authenticated collection."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fb_crawl.adapters.browser.account_safety import SafetySignal


class ExecutionControl(Protocol):
    def is_cancel_requested(self) -> bool: ...

    def emit(
        self,
        event_type: str,
        *,
        counters: Mapping[str, int] | None = None,
        safe_message: str = "",
    ) -> None: ...

    def check_account_safety(self, browser: object) -> SafetySignal | None: ...


class NavigationPacer(Protocol):
    def wait(self) -> None: ...


def _freeze_checkpoint_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_checkpoint_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_checkpoint_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class CheckpointInterruptionSnapshot:
    """Immutable, sanitized state retained when interruption persistence fails."""

    payload: Mapping[str, object]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "CheckpointInterruptionSnapshot":
        frozen = _freeze_checkpoint_value(payload)
        if not isinstance(frozen, Mapping):
            raise TypeError("Checkpoint interruption state must be a mapping.")
        return cls(frozen)


def attach_checkpoint_persistence_failure(
    stop: CrawlCancelled | JobBudgetReached,
    issue: object,
    payload: Mapping[str, object],
) -> None:
    """Expose only sanitized persistence-failure context on the original stop."""
    stop.checkpoint_issue = issue
    stop.checkpoint_snapshot = CheckpointInterruptionSnapshot.from_payload(payload)


class CrawlCancelled(RuntimeError):
    """Raised when cooperative cancellation has been requested."""

    code = "crawl_cancelled"

    def __init__(self) -> None:
        super().__init__("Crawl cancellation was requested.")


class JobBudgetReached(RuntimeError):
    """Raised when a worker-owned crawl budget has been exhausted."""

    code = "job_budget_reached"

    def __init__(self) -> None:
        super().__init__("Crawl job budget was reached.")


class AccountSafetyStop(RuntimeError):
    """Raised with the immutable, sanitized signal that halted collection."""

    code = "account_safety_stop"

    def __init__(self, signal: SafetySignal) -> None:
        from fb_crawl.adapters.browser.account_safety import SafetySignal

        if not isinstance(signal, SafetySignal):
            raise TypeError("Account safety stops require a SafetySignal.")
        canonical_signal = SafetySignal(
            signal.code,
            signal.safe_message,
            signal.manual_review,
        )
        self.signal = signal
        super().__init__(canonical_signal.safe_message)


class NoOpExecutionControl:
    """Compatibility control for the interactive CLI."""

    def is_cancel_requested(self) -> bool:
        return False

    def emit(
        self,
        event_type: str,
        *,
        counters: Mapping[str, int] | None = None,
        safe_message: str = "",
    ) -> None:
        return None

    def check_account_safety(self, browser: object) -> SafetySignal | None:
        return None


class NoOpNavigationPacer:
    """Compatibility pacer for the interactive CLI."""

    def wait(self) -> None:
        return None


NOOP_EXECUTION_CONTROL: ExecutionControl = NoOpExecutionControl()
NOOP_NAVIGATION_PACER: NavigationPacer = NoOpNavigationPacer()


def guard_cancellation(control: ExecutionControl) -> None:
    """Stop before browser/session work when cancellation has been requested."""
    if control.is_cancel_requested():
        raise CrawlCancelled()


def guard_execution(control: ExecutionControl, browser: object) -> None:
    """Check cancellation first, then convert a safety signal into a typed stop."""
    guard_cancellation(control)
    signal = control.check_account_safety(browser)
    if signal is not None:
        raise AccountSafetyStop(signal)


def cooperative_wait(
    seconds: float,
    *,
    control: ExecutionControl = NOOP_EXECUTION_CONTROL,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    deadline_monotonic: float | None = None,
    sleep_slice_seconds: float = 1.0,
) -> bool:
    """Wait cooperatively, returning false when a local deadline clips the wait.

    Worker controls are checked at most one second apart. The no-op CLI path
    retains a single injected sleeper call while still respecting a local
    crawl deadline.
    """
    numeric_values = (seconds, sleep_slice_seconds)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in numeric_values
    ):
        raise ValueError("Cooperative wait durations must be finite numbers.")
    if seconds < 0 or sleep_slice_seconds <= 0 or sleep_slice_seconds > 1:
        raise ValueError("Cooperative wait durations are outside safe bounds.")
    if deadline_monotonic is not None and (
        isinstance(deadline_monotonic, bool)
        or not isinstance(deadline_monotonic, (int, float))
        or not math.isfinite(deadline_monotonic)
    ):
        raise ValueError("Cooperative wait deadline must be finite.")
    if not callable(sleep) or not callable(monotonic):
        raise TypeError("Cooperative wait requires callable time functions.")

    guard_cancellation(control)
    if seconds == 0:
        return True

    requested = float(seconds)
    remaining = requested
    clipped = False
    if deadline_monotonic is not None:
        local_remaining = float(deadline_monotonic) - float(monotonic())
        if local_remaining <= 0:
            return False
        if local_remaining < remaining:
            remaining = local_remaining
            clipped = True

    if control is NOOP_EXECUTION_CONTROL or isinstance(control, NoOpExecutionControl):
        sleep(remaining)
        return not clipped

    while remaining > 0:
        guard_cancellation(control)
        duration = min(float(sleep_slice_seconds), remaining)
        sleep(duration)
        remaining -= duration
        guard_cancellation(control)
        if deadline_monotonic is not None and monotonic() >= deadline_monotonic:
            return False
    return not clipped


class SafeNavigationPacer:
    """Deterministically enforce a shared minimum interval between navigations."""

    def __init__(
        self,
        minimum_interval_seconds: float = 8.0,
        control: ExecutionControl = NOOP_EXECUTION_CONTROL,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        sleep_slice_seconds: float = 1.0,
    ) -> None:
        if (
            isinstance(minimum_interval_seconds, bool)
            or not isinstance(minimum_interval_seconds, (int, float))
            or not math.isfinite(minimum_interval_seconds)
            or minimum_interval_seconds < 8.0
        ):
            raise ValueError("Navigation pacing must be at least eight seconds.")
        if (
            isinstance(sleep_slice_seconds, bool)
            or not isinstance(sleep_slice_seconds, (int, float))
            or not math.isfinite(sleep_slice_seconds)
            or sleep_slice_seconds <= 0
            or sleep_slice_seconds > 1.0
        ):
            raise ValueError("Navigation pacing slices must be > 0 and <= 1 second.")
        if not callable(monotonic) or not callable(sleep):
            raise TypeError("Navigation pacing requires callable clock and sleep functions.")
        if not callable(getattr(control, "is_cancel_requested", None)):
            raise TypeError("Navigation pacing requires an execution control.")

        self._minimum_interval_seconds = float(minimum_interval_seconds)
        self._control = control
        self._monotonic = monotonic
        self._sleep = sleep
        self._sleep_slice_seconds = float(sleep_slice_seconds)
        self._last_navigation_at: float | None = None

    def _read_clock(self) -> float:
        value = self._monotonic()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("Navigation pacing clock must return a finite number.")
        return float(value)

    def wait(self) -> None:
        guard_cancellation(self._control)
        now = self._read_clock()
        if self._last_navigation_at is None:
            self._last_navigation_at = now
            return

        if now < self._last_navigation_at:
            raise ValueError("Navigation pacing clock moved backwards.")

        next_allowed_at = self._last_navigation_at + self._minimum_interval_seconds
        if not math.isfinite(next_allowed_at):
            raise ValueError("Navigation pacing clock is outside the supported range.")
        guard_cancellation(self._control)
        if now >= next_allowed_at:
            self._last_navigation_at = now
            return
        max_slices = math.ceil(
            (next_allowed_at - now) / (self._sleep_slice_seconds / 2)
        ) + 2
        for _ in range(max_slices):
            if now >= next_allowed_at:
                break
            guard_cancellation(self._control)
            duration = min(self._sleep_slice_seconds, next_allowed_at - now)
            self._sleep(duration)
            guard_cancellation(self._control)
            updated_now = self._read_clock()
            if updated_now <= now:
                raise ValueError("Navigation pacing clock did not advance after sleep.")
            now = updated_now
        else:
            raise ValueError("Navigation pacing clock did not reach the required interval.")

        self._last_navigation_at = now
