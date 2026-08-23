"""Human-like browser interaction helpers to minimize bot detection."""

from __future__ import annotations

import random
import time
from collections.abc import Callable


def human_scroll(
    browser: object,
    *,
    steps_range: tuple[int, int] = (3, 6),
    chunk_delay_range: tuple[float, float] = (0.04, 0.12),
    sleep_func: Callable[[float], None] = time.sleep,
    jitter_func: Callable[[float, float], float] = random.uniform,
) -> None:
    """Scroll smoothly to the bottom of the page in a bot-resistant manner."""
    execute_script = getattr(browser, "execute_script", None)
    if not callable(execute_script):
        return

    script = (
        "try { "
        "  const target = document.body ? document.body.scrollHeight : 0; "
        "  window.scrollTo({top: target, behavior: 'smooth'}); "
        "} catch(e) { "
        "  window.scrollTo(0, document.body.scrollHeight); "
        "}"
    )
    try:
        execute_script(script)
    except Exception:
        try:
            execute_script("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass


def human_micro_pause(
    *,
    min_seconds: float = 0.2,
    max_seconds: float = 0.8,
    sleep_func: Callable[[float], None] = time.sleep,
    jitter_func: Callable[[float, float], float] = random.uniform,
) -> None:
    """Execute a randomized brief pause mimicking human reading or viewing."""
    pause = jitter_func(min_seconds, max_seconds)
    if pause > 0:
        sleep_func(pause)


def human_mouse_wiggle(browser: object) -> None:
    """Perform a slight randomized mouse movement if ActionChains is available."""
    try:
        from selenium.webdriver.common.action_chains import ActionChains

        actions = ActionChains(browser)
        dx = random.randint(-15, 15)
        dy = random.randint(-15, 15)
        actions.move_by_offset(dx, dy).perform()
    except Exception:
        # Gracefully ignore in non-standard or mock browser environments
        pass
