from __future__ import annotations


import json
import os


from pathlib import Path
from fb_crawl.core.exceptions import SessionError

from collections.abc import Mapping
from urllib.parse import urlparse

FACEBOOK_HOME = "https://www.facebook.com/"

BLOCKED_AUTH_PATHS = (
    "/login",
    "/checkpoint",
    "/two_step_verification",
)


COOKIE_FIELDS = (
    "name",
    "value",
    "path",
    "domain",
    "secure",
    "httpOnly",
    "expiry",
    "sameSite",
)


VALID_SAME_SITE = frozenset(
    {
        "Strict",
        "Lax",
        "None",
    }
)


def is_authenticated(browser) -> bool:
    current_url = str(browser.current_url or "")

    path = urlparse(current_url).path.lower()

    if any(path.startswith(prefix) for prefix in BLOCKED_AUTH_PATHS):
        return False

    return any(
        cookie.get("name") == "c_user" and bool(cookie.get("value"))
        for cookie in browser.get_cookies()
        if isinstance(cookie, Mapping)
    )


def _compatible_cookie(
    value: object,
) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None

    if not isinstance(
        value.get("name"),
        str,
    ):
        return None

    if not isinstance(
        value.get("value"),
        str,
    ):
        return None

    cookie = {field: value[field] for field in COOKIE_FIELDS if field in value}

    if cookie.get("sameSite") not in VALID_SAME_SITE:
        cookie.pop(
            "sameSite",
            None,
        )

    if "expiry" in cookie:
        try:
            cookie["expiry"] = int(cookie["expiry"])
        except (TypeError, ValueError):
            cookie.pop("expiry")

    return cookie


class SessionStore:
    def __init__(
        self,
        path: Path,
    ) -> None:
        self.path = Path(path)

    def restore(
        self,
        browser,
    ) -> bool:
        if not self.path.is_file():
            return False

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ):
            return False

        if not isinstance(payload, list):
            return False

        cookies = [
            cookie
            for item in payload
            if (cookie := _compatible_cookie(item)) is not None
        ]

        if not cookies:
            return False

        try:
            browser.get(FACEBOOK_HOME)

            for cookie in cookies:
                browser.add_cookie(cookie)

            browser.refresh()

            return is_authenticated(browser)

        except Exception:
            # Session restoration failed, so we treat the session as invalid. The browser may have
            # regarded as invalid session.
            return False

    def save(
        self,
        browser,
    ) -> None:
        if not is_authenticated(browser):
            raise SessionError("Cannot save without a valid " "authenticated session.")

        cookies = [
            cookie
            for item in browser.get_cookies()
            if (cookie := _compatible_cookie(item)) is not None
        ]

        temporary = self.path.with_name(self.path.name + ".tmp")

        try:
            self.path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            descriptor = os.open(
                temporary,
                (os.O_WRONLY | os.O_CREAT | os.O_TRUNC),
                0o600,
            )

            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
            ) as file:
                json.dump(
                    cookies,
                    file,
                    ensure_ascii=False,
                    indent=2,
                )

                file.write("\n")
                file.flush()
                os.fsync(file.fileno())

            os.replace(
                temporary,
                self.path,
            )

            try:
                os.chmod(
                    self.path,
                    0o600,
                )
            except OSError:
                # Windows không đảm bảo Unix permission bits.
                pass

        except (
            OSError,
            TypeError,
            ValueError,
        ) as error:
            raise SessionError(
                f"Cannot persist session file " f"{self.path}."
            ) from error

        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
