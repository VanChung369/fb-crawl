from __future__ import annotations

from unittest.mock import MagicMock
from fb_crawl.adapters.browser.human import (
    human_scroll,
    human_micro_pause,
    human_mouse_wiggle,
)


def test_human_scroll_executes_smooth_script() -> None:
    calls = []

    def fake_execute_script(script: str, *args):
        calls.append((script, args))
        return None

    browser = MagicMock()
    browser.execute_script.side_effect = fake_execute_script

    human_scroll(browser)

    assert len(calls) == 1
    assert "scrollTo" in calls[0][0]
    assert "smooth" in calls[0][0]


def test_human_micro_pause_calls_sleep_with_jitter() -> None:
    sleeps = []
    human_micro_pause(
        min_seconds=0.5,
        max_seconds=1.0,
        sleep_func=sleeps.append,
        jitter_func=lambda low, high: 0.75,
    )
    assert sleeps == [0.75]


def test_human_mouse_wiggle_does_not_crash_on_mock() -> None:
    browser = MagicMock()
    human_mouse_wiggle(browser)
