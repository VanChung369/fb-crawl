# Lead Finder Auth and Accounts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add verified email/password accounts, rotating device-bound sessions, account/device APIs, route-specific authentication, and one-time administrator bootstrap to `fb-crawl`.

**Architecture:** Add focused `accounts` and `auth` packages plus a PostgreSQL repository built from the same `DATABASE_URL` as the existing API. Product routers use bearer/cookie authentication while current crawler routes keep `X-API-Key`; `ProductServices` is injected into `create_app` so existing tests and dev mocks remain composable.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic 2, PostgreSQL/psycopg 3, Argon2id (`argon2-cffi`), PyJWT, pytest.

**Spec:** `docs/superpowers/specs/2026-08-30-lead-finder-phase-1-design.md`

## Global Constraints

- Access JWT lifetime is exactly 15 minutes and JWT claims never authorize role, plan, quota, or account status.
- Email verification expires after 24 hours; password reset expires after one hour.
- Refresh tokens are opaque, hash-only, device-bound, rotated on use, and reuse revokes the family.
- Passwords and plaintext tokens never enter logs, CLI arguments, or database columns.
- Existing crawler API routes remain protected by `X-API-Key`; no master key enters the extension.
- The first admin is created only by a one-time, password-prompting CLI command.
- Existing migrations `001` through `003` remain immutable.

---

### Task 1: Product dependencies, settings, and account migration

**Files:**
- Modify: `pyproject.toml`
- Create: `src/fb_crawl/auth/config.py`
- Create: `src/fb_data_pipeline/migrations/004_product_accounts.sql`
- Modify: `tests/unit/data_pipeline/test_migrations.py`
- Create: `tests/unit/auth/test_config.py`

**Interfaces:**
- Produces: `AuthSettings(jwt_secret, token_hmac_secret, public_base_url, product_timezone, access_ttl_seconds=900)` and `load_auth_settings(env: Mapping[str, str]) -> AuthSettings`.
- Produces database tables `accounts`, `account_tokens`, `devices`, `auth_sessions`, and `rate_limit_buckets`.

- [x] **Step 1: Write migration and configuration tests**

```python
def test_product_account_migration_is_fourth() -> None:
    migrations = load_migrations()
    assert migrations[-1].version == 4
    assert "CREATE TABLE accounts" in migrations[-1].sql
    assert "CREATE TABLE auth_sessions" in migrations[-1].sql

def test_auth_settings_reject_short_secrets() -> None:
    with pytest.raises(ConfigurationError, match="JWT secret"):
        load_auth_settings({"LEAD_FINDER_JWT_SECRET": "short"})
```

- [x] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest tests/unit/data_pipeline/test_migrations.py tests/unit/auth/test_config.py -q`

Expected: FAIL because migration `004` and `fb_crawl.auth.config` do not exist.

- [x] **Step 3: Add exact runtime dependencies and settings**

Add `argon2-cffi>=23.1,<26`, `PyJWT>=2.9,<3`, and `email-validator>=2.2,<3` to both the `api` and `dev` extras. Implement frozen settings that require 32+ nonblank characters for both secrets, an `https://` public base URL outside dev, and a valid `ZoneInfo` timezone defaulting to `Asia/Ho_Chi_Minh`.

Create migration `004` with the columns and constraints from the spec, including normalized-email uniqueness, `(account_id, installation_id)` uniqueness, token-purpose checks, session/token expiry indexes, and no cascading foreign key from account data into shared Facebook identity tables.

```python
@dataclass(frozen=True, slots=True)
class AuthSettings:
    jwt_secret: str = field(repr=False)
    token_hmac_secret: str = field(repr=False)
    public_base_url: str
    product_timezone: ZoneInfo
    access_ttl_seconds: int = 900
```

- [x] **Step 4: Re-run focused tests**

Run: `python -m pytest tests/unit/data_pipeline/test_migrations.py tests/unit/auth/test_config.py -q`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add pyproject.toml src/fb_crawl/auth src/fb_data_pipeline/migrations/004_product_accounts.sql tests/unit/auth tests/unit/data_pipeline/test_migrations.py
git commit -m "feat: add product account foundation"
```

### Task 2: Password, token, account, and session domain primitives

**Files:**
- Create: `src/fb_crawl/accounts/__init__.py`
- Create: `src/fb_crawl/accounts/models.py`
- Create: `src/fb_crawl/auth/__init__.py`
- Create: `src/fb_crawl/auth/passwords.py`
- Create: `src/fb_crawl/auth/tokens.py`
- Create: `tests/unit/auth/test_passwords.py`
- Create: `tests/unit/auth/test_tokens.py`
- Create: `tests/unit/accounts/test_models.py`

**Interfaces:**
- Produces: `Account`, `Device`, `AuthSession`, `AccountRole`, and `AccountStatus` frozen dataclasses/enums.
- Produces: `PasswordHasher.hash(password: str) -> str` and `.verify(encoded: str, password: str) -> bool`.
- Produces: `TokenService.issue_access(account_id: int, session_id: UUID, device_id: int, now: datetime) -> str`, `.decode_access(token: str, now: datetime) -> AccessClaims`, `.new_opaque_token() -> str`, and `.digest_opaque(token: str) -> str`.

- [ ] **Step 1: Write failing security primitive tests**

```python
def test_access_claims_contain_identity_not_entitlements() -> None:
    token = service.issue_access(7, SESSION_ID, 9, NOW)
    claims = service.decode_access(token, NOW)
    assert (claims.account_id, claims.session_id, claims.device_id) == (7, SESSION_ID, 9)
    assert not hasattr(claims, "role")
    assert not hasattr(claims, "quota")

