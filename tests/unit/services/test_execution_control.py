from __future__ import annotations

import pytest

from fb_crawl.adapters.browser.account_safety import SafetyCode, SafetySignal
from fb_crawl.services.execution_control import (
    AccountSafetyStop,
    CrawlCancelled,
    NoOpExecutionControl,
    NoOpNavigationPacer,
    SafeNavigationPacer,
    guard_cancellation,
    guard_execution,
)


class CancellingControl:
    def is_cancel_requested(self) -> bool:
        return True

    def emit(self, event_type: str, *, counters=None, safe_message: str = "") -> None:
        raise AssertionError("cancelled work must not emit")

    def check_account_safety(self, browser: object) -> SafetySignal | None:
        raise AssertionError("cancelled work must not inspect the browser")


class SignallingControl:
    def __init__(self, signal: SafetySignal) -> None:
        self.signal = signal
        self.inspected = 0

    def is_cancel_requested(self) -> bool:
        return False

    def emit(self, event_type: str, *, counters=None, safe_message: str = "") -> None:
        return None

    def check_account_safety(self, browser: object) -> SafetySignal | None:
        self.inspected += 1
        return self.signal


def test_noop_execution_control_is_inert() -> None:
    """Break caught: interactive callers gain worker side effects by default."""
    control = NoOpExecutionControl()

    assert control.is_cancel_requested() is False
    control.emit("target_progress", counters={"steps_completed": 1})
    assert control.check_account_safety(object()) is None


def test_cancellation_only_guard_is_usable_before_browser_bootstrap() -> None:
    """Break caught: pre-session cancellation attempts account inspection."""
    with pytest.raises(CrawlCancelled):
        guard_cancellation(CancellingControl())


def test_full_guard_checks_cancellation_before_browser_inspection() -> None:
    """Break caught: a cancelled job touches the browser before it stops."""
    with pytest.raises(CrawlCancelled):
        guard_execution(CancellingControl(), object())


def test_full_guard_raises_the_original_account_safety_signal() -> None:
    """Break caught: safety signals lose their identity at the worker boundary."""
    signal = SafetySignal(SafetyCode.CAPTCHA, "Facebook CAPTCHA requires manual review.", True)

    with pytest.raises(AccountSafetyStop) as captured:
        guard_execution(SignallingControl(signal), object())

    assert captured.value.signal is signal


def test_account_safety_stop_rejects_a_bypassed_signal_without_stringifying_it() -> None:
    """Break caught: a forged signal leaks arbitrary data through a stop exception."""
    class RawValue:
        def __str__(self) -> str:
            raise AssertionError("must not stringify raw browser content")

    forged = object.__new__(SafetySignal)
    object.__setattr__(forged, "code", SafetyCode.CAPTCHA)
    object.__setattr__(forged, "safe_message", RawValue())
    object.__setattr__(forged, "manual_review", True)

    with pytest.raises(TypeError):
        AccountSafetyStop(forged)


def test_noop_navigation_pacer_returns_immediately() -> None:
    """Break caught: the interactive CLI receives a mandatory worker delay."""
    assert NoOpNavigationPacer().wait() is None


def test_safe_navigation_pacer_waits_remaining_interval_in_cancellable_slices() -> None:
    """Break caught: consecutive navigations violate the fixed minimum interval."""
    now = [0.0]
    sleeps: list[float] = []

    class RecordingControl:
        def __init__(self) -> None:
            self.cancellation_checks = 0

        def is_cancel_requested(self) -> bool:
            self.cancellation_checks += 1
            return False

        def emit(self, event_type: str, *, counters=None, safe_message: str = "") -> None:
            return None

        def check_account_safety(self, browser: object) -> SafetySignal | None:
            return None

    control = RecordingControl()

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    pacer = SafeNavigationPacer(
        8,
        control=control,
        monotonic=lambda: now[0],
        sleep=sleep,
        sleep_slice_seconds=1,
    )

    pacer.wait()
    now[0] = 3.0
    pacer.wait()

    assert sleeps == [1.0, 1.0, 1.0, 1.0, 1.0]
    assert control.cancellation_checks >= 6


def test_safe_navigation_pacer_rechecks_cancellation_after_the_final_sleep_slice() -> None:
    """Break caught: cancellation arriving in the final sleep slice starts navigation."""
    now = [0.0]

    class FinalSliceCancellingControl:
        cancelled = False

        def is_cancel_requested(self) -> bool:
            return self.cancelled

        def emit(self, event_type: str, *, counters=None, safe_message: str = "") -> None:
            return None

        def check_account_safety(self, browser: object) -> SafetySignal | None:
            return None

    control = FinalSliceCancellingControl()

    def sleep(seconds: float) -> None:
        now[0] += seconds
        control.cancelled = True

    pacer = SafeNavigationPacer(8, control, monotonic=lambda: now[0], sleep=sleep)
    pacer.wait()
    now[0] = 7.0

    with pytest.raises(CrawlCancelled):
        pacer.wait()


