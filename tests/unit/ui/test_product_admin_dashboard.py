from pathlib import Path
import re


ROOT = Path(__file__).parents[3]
INDEX = (ROOT / "src/fb_ui/index.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "src/fb_ui/js/app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "src/fb_ui/css/styles.css").read_text(encoding="utf-8")


def test_admin_workspace_has_explicit_duration_and_entitlement_controls() -> None:
    for control in (
        "license-duration-preset",
        "license-duration-unit",
        "license-duration-value",
        "monthly-contact-limit",
        "max-devices",
        "allow-group-crawl",
        "allow-comment-crawl",
    ):
        assert f'id="{control}"' in INDEX

    for surface in (
        "view-product-admin",
        "form-product-login",
        "table-admin-license-keys-body",
        "table-admin-accounts-body",
        "modal-generated-license",
        "modal-admin-subscriptions",
    ):
        assert f'id="{surface}"' in INDEX


def test_admin_auth_uses_cookie_transport_csrf_and_device_binding() -> None:
    assert "/api/v1/auth/login" in SCRIPT
    assert "transport: 'web'" in SCRIPT
    assert "credentials: 'include'" in SCRIPT
    assert "X-Installation-ID" in SCRIPT
    assert "lead_finder_csrf" in SCRIPT


def test_generated_key_is_text_only_and_cleared_on_close_or_navigation() -> None:
    assert "generatedKeyOutput.textContent = plaintext" in SCRIPT
    assert "generatedKeyOutput.textContent = ''" in SCRIPT
    assert "clearGeneratedLicenseKey()" in SCRIPT


def test_admin_api_data_is_rendered_with_dom_text_nodes_not_html_templates() -> None:
    match = re.search(
        r"renderAdminLicenseRows\(items\)\s*\{(?P<body>.*?)\n  \}",
        SCRIPT,
        re.DOTALL,
    )
    assert match is not None
    body = match.group("body")
    assert "createElement" in body
    assert "innerHTML" not in body
    assert "appendAdminTextCell" in body
    assert "cell.textContent = String(value ?? '—')" in SCRIPT


def test_admin_workspace_wires_license_account_and_subscription_actions() -> None:
    for endpoint in (
        "/api/v1/admin/license-keys",
        "/api/v1/admin/accounts",
        "/suspend",
        "/subscriptions",
        "/start-now",
    ):
        assert endpoint in SCRIPT
    assert "window.confirm" in SCRIPT
    assert "Thời gian còn lại của gói hiện tại sẽ bị mất" in SCRIPT


def test_product_login_panel_respects_hidden_authenticated_state() -> None:
    assert ".product-login-panel[hidden]" in STYLES
    assert "display: none !important" in STYLES


def test_sensitive_admin_actions_reauthenticate_with_password_then_retry_once() -> None:
    assert 'id="modal-admin-reauth"' in INDEX
    assert 'id="form-admin-reauth"' in INDEX
    assert 'id="admin-reauth-password"' in INDEX
    assert "/api/v1/auth/reauthenticate" in SCRIPT
    assert "pendingAdminAction" in SCRIPT
    assert "passwordInput.value = ''" in SCRIPT


def test_admin_workspace_exposes_device_audit_and_cursor_navigation() -> None:
    for element_id in (
        "modal-admin-devices",
        "table-admin-audit-events-body",
        "btn-admin-licenses-next",
        "btn-admin-licenses-previous",
        "btn-admin-accounts-next",
        "btn-admin-accounts-previous",
        "btn-admin-audit-next",
        "btn-admin-audit-previous",
    ):
        assert f'id="{element_id}"' in INDEX
    assert "/devices" in SCRIPT
    assert "/api/v1/admin/audit-events" in SCRIPT
    assert "next_cursor" in SCRIPT
