"""Public password reset form; the reset token stays in page memory."""

PASSWORD_RESET_PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Reset password · Lead Finder</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fc;color:#18243b;font:15px/1.6 system-ui,sans-serif;display:grid;min-height:100vh;place-items:center;padding:20px}
main{width:100%;max-width:420px;background:#fff;padding:28px;border:1px solid #dce3ef;border-radius:14px;box-shadow:0 6px 24px #18243b0c}
h1{font-size:24px;line-height:1.3;margin:0 0 10px}p{margin:0 0 18px;color:#526179}label{display:block;margin:14px 0 6px;font-weight:600}
input,button{width:100%;font:inherit;border-radius:8px;min-height:44px}input{border:1px solid #c6d0e2;padding:10px 12px;min-width:0}
input:focus{outline:2px solid #bad5ff;border-color:#2563eb}button{margin-top:20px;padding:10px 14px;border:0;background:#2563eb;color:#fff;font-weight:600;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}.error{color:#b42318;margin-top:12px}.success{color:#157347}hr{border:0;border-top:1px solid #e0e6ef;margin:24px 0}h2{font-size:17px;margin:0 0 10px}[hidden]{display:none!important}
</style></head><body><main>
<h1 id="title">Reset your password</h1>
<p id="message">Choose a new password for your Lead Finder account.</p>
<form id="reset-form">
  <label for="new-password">New password</label>
  <input id="new-password" name="new_password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required aria-describedby="password-hint">
  <p id="password-hint">Use at least 12 characters.</p>
  <label for="confirm-password">Confirm password</label>
  <input id="confirm-password" name="confirm_password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required>
  <p id="reset-error" class="error" role="alert" hidden></p>
  <button id="reset-button" type="submit">Reset password</button>
</form>
<section id="recovery" hidden>
  <hr><h2>Request a new reset link</h2>
  <form id="recovery-form">
    <label for="account-email">Account email</label>
    <input id="account-email" name="email" type="email" autocomplete="email" maxlength="320" required>
    <p id="recovery-status" role="status" hidden></p>
    <p id="recovery-error" class="error" role="alert" hidden></p>
    <button id="recovery-button" type="submit">Send reset link</button>
  </form>
</section>
<noscript><p>Enable JavaScript to reset your password.</p></noscript>
</main><script>
(() => {
  let token = new URLSearchParams(location.search).get('token') || '';
  // Remove the secret from browser history before the user interacts with the form.
  history.replaceState(null, '', location.pathname);
  const form = document.getElementById('reset-form');
  const password = document.getElementById('new-password');
  const confirmation = document.getElementById('confirm-password');
  const button = document.getElementById('reset-button');
  const error = document.getElementById('reset-error');
  const message = document.getElementById('message');
  const recovery = document.getElementById('recovery');
  let busy = false;
  const showError = text => {error.textContent = text; error.hidden = false;};
  const invalidLink = () => {
    token = '';
    form.hidden = true;
    message.textContent = 'This reset link is invalid or expired. Request a new link below.';
    recovery.hidden = false;
  };
  if (!token || token.length > 4096) invalidLink();
  const post = (path, body) => fetch(path, {
    method:'POST', credentials:'omit', headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body), signal:AbortSignal.timeout(15000),
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !token) return;
    error.hidden = true;
    if (password.value !== confirmation.value) {showError('Passwords do not match.'); return;}
    if (password.value.length < 12 || password.value.length > 128) {showError('Password must be 12–128 characters.'); return;}
    busy = true; button.disabled = true; button.textContent = 'Resetting…';
    try {
      const response = await post('/api/v1/auth/reset-password', {token, new_password:password.value});
      if (response.ok) {
        token = ''; form.hidden = true; recovery.hidden = true;
        document.getElementById('title').textContent = 'Password updated';
        message.textContent = 'Return to the Lead Finder extension and sign in with your new password.';
        message.className = 'success'; message.setAttribute('role', 'status');
      } else if (response.status === 400) invalidLink();
      else showError(response.status === 429 ? 'Too many attempts. Please wait before trying again.' : 'Could not reset your password. Please try again.');
    } catch {
      showError('Could not connect to the server. Check your connection and try again.');
    } finally {
      password.value = ''; confirmation.value = '';
      busy = false; button.disabled = false; button.textContent = 'Reset password';
    }
  });
  const recoveryForm = document.getElementById('recovery-form');
  const recoveryButton = document.getElementById('recovery-button');
  const recoveryError = document.getElementById('recovery-error');
  const recoveryStatus = document.getElementById('recovery-status');
  let sending = false;
  recoveryForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (sending) return;
    sending = true; recoveryButton.disabled = true; recoveryButton.textContent = 'Sending…';
    recoveryError.hidden = true; recoveryStatus.hidden = true;
    try {
      const response = await post('/api/v1/auth/forgot-password', {email:document.getElementById('account-email').value.trim()});
      if (response.ok) {
        recoveryStatus.textContent = 'If an account exists for this email, a reset link has been sent. Check your inbox and spam folder.';
        recoveryStatus.hidden = false; recoveryButton.hidden = true;
      } else {
        recoveryError.textContent = response.status === 429 ? 'Too many attempts. Please wait before trying again.' : 'Could not send reset email. Please try again.';
        recoveryError.hidden = false;
      }
    } catch {
      recoveryError.textContent = 'Could not connect to the server. Check your connection and try again.';
      recoveryError.hidden = false;
    } finally {
      sending = false; recoveryButton.disabled = false; recoveryButton.textContent = 'Send reset link';
    }
  });
})();
</script></body></html>'''