def test_safe_navigation_pacer_recomputes_remaining_interval_from_the_clock() -> None:
    """Break caught: delayed sleep causes redundant pacing slices."""
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] = 8.0

    pacer = SafeNavigationPacer(8, monotonic=lambda: now[0], sleep=sleep)
    pacer.wait()
    now[0] = 3.0
    pacer.wait()

    assert sleeps == [1.0]


@pytest.mark.parametrize("later_time", [8.0, 20.0])
def test_safe_navigation_pacer_does_not_sleep_after_reaching_or_passing_the_deadline(
    later_time: float,
) -> None:
    """Break caught: a late navigation call enters pacing math and raises or sleeps."""
    now = [0.0]

    def sleep(_: float) -> None:
        raise AssertionError("late navigation must not sleep")

    pacer = SafeNavigationPacer(8, monotonic=lambda: now[0], sleep=sleep)
    pacer.wait()
    now[0] = later_time

    assert pacer.wait() is None


def test_safe_navigation_pacer_allows_normal_early_wakeups() -> None:
    """Break caught: ordinary early wakeups are treated as a clock failure."""
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += 0.5

    pacer = SafeNavigationPacer(8, monotonic=lambda: now[0], sleep=sleep)
    pacer.wait()
    now[0] = 3.0
    pacer.wait()

    assert len(sleeps) == 10


def test_safe_navigation_pacer_bounds_asymptotic_clock_progress() -> None:
    """Break caught: a strictly increasing clock can cause an unbounded sleep loop."""
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += (8.0 - now[0]) / 2.0

    pacer = SafeNavigationPacer(8, monotonic=lambda: now[0], sleep=sleep)
    pacer.wait()
    now[0] = 3.0

    with pytest.raises(ValueError):
        pacer.wait()
    assert len(sleeps) <= 12


@pytest.mark.parametrize("clock_value", [float("nan"), float("inf"), float("-inf")])
def test_safe_navigation_pacer_rejects_nonfinite_clock_values(clock_value: float) -> None:
    """Break caught: a corrupt clock value produces unbounded or invalid pacing."""
    pacer = SafeNavigationPacer(8, monotonic=lambda: clock_value, sleep=lambda _: None)

    with pytest.raises(ValueError):
        pacer.wait()


def test_safe_navigation_pacer_rejects_a_backwards_or_stalled_clock() -> None:
    """Break caught: a non-monotonic fake clock can loop or weaken the interval."""
    now = [10.0]

    pacer = SafeNavigationPacer(8, monotonic=lambda: now[0], sleep=lambda _: None)
    pacer.wait()
    now[0] = 9.0

    with pytest.raises(ValueError):
        pacer.wait()


def test_safe_navigation_pacer_rejects_a_clock_that_does_not_advance_after_sleep() -> None:
    """Break caught: a stalled injected clock creates an infinite sleep loop."""
    now = [0.0]
    sleeps: list[float] = []

    pacer = SafeNavigationPacer(
        8,
        monotonic=lambda: now[0],
        sleep=lambda seconds: sleeps.append(seconds),
    )
    pacer.wait()
    now[0] = 3.0

    with pytest.raises(ValueError):
        pacer.wait()
    assert sleeps == [1.0]


@pytest.mark.parametrize("minimum", [0, 7.99, True, "8", float("nan"), float("inf")])
def test_safe_navigation_pacer_rejects_unsafe_or_invalid_minimum(minimum: object) -> None:
    """Break caught: callers can weaken or corrupt the mandatory navigation interval."""
    with pytest.raises((TypeError, ValueError)):
        SafeNavigationPacer(minimum)  # type: ignore[arg-type]


@pytest.mark.parametrize("slice_seconds", [0, -1, True, "1", 1.01, float("nan"), float("inf")])
def test_safe_navigation_pacer_rejects_invalid_or_unbounded_sleep_slices(slice_seconds: object) -> None:
    """Break caught: pacing slices can be invalid or too long to observe cancellation."""
    with pytest.raises((TypeError, ValueError), match="<= 1 second"):
        SafeNavigationPacer(8, sleep_slice_seconds=slice_seconds)  # type: ignore[arg-type]
