"""Conservative, non-interactive account-safety classification."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final
from unicodedata import normalize
from urllib.parse import parse_qs, urlparse

from fb_crawl.core.jobs import SafetyCode
from fb_crawl.core.urls import FACEBOOK_HOSTS


_MAX_VISIBLE_TEXT: Final = 100_000
_INVALID_URL: Final = object()

_CANONICAL_SIGNAL_VALUES: Final = {
    SafetyCode.SESSION_EXPIRED: ("Facebook session is unavailable.", False),
    SafetyCode.CHECKPOINT: ("Facebook checkpoint requires manual review.", True),
    SafetyCode.TWO_FACTOR: ("Facebook two-factor verification requires manual review.", True),
    SafetyCode.CAPTCHA: ("Facebook CAPTCHA requires manual review.", True),
    SafetyCode.TEMPORARY_BLOCK: ("Facebook temporarily blocked requests.", False),
    SafetyCode.ACCOUNT_RESTRICTED: ("Facebook account restriction requires manual review.", True),
    SafetyCode.UNUSUAL_ACTIVITY: ("Facebook unusual activity requires manual review.", True),
    SafetyCode.ACCOUNT_RECOVERY: ("Facebook account recovery requires manual review.", True),
}

_PATH_SIGNAL_BY_FIRST_SEGMENT: Final = {
    "checkpoint": SafetyCode.CHECKPOINT,
    "checkpoint.php": SafetyCode.CHECKPOINT,
    "two_step_verification": SafetyCode.TWO_FACTOR,
    "two_step_verification.php": SafetyCode.TWO_FACTOR,
    "two_factor": SafetyCode.TWO_FACTOR,
    "two_factor.php": SafetyCode.TWO_FACTOR,
    "captcha": SafetyCode.CAPTCHA,
    "captcha.php": SafetyCode.CAPTCHA,
    "temporary_block": SafetyCode.TEMPORARY_BLOCK,
    "temporary_block.php": SafetyCode.TEMPORARY_BLOCK,
    "temporarily_blocked": SafetyCode.TEMPORARY_BLOCK,
    "temporarily_blocked.php": SafetyCode.TEMPORARY_BLOCK,
    "account_restricted": SafetyCode.ACCOUNT_RESTRICTED,
    "account_restricted.php": SafetyCode.ACCOUNT_RESTRICTED,
    "restricted": SafetyCode.ACCOUNT_RESTRICTED,
    "restricted.php": SafetyCode.ACCOUNT_RESTRICTED,
    "recover": SafetyCode.ACCOUNT_RECOVERY,
    "recover.php": SafetyCode.ACCOUNT_RECOVERY,
    "account_recovery": SafetyCode.ACCOUNT_RECOVERY,
    "account_recovery.php": SafetyCode.ACCOUNT_RECOVERY,
    "login": SafetyCode.SESSION_EXPIRED,
    "login.php": SafetyCode.SESSION_EXPIRED,
    "logout": SafetyCode.SESSION_EXPIRED,
    "logout.php": SafetyCode.SESSION_EXPIRED,
}

_QUERY_SIGNAL_BY_KEY: Final = (
    ("checkpoint", SafetyCode.CHECKPOINT),
    ("two_factor", SafetyCode.TWO_FACTOR),
    ("captcha", SafetyCode.CAPTCHA),
)

# These are intentionally literal and small.  They are visible-text indicators,
# not selector or evasion logic.
_TEXT_MARKERS: Final = (
    (
        SafetyCode.ACCOUNT_RESTRICTED,
        (
            "your account is restricted",
            "account restricted",
            "tài khoản của bạn bị hạn chế",
        ),
    ),
    (
        SafetyCode.CHECKPOINT,
        (
            "confirm it's you",
            "confirm it is you",
            "xác nhận đó là bạn",
        ),
    ),
    (
        SafetyCode.TWO_FACTOR,
        (
            "two-factor authentication",
            "two factor authentication",
            "xác thực hai yếu tố",
        ),
    ),
    (
        SafetyCode.CAPTCHA,
        (
            "captcha",
        ),
    ),
    (
        SafetyCode.UNUSUAL_ACTIVITY,
        (
            "unusual activity",
            "suspicious activity",
            "hoạt động bất thường",
        ),
    ),
    (
        SafetyCode.ACCOUNT_RECOVERY,
        (
            "facebook has locked your account. start the account recovery process.",
            "facebook đã khóa tài khoản của bạn. hãy bắt đầu quá trình khôi phục tài khoản.",
        ),
    ),
    (
        SafetyCode.TEMPORARY_BLOCK,
        (
            "temporarily blocked",
            "we limit how often you can do certain things",
            "tạm thời bị chặn",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class SafetySignal:
    code: SafetyCode
    safe_message: str
    manual_review: bool

    def __post_init__(self) -> None:
        if type(self.code) is not SafetyCode:
            raise TypeError("Safety signal code must be a SafetyCode.")
        if type(self.safe_message) is not str:
            raise TypeError("Safety signal message must be a plain string.")
        if type(self.manual_review) is not bool:
            raise TypeError("Safety signal review flag must be a boolean.")
        if (self.safe_message, self.manual_review) != _CANONICAL_SIGNAL_VALUES[self.code]:
            raise ValueError("Safety signal fields must match the canonical code policy.")


def _visible_text(browser: object) -> str:
    execute_script = getattr(browser, "execute_script")
    value = execute_script("return document.body ? document.body.innerText : '';")
    return value if isinstance(value, str) else ""


def _signal(code: SafetyCode) -> SafetySignal:
    safe_message, manual_review = _CANONICAL_SIGNAL_VALUES[code]
    return SafetySignal(
        code=code,
        safe_message=safe_message,
        manual_review=manual_review,
    )


def _facebook_url(browser: object) -> tuple[tuple[str, ...], dict[str, list[str]]] | object:
    try:
        current_url = getattr(browser, "current_url")
        if not isinstance(current_url, str) or not current_url:
            return _INVALID_URL
        parsed = urlparse(current_url)
        host = parsed.hostname.casefold() if parsed.hostname else ""
        _ = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or host not in FACEBOOK_HOSTS
        ):
            return _INVALID_URL
        path_segments = tuple(part.casefold() for part in parsed.path.split("/") if part)
        return path_segments, parse_qs(parsed.query, keep_blank_values=True)
    except Exception:
        return _INVALID_URL


def _url_signal(path_segments: tuple[str, ...], query: Mapping[str, list[str]]) -> SafetyCode | None:
    first = path_segments[0] if path_segments else ""
    query_keys = frozenset(key.casefold() for key in query)
    for key, code in _QUERY_SIGNAL_BY_KEY:
        if key in query_keys:
            return code
    return _PATH_SIGNAL_BY_FIRST_SEGMENT.get(first)


def _has_c_user(browser: object) -> bool:
    try:
        get_cookies = getattr(browser, "get_cookies")
        cookies = get_cookies()
    except Exception:
        return False

    if not isinstance(cookies, list):
        return False
    return any(
        isinstance(cookie, Mapping)
        and cookie.get("name") == "c_user"
        and isinstance(cookie.get("value"), str)
        and bool(cookie["value"])
        for cookie in cookies
    )


def _normalized_visible_text(browser: object, text_func: Callable[[object], str]) -> str:
    try:
        value = text_func(browser)
    except Exception:  # Browser adapters must not surface browser-provided detail.
        return ""
    if not isinstance(value, str):
        return ""
    return " ".join(normalize("NFKC", value[:_MAX_VISIBLE_TEXT]).casefold().split())


def classify_account_safety(
    browser: object,
    *,
    text_func: Callable[[object], str] = _visible_text,
) -> SafetySignal | None:
    """Return a stable safety signal without retaining URL or visible-text data."""
    if not callable(text_func):
        raise TypeError("text_func must be callable.")

    parsed = _facebook_url(browser)
    if parsed is _INVALID_URL:
        return _signal(SafetyCode.SESSION_EXPIRED)
    url_signal = _url_signal(*parsed)
    if url_signal is not None:
        return _signal(url_signal)

    if not _has_c_user(browser):
        return _signal(SafetyCode.SESSION_EXPIRED)

    text = _normalized_visible_text(browser, text_func)
    for code, markers in _TEXT_MARKERS:
        if any(marker in text for marker in markers):
            return _signal(code)
    return None
