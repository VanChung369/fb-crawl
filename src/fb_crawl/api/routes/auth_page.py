"""Public, self-contained account email verification page."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


def create_auth_page_router() -> APIRouter:
    router = APIRouter()

    @router.get("/verify-email", response_class=HTMLResponse, include_in_schema=False)
    def verify_email_page() -> HTMLResponse:
        return HTMLResponse(PAGE, headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        })

    return router


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Verify email · Lead Finder</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fc;color:#18243b;font:16px/1.6 system-ui,sans-serif;display:grid;min-height:100vh;place-items:center;padding:24px}
main{background:white;border:1px solid #e2e8f0;border-radius:20px;padding:36px;max-width:480px;width:100%;box-shadow:0 12px 40px #18243b0a}
.brand{color:#2563eb;font-weight:700;letter-spacing:.04em}h1{font-size:28px;line-height:1.25;margin:20px 0 12px}p{color:#52617a}label{display:block;font-weight:600;margin-top:24px}
input,button{font:inherit;width:100%;padding:12px;border-radius:9px;margin-top:8px}input{border:1px solid #aab7cc}button{background:#2563eb;color:white;border:0;cursor:pointer;font-weight:600}button:disabled{opacity:.6;cursor:wait}[hidden]{display:none}
</style></head><body><main><div class="brand">Lead Finder</div>
<div role="status" aria-live="polite"><h1 id="title">Verifying your email…</h1><p id="message">Please wait while we confirm your email address.</p></div>
<form id="recovery" hidden><label for="email">Account email</label><input id="email" name="email" type="email" autocomplete="email" required maxlength="320"><button id="resend" type="submit">Send a new verification email</button><p id="resend-status" role="status" aria-live="polite"></p></form>
<noscript><p>Enable JavaScript, then reopen the verification link from your email.</p></noscript>
</main><script>
(() => {
  let token = new URLSearchParams(window.location.search).get('token');
  const emailHint = new URLSearchParams(window.location.search).get('email');
  window.history.replaceState(null, '', window.location.pathname);
  const title = document.getElementById('title');
  const message = document.getElementById('message');
  const recovery = document.getElementById('recovery');
  const email = document.getElementById('email');
  if (emailHint) email.value = emailHint;
  function failed(text) {
    title.textContent = 'Unable to verify email';
    message.textContent = text;
    recovery.hidden = false;
  }
  async function post(path, body) {
    return fetch(path, {method: 'POST', credentials: 'omit', referrerPolicy: 'no-referrer',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
      signal: AbortSignal.timeout(20000)});
  }
  recovery.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = document.getElementById('resend');
    const status = document.getElementById('resend-status');
    button.disabled = true;
    status.textContent = 'Requesting a new email…';
    try {
      const response = await post('/api/v1/auth/resend-verification', {email: email.value.trim()});
      status.textContent = response.ok
        ? 'If this account needs verification, a new email is on its way. Check your inbox and spam folder.'
        : response.status === 429 ? 'Too many requests. Please wait before trying again.'
        : 'Could not request an email. Check your email address and try again.';
    } catch (_) { status.textContent = 'Connection failed. Please try again.'; }
    finally { button.disabled = false; }
  });
  async function verify() {
    if (!token) { failed('This link is missing its verification code. Enter your account email to request a new link.'); return; }
    try {
      const pending = post('/api/v1/auth/verify-email', {token});
      token = null;
      const response = await pending;
      if (response.ok) {
        title.textContent = 'Email verified';
        message.textContent = 'Your email address is confirmed. Return to the Lead Finder extension and sign in.';
      } else {
        failed(response.status === 429 ? 'Too many attempts. Please wait before requesting a new link.'
          : response.status >= 500 || response.status === 404 ? 'Verification is temporarily unavailable. Please try again later or request a new link.'
          : 'This link is invalid, expired, or already used. If you have already verified your email, sign in to the extension. Otherwise, request a new link below.');
      }
    } catch (_) { failed('Could not connect to the server. Reopen the email link to retry, or request a new link below.'); }
  }
  verify();
})();
</script></body></html>"""