def test_password_hash_never_contains_plaintext() -> None:
    encoded = hasher.hash("correct horse battery staple")
    assert "correct horse" not in encoded
    assert hasher.verify(encoded, "correct horse battery staple") is True
```

- [ ] **Step 2: Verify red state**

Run: `python -m pytest tests/unit/auth/test_passwords.py tests/unit/auth/test_tokens.py tests/unit/accounts/test_models.py -q`

Expected: FAIL on missing modules.

- [ ] **Step 3: Implement minimal primitives**

Use `argon2.PasswordHasher` with explicit time/memory/parallelism configuration. JWT includes `sub`, `sid`, `did`, `iat`, `exp`, and a fixed issuer/audience. Opaque tokens use `secrets.token_urlsafe(32)` and HMAC-SHA-256 with the configured token secret; compare digests with `hmac.compare_digest`.

```python
class TokenService:
    def issue_access(self, account_id: int, session_id: UUID, device_id: int, now: datetime) -> str: ...
    def decode_access(self, token: str, now: datetime) -> AccessClaims: ...
    def new_opaque_token(self) -> str: return secrets.token_urlsafe(32)
```

- [ ] **Step 4: Verify green state**

Run: `python -m pytest tests/unit/auth/test_passwords.py tests/unit/auth/test_tokens.py tests/unit/accounts/test_models.py -q`

Expected: PASS, including expired/wrong-audience JWT and malformed-token cases.

- [ ] **Step 5: Commit**

```bash
git add src/fb_crawl/accounts src/fb_crawl/auth tests/unit/accounts tests/unit/auth
git commit -m "feat: add account authentication primitives"
```

### Task 3: PostgreSQL account and rotating-session repository

**Files:**
- Create: `src/fb_crawl/accounts/repository.py`
- Create: `src/fb_crawl/accounts/postgres.py`
- Create: `tests/unit/accounts/test_postgres.py`
- Create: `tests/integration/data_pipeline/test_account_repository.py`

**Interfaces:**
- Produces: `AccountRepository` protocol and `PostgresAccountRepository`.
- Required operations: `create_account`, `find_account_by_email`, `verify_email_token`, `create_device`, `list_devices`, `revoke_device`, `create_session`, `rotate_session`, `revoke_session`, `revoke_account_sessions`, `request_account_deletion`, `purge_due_deleted_accounts`, and `record_rate_limit_hit`.
- `rotate_session(old_digest, new_digest, now, expires_at) -> AuthSession` must revoke the whole family when `old_digest` already has `rotated_from_id`/replacement state.

- [ ] **Step 1: Write repository contract tests**

```python
def test_rotate_refresh_token_is_single_use(repository) -> None:
    first = repository.create_session(ACCOUNT_ID, DEVICE_ID, "old", EXPIRES)
    repository.rotate_session("old", "new", NOW, EXPIRES)
    with pytest.raises(SessionReuseDetected):
        repository.rotate_session("old", "another", NOW, EXPIRES)
    assert repository.family_is_revoked(first.id)
```

- [ ] **Step 2: Run unit and PostgreSQL tests**

Run: `python -m pytest tests/unit/accounts/test_postgres.py tests/integration/data_pipeline/test_account_repository.py -q`

Expected: FAIL because repository types are missing; integration tests skip only when `TEST_DATABASE_URL` is absent.

- [ ] **Step 3: Implement repository transactions**

Use one short psycopg transaction per operation, `SELECT ... FOR UPDATE` for token rotation/device revocation, database-generated timestamps where possible, safe `DatabaseError` mapping, and injectable connection factories matching existing repository tests. Never return password hashes or token hashes from list APIs.

```python
class AccountRepository(Protocol):
    def create_account(self, normalized_email: str, display_email: str, password_hash: str) -> Account: ...
    def rotate_session(self, old_digest: str, new_digest: str, now: datetime, expires_at: datetime) -> AuthSession: ...
    def revoke_device(self, account_id: int, device_id: int, now: datetime) -> None: ...
