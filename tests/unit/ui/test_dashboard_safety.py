from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).parents[3]
INDEX_PATH = ROOT / "src" / "fb_ui" / "index.html"
APP_JS_PATH = ROOT / "src" / "fb_ui" / "js" / "app.js"
CSS_PATH = ROOT / "src" / "fb_ui" / "css" / "styles.css"


def test_dashboard_login_flow_does_not_collect_facebook_password() -> None:
    """Break caught: the WebUI sends Facebook password or 2FA through the API."""

    html = INDEX_PATH.read_text(encoding="utf-8")
    script = APP_JS_PATH.read_text(encoding="utf-8")

    assert "extract-password" not in html
    assert "extract-email" not in html
    assert "extract-2fa" not in html
    assert "/api/v1/sessions/extract" not in script
    assert "password:" not in script
    assert "two_factor_code" not in script
    assert "launch-login" in script


def test_dashboard_keeps_settings_token_editable_and_proxy_credentials_hidden() -> None:
    """Break caught: FBNumber JWT is not visible/editable or proxies leak credentials."""

    script = APP_JS_PATH.read_text(encoding="utf-8")

    assert "tokenInput.value = data.api_token || ''" in script
    assert "api_token: tokenValue || null" in script
    assert "p.raw_url}</span>" not in script
    assert "p.display_url" in script


def test_dashboard_has_mobile_sidebar_layout() -> None:
    """Break caught: fixed desktop sidebar creates horizontal overflow on mobile."""

    css = CSS_PATH.read_text(encoding="utf-8")

    assert "@media (max-width: 768px)" in css
    assert "margin-left: 0" in css
    assert "position: sticky" in css


def test_dashboard_escapes_untrusted_text_before_html_templates() -> None:
    """Break caught: crawled names/messages are interpolated directly into HTML."""

    script = APP_JS_PATH.read_text(encoding="utf-8")

    assert "escapeHtml(value)" in script
    assert "escapeAttr(value)" in script
    assert "this.escapeHtml(" in script


def test_dashboard_exposes_safe_group_batch_creation_flow() -> None:
    """Break caught: user has to manually split a full-group crawl into jobs."""

    html = INDEX_PATH.read_text(encoding="utf-8")
    script = APP_JS_PATH.read_text(encoding="utf-8")

    assert "job-opt-auto-batch" in html
    assert "job-opt-batch-count" in html
    assert "/api/v1/jobs/group-batches" in script
    assert "batch_count:" in script
    assert "batch_size:" in script


def test_dashboard_does_not_send_max_users_for_profile_jobs() -> None:
    """Break caught: profile job creation sends relationship/member-only options."""

    script = APP_JS_PATH.read_text(encoding="utf-8")

    assert "max_users:" not in script
    assert "options.max_users =" in script
    assert "['members', 'friends', 'followers'].includes(action)" in script


def test_dashboard_error_toasts_render_above_open_modals() -> None:
    """Break caught: job errors render behind the modal backdrop."""

    css = CSS_PATH.read_text(encoding="utf-8")
    script = APP_JS_PATH.read_text(encoding="utf-8")

    modal_match = re.search(r"\.modal-backdrop\s*\{[^}]*z-index:\s*(\d+)", css)
    toast_match = re.search(r"\.toast-container\s*\{[^}]*z-index:\s*(\d+)", css)

    assert modal_match is not None
    assert toast_match is not None
    assert int(toast_match.group(1)) > int(modal_match.group(1))
    assert "this.showToast(`Lỗi tạo Job: ${err.message}`" in script
    assert "throwOnError: true" in script


def test_dashboard_session_pool_does_not_paint_unknown_accounts_green() -> None:
    """Break caught: unchecked or manual-review Facebook accounts look healthy."""

    html = INDEX_PATH.read_text(encoding="utf-8")
    script = APP_JS_PATH.read_text(encoding="utf-8")

    assert "['unknown', 'cooldown'].includes(s.status)" in script
    assert "s.status === 'healthy'" in script
    assert "manual_review" in script
    assert '<option value="unknown">' in html
    assert '<option value="manual_review">' in html


def test_dashboard_leads_renders_birth_date_and_facebook_profile_link() -> None:
    """Break caught: Leads Explorer missing birth date column or clickable FB profile links."""

    html = INDEX_PATH.read_text(encoding="utf-8")
    script = APP_JS_PATH.read_text(encoding="utf-8")

    assert "<th>Ngày Sinh</th>" in html
    assert "u.birth_date" in script
    assert "facebook.com/" in script
    assert 'target="_blank"' in script
