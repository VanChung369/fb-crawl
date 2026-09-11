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


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Verify email · Lead Finder</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fc;color:#18243b;font:16px/1.6 system-ui,sans-serif;display:grid;min-height:100vh;place-items:center;padding:24px}
main{background:white;border:1px solid #e2e8f0;border-radius:20px;padding:36px;max-width:480px;width:100%;box-shadow:0 12px 40px #18243b0a}
.brand{color:#2563eb;font-weight:700;letter-spacing:.04em}h1{font-size:28px;line-height:1.25;margin:20px 0 12px}p{color:#52617a}label{display:block;font-weight:600;margin-top:20px;font-size:14px}
input,button{font:inherit;width:100%;padding:12px;border-radius:9px;margin-top:6px}input{border:1px solid #aab7cc;background:#fff}button{background:#2563eb;color:white;border:0;cursor:pointer;font-weight:600}button:disabled{opacity:.6;cursor:wait}[hidden]{display:none}
.otp-box{font-family:monospace;font-size:24px;font-weight:700;letter-spacing:.3em;text-align:center}
.btn-secondary{background:transparent;color:#2563eb;border:1px solid #cbd5e1;margin-top:12px}
.success-msg{color:#16a34a;font-weight:600}
.error-msg{color:#dc2626;font-size:14px;margin-top:8px}
</style></head><body><main><div class="brand">Lead Finder</div>
<div role="status" aria-live="polite"><h1 id="title">Verify your email</h1><p id="message">Enter your 6-digit verification code below to confirm your account.</p></div>

<form id="verify-form">
  <label for="verify-email">Account email</label>
  <input id="verify-email" name="email" type="email" autocomplete="email" required maxlength="320" placeholder="your@email.com">
  <label for="verify-code">6-digit verification code</label>
  <input id="verify-code" name="code" class="otp-box" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required placeholder="••••••">
  <p id="verify-error" class="error-msg" hidden></p>
  <button id="verify-btn" type="submit">Verify email</button>
</form>

<div id="recovery" style="margin-top:16px"><button id="resend" type="button" class="btn-secondary">Resend verification code</button><p id="resend-status" role="status" aria-live="polite" style="font-size:14px;margin-top:8px"></p></div>
<noscript><p>Enable JavaScript to verify your email address.</p></noscript>
</main><script>
(() => {
  let token = new URLSearchParams(window.location.search).get('token');
  let code = new URLSearchParams(window.location.search).get('code');
  const emailHint = new URLSearchParams(window.location.search).get('email');
  window.history.replaceState(null, '', window.location.pathname);
  const title = document.getElementById('title');
  const message = document.getElementById('message');
  const verifyForm = document.getElementById('verify-form');
  const emailInput = document.getElementById('verify-email');
  const codeInput = document.getElementById('verify-code');
  const verifyBtn = document.getElementById('verify-btn');
  const verifyError = document.getElementById('verify-error');
  const resendBtn = document.getElementById('resend');
  const resendStatus = document.getElementById('resend-status');

  if (emailHint) emailInput.value = emailHint;
  if (code) codeInput.value = code;

  codeInput.addEventListener('input', (e) => {
    e.target.value = e.target.value.replace(/\D/g, '').slice(0, 6);
    verifyError.hidden = true;
  });

  async function post(path, body) {
    return fetch(path, {method: 'POST', credentials: 'omit', referrerPolicy: 'no-referrer',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
      signal: AbortSignal.timeout(20000)});
  }

  resendBtn.addEventListener('click', async () => {
    const emailVal = emailInput.value.trim();
    if (!emailVal) {
      resendStatus.textContent = 'Please enter your account email above first.';
      emailInput.focus();
      return;
    }
    resendBtn.disabled = true;
    resendStatus.textContent = 'Requesting a new verification code…';
    try {
      const response = await post('/api/v1/auth/resend-verification', {email: emailVal});
      resendStatus.textContent = response.ok
        ? 'A new 6-digit code has been sent. Check your inbox and spam folder.'
        : response.status === 429 ? 'Too many requests. Please wait before trying again.'
        : 'Could not send verification code. Check your email address and try again.';
    } catch (_) { resendStatus.textContent = 'Connection failed. Please try again.'; }
    finally { resendBtn.disabled = false; }
  });

  verifyForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const emailVal = emailInput.value.trim();
    const codeVal = codeInput.value.trim();
    if (codeVal.length !== 6) {
      verifyError.textContent = 'Please enter a 6-digit verification code.';
      verifyError.hidden = false;
      return;
    }
    verifyBtn.disabled = true;
    verifyError.hidden = true;
    try {
      const response = await post('/api/v1/auth/verify-email', {email: emailVal, code: codeVal});
      if (response.ok) {
        title.textContent = 'Email verified';
        message.innerHTML = '<span class="success-msg">✓ Your email address is confirmed!</span><br>Return to the Lead Finder extension and sign in.';
        verifyForm.hidden = true;
        document.getElementById('recovery').hidden = true;
      } else {
        const data = await response.json().catch(() => ({}));
        verifyError.textContent = response.status === 429 ? 'Too many attempts. Please wait before trying again.'
          : (data.message || 'Verification code is invalid or expired. Check your code or request a new one.');
        verifyError.hidden = false;
      }
    } catch (_) {
      verifyError.textContent = 'Could not connect to the server. Please check your connection and try again.';
      verifyError.hidden = false;
    } finally {
      verifyBtn.disabled = false;
    }
  });

  if (token || (code && emailHint)) {
    title.textContent = 'Verifying your email…';
    message.textContent = 'Please wait while we confirm your email address.';
    post('/api/v1/auth/verify-email', {token: token || code, code: code, email: emailHint})
      .then(async (response) => {
        if (response.ok) {
          title.textContent = 'Email verified';
          message.innerHTML = '<span class="success-msg">✓ Your email address is confirmed!</span><br>Return to the Lead Finder extension and sign in.';
          verifyForm.hidden = true;
          document.getElementById('recovery').hidden = true;
        } else {
          title.textContent = 'Verify your email';
          message.textContent = 'The link code was expired or invalid. Enter your code below or request a new one.';
        }
      })
      .catch(() => {
        title.textContent = 'Verify your email';
        message.textContent = 'Could not auto-verify. Enter your code below:';
      });
  }
})();
</script></body></html>"""