```

- [ ] **Step 4: Re-run repository tests**

Run: `python -m pytest tests/unit/accounts/test_postgres.py tests/integration/data_pipeline/test_account_repository.py -q`

Expected: PASS or documented PostgreSQL skips.

- [ ] **Step 5: Commit**

```bash
git add src/fb_crawl/accounts tests/unit/accounts/test_postgres.py tests/integration/data_pipeline/test_account_repository.py
git commit -m "feat: persist accounts devices and sessions"
```

### Task 4: Account authentication service and email delivery

**Files:**
- Create: `src/fb_crawl/auth/email.py`
- Create: `src/fb_crawl/auth/rate_limit.py`
- Create: `src/fb_crawl/auth/service.py`
- Create: `tests/unit/auth/test_service.py`
- Create: `tests/unit/auth/test_email.py`

**Interfaces:**
- Produces: `EmailDeliveryPort.send_verification(email: str, url: str) -> None` and `.send_password_reset(...)` plus `SmtpEmailDelivery`.
- Produces: `RateLimitService.check(action: str, account_key: str | None, device_key: str | None, ip_address: str, now: datetime) -> None` using PostgreSQL buckets and HMACed rotating IP keys.
- Produces: `AccountAuthService.register`, `verify_email`, `resend_verification`, `login`, `refresh`, `forgot_password`, `reset_password`, and `logout`.
- Returns `AuthTokens(access_token, refresh_token, access_expires_at)` only from login/refresh.

- [ ] **Step 1: Write service behavior tests with fake repository/email**

```python
def test_login_requires_verified_email(service, repo) -> None:
    account = repo.add_account(verified=False)
    with pytest.raises(EmailVerificationRequired):
        service.login(account.display_email, VALID_PASSWORD, INSTALLATION_ID, "Chrome", NOW)

def test_password_reset_revokes_all_sessions(service, repo) -> None:
    token = service.forgot_password(EMAIL, NOW)
    service.reset_password(token, "new secure password", NOW)
    assert repo.active_sessions(ACCOUNT_ID) == ()

def test_login_rate_limit_is_shared_by_ip_and_email(service, limiter) -> None:
    limiter.exhaust("login", EMAIL, "203.0.113.4")
    with pytest.raises(AuthRateLimited):
        service.login(EMAIL, VALID_PASSWORD, INSTALLATION_ID, "Chrome", NOW, ip_address="203.0.113.4")
```

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/unit/auth/test_service.py tests/unit/auth/test_email.py -q`

Expected: FAIL on missing service.

- [ ] **Step 3: Implement service orchestration**

Normalize email before repository calls, enforce PostgreSQL-backed action/email/device/HMACed-IP buckets before expensive password/email work, use generic external responses for unknown emails, enforce password length 12–128, create single-use verification/reset tokens, bind sessions to installation UUID devices, rotate refresh tokens, and sanitize SMTP failures as `email_delivery_failed` without leaking credentials.

```python
class AccountAuthService:
    def register(self, email: str, password: str, now: datetime) -> RegistrationResult: ...
    def login(self, email: str, password: str, installation_id: UUID, device_name: str, now: datetime, ip_address: str) -> AuthTokens: ...
    def refresh(self, refresh_token: str, installation_id: UUID, now: datetime) -> AuthTokens: ...
```

- [ ] **Step 4: Verify service tests**

Run: `python -m pytest tests/unit/auth/test_service.py tests/unit/auth/test_email.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fb_crawl/auth tests/unit/auth
git commit -m "feat: add verified account auth service"
```

### Task 5: Product bearer/cookie authentication and account APIs

**Files:**
- Create: `src/fb_crawl/composition/product.py`
- Create: `src/fb_crawl/api/product_schemas.py`
- Create: `src/fb_crawl/api/routes/auth.py`
- Create: `src/fb_crawl/api/routes/product_account.py`
- Modify: `src/fb_crawl/api/dependencies.py`
- Modify: `src/fb_crawl/api/app.py`
- Modify: `src/fb_crawl/cli/api.py`
- Create: `tests/unit/api/test_product_auth_routes.py`
- Create: `tests/unit/api/test_product_account_routes.py`
- Modify: `tests/unit/api/test_auth.py`
- Modify: `tests/integration/test_api_cli.py`

**Interfaces:**
- Produces: `ProductServices(auth_service, account_repository, token_service)` and `CurrentAccount(account, device, session)` dependency.
- Public routes match the spec; bearer routes include `/account/me`, `/account`, `/devices`, and `/auth/logout`.

- [ ] **Step 1: Add failing route-policy tests**

