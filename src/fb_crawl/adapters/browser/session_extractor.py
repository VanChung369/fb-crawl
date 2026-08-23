"""Automated Facebook browser login and session cookie extractor."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fb_crawl.adapters.browser.driver import create_browser
from fb_crawl.adapters.browser.session import SessionStore, is_authenticated
from fb_crawl.config import BrowserSettings
from fb_crawl.core.exceptions import SessionError


def extract_session_from_credentials(
    email: str,
    password: str,
    *,
    two_factor_code: str | None = None,
    proxy: str | None = None,
    output_path: Path | None = None,
    headless: bool = True,
    timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    """Log in to Facebook via browser and extract session cookies."""

    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    clean_email = email.strip()
    clean_password = password.strip()
    if not clean_email or not clean_password:
        raise SessionError("Email / Số điện thoại và mật khẩu không được để trống.")

    settings = BrowserSettings(
        headless=headless,
        proxy=proxy,
        browser_timeout_seconds=timeout_seconds,
    )

    browser = create_browser(settings)
    try:
        browser.get("https://www.facebook.com/login")
        wait = WebDriverWait(browser, timeout_seconds)

        # 1. Fill email
        email_elem = wait.until(
            EC.presence_of_element_located((By.NAME, "email"))
        )
        email_elem.clear()
        for char in clean_email:
            email_elem.send_keys(char)
            time.sleep(0.02)

        # 2. Fill password
        pass_elem = wait.until(
            EC.presence_of_element_located((By.NAME, "pass"))
        )
        pass_elem.clear()
        for char in clean_password:
            pass_elem.send_keys(char)
            time.sleep(0.02)

        # 3. Submit
        submit_btn = None
        for selector in [
            (By.NAME, "login"),
            (By.ID, "loginbutton"),
            (By.CSS_SELECTOR, "form#login_form [role='button']"),
            (By.CSS_SELECTOR, "button[type='submit']"),
        ]:
            try:
                submit_btn = browser.find_element(*selector)
                if submit_btn.is_displayed():
                    break
            except Exception:
                continue

        if submit_btn:
            submit_btn.click()
        else:
            pass_elem.submit()

        # 4. Handle 2FA if present and code is provided
        time.sleep(3.0)
        if two_factor_code:
            clean_2fa = two_factor_code.strip()
            for code_selector in [
                (By.ID, "approvals_code"),
                (By.NAME, "approvals_code"),
                (By.CSS_SELECTOR, "input[name='approvals_code']"),
                (By.CSS_SELECTOR, "input[type='text']"),
            ]:
                try:
                    two_fa_input = browser.find_element(*code_selector)
                    if two_fa_input.is_displayed():
                        two_fa_input.clear()
                        two_fa_input.send_keys(clean_2fa)
                        time.sleep(0.5)

                        # Click checkpoint submit
                        for btn_sel in [
                            (By.ID, "checkpointSubmitButton"),
                            (By.CSS_SELECTOR, "button[type='submit']"),
                            (By.CSS_SELECTOR, "#checkpointSubmitButton"),
                        ]:
                            try:
                                btn = browser.find_element(*btn_sel)
                                btn.click()
                                break
                            except Exception:
                                continue
                        break
                except Exception:
                    continue

        # 5. Wait for authenticated session
        deadline = time.monotonic() + timeout_seconds
        authenticated = False
        while time.monotonic() < deadline:
            if is_authenticated(browser):
                authenticated = True
                break
            time.sleep(1.0)

        if not authenticated:
            current_url = str(browser.current_url or "")
            if "checkpoint" in current_url:
                raise SessionError("Tài khoản bị dính Facebook Checkpoint / Yêu cầu xác minh 2FA.")
            if "login" in current_url:
                raise SessionError("Đăng nhập thất bại. Vui lòng kiểm tra lại tài khoản hoặc mật khẩu.")
            raise SessionError("Hết thời gian chờ xác thực đăng nhập Facebook.")

        # 6. Extract cookies and save if output_path is provided
        cookies = browser.get_cookies()
        if output_path:
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            store = SessionStore(out_p)
            store.save(browser)

        return cookies
    finally:
        try:
            browser.quit()
        except Exception:
            pass