```python
def test_product_login_does_not_require_internal_api_key(client) -> None:
    response = client.post("/api/v1/auth/login", json=LOGIN_BODY)
    assert response.status_code != 401 or response.json()["code"] != "api_unauthorized"

def test_existing_jobs_still_require_internal_api_key(client) -> None:
    assert client.get("/api/v1/jobs").status_code == 401

def test_cookie_authenticated_state_change_requires_csrf(client, web_session_cookie) -> None:
    response = client.delete("/api/v1/account", cookies=web_session_cookie)
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_validation_failed"
```

- [ ] **Step 2: Run route tests and see the global middleware failure**

Run: `python -m pytest tests/unit/api/test_auth.py tests/unit/api/test_product_auth_routes.py tests/unit/api/test_product_account_routes.py -q`

Expected: FAIL because the current middleware requires `X-API-Key` for every `/api/v1` route.

- [ ] **Step 3: Implement explicit policy routing**

Replace the broad middleware condition with exact internal-prefix classification. Product auth routers use injected services; bearer dependency decodes JWT then reloads session, device, account status, and allowed-device state. Web login/refresh may set secure refresh cookies and must pass an Origin-bound double-submit CSRF token for state changes, while extension responses return the opaque refresh token in the JSON contract. Add exact CORS headers `Authorization`, `X-Installation-ID`, `X-CSRF-Token`, `Content-Type`, and existing internal headers.

```python
PUBLIC_PRODUCT_PREFIXES = ("/api/v1/auth/",)
INTERNAL_API_PREFIXES = (
    "/api/v1/jobs", "/api/v1/account/default", "/api/v1/users",
    "/api/v1/sessions", "/api/v1/proxies", "/api/v1/settings",
    "/api/v1/stats", "/api/v1/export",
)

def current_account(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> CurrentAccount: ...
```

- [ ] **Step 4: Run API and CLI regression tests**

Run: `python -m pytest tests/unit/api tests/integration/test_api_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fb_crawl/api src/fb_crawl/cli/api.py src/fb_crawl/composition/product.py tests/unit/api tests/integration/test_api_cli.py
git commit -m "feat: expose product account authentication API"
```

### Task 6: One-time administrator bootstrap and final auth verification

**Files:**
- Create: `src/fb_crawl/cli/admin.py`
- Modify: `src/fb_crawl/cli/app.py`
- Create: `tests/unit/cli/test_admin_parser.py`
- Create: `tests/integration/test_admin_cli.py`
- Modify: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Produces CLI `fb-crawl admin bootstrap --email admin@example.com`; password is read twice with `getpass.getpass` and never accepted as an option.
- Produces CLI `fb-crawl admin purge-deleted-accounts` for scheduled hard deletion after the configured recovery window.

- [ ] **Step 1: Write parser and command safety tests**

```python
def test_admin_bootstrap_has_no_password_argument() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["admin", "bootstrap", "--email", EMAIL, "--password", "secret"])

def test_second_bootstrap_is_rejected(repository, command) -> None:
    assert command.run(EMAIL, "strong password") == 0
    with pytest.raises(AdminAlreadyExists):
        command.run("other@example.com", "another strong password")

def test_purge_deletes_only_accounts_past_recovery_window(command, repository) -> None:
    assert command.purge_deleted(now=NOW) == 1
    assert repository.active_account(RECENTLY_DELETED_ID) is not None
```

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/unit/cli/test_admin_parser.py tests/integration/test_admin_cli.py -q`

Expected: FAIL because admin mode is absent.

- [ ] **Step 3: Implement bootstrap and operator documentation**

Add `admin` dispatch to `cli/app.py`, require `DATABASE_URL`, apply migrations, refuse when any admin exists, securely prompt/confirm the password, create a verified active admin, and print only the new account ID/email. Add the idempotent purge command using `LEAD_FINDER_ACCOUNT_RECOVERY_DAYS`; it revokes access immediately at delete request and later removes only account-owned rows, never shared Facebook identity/evidence. Document required JWT/HMAC/base URL/SMTP/product-timezone variables without sample production secrets.

```python
password = getpass.getpass("Admin password: ")
confirmation = getpass.getpass("Confirm admin password: ")
if password != confirmation:
    raise ValidationError("Passwords do not match.")
admin = repository.bootstrap_admin(email, hasher.hash(password), now)
```

- [ ] **Step 4: Run the complete backend verification**

Run: `python -m pytest -q`

Expected: all tests PASS or only existing explicitly configured PostgreSQL tests SKIP.

Run: `python -m build`

Expected: wheel and sdist build successfully and contain migration `004`.

- [ ] **Step 5: Commit**

```bash
git add src/fb_crawl/cli .env.example README.md tests/unit/cli/test_admin_parser.py tests/integration/test_admin_cli.py
git commit -m "feat: bootstrap lead finder administrators"
```
