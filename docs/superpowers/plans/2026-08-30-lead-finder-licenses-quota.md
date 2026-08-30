# Lead Finder Licenses, Entitlements, and Quota Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let administrators create commercial license keys and let verified accounts redeem them into time-bounded entitlements with deterministic device and monthly-contact limits.

**Architecture:** Add license/entitlement domain services and a transactional PostgreSQL repository on top of the auth foundation. Entitlements are snapshots selected at request time; quota is a per-account calendar-month counter plus a unique reveal ledger that the contact plan will consume.

**Tech Stack:** Python 3.12+, FastAPI, PostgreSQL/psycopg 3, HMAC-SHA-256, Pydantic 2, pytest, existing vanilla dashboard JavaScript.

**Spec:** `docs/superpowers/specs/2026-08-30-lead-finder-phase-1-design.md`

## Global Constraints

- Execute after `2026-08-30-lead-finder-auth-accounts.md` is complete.
- Default entitlement is exactly 100 contacts per product-timezone calendar month, one device, and no group/comment crawl.
- License keys are high-entropy, plaintext-once values stored only as versioned HMAC digests.
- Preset months use calendar-month arithmetic; custom durations use positive whole days.
- One key binds permanently to one account and cannot be reused after account deletion.
- New paid subscriptions queue after current/scheduled paid time unless an audited admin override forfeits the current remainder.
- Stored subscription status is only `valid|revoked`; scheduled/active/expired are derived.
- Existing migrations remain immutable.

---

### Task 1: License, subscription, usage, and reveal migration

**Files:**
- Create: `src/fb_data_pipeline/migrations/005_product_licenses.sql`
- Modify: `tests/unit/data_pipeline/test_migrations.py`
- Create: `tests/integration/data_pipeline/test_license_schema.py`

**Interfaces:**
- Produces tables `plans`, `license_keys`, `account_subscriptions`, `usage_monthly`, `account_contact_reveals`, and `admin_audit_events`.
- Seeds protected plan `default` with limit `100`, `max_devices=1`, and both crawl flags false.

- [x] **Step 1: Write schema contract tests**

```python
def test_license_migration_seeds_default_plan() -> None:
    migration = load_migrations()[-1]
    assert migration.version == 5
    assert "VALUES ('default', 'Default', 100, 1, false, false, true)" in migration.sql
    assert "UNIQUE (account_id, facebook_user_id, period_start)" in migration.sql
```

- [x] **Step 2: Run tests and confirm migration is missing**

Run: `python -m pytest tests/unit/data_pipeline/test_migrations.py tests/integration/data_pipeline/test_license_schema.py -q`

Expected: FAIL on missing version 5; integration test skips only without `TEST_DATABASE_URL`.

- [x] **Step 3: Implement migration**

Use the exact statuses/constraints from the spec. Add lookup indexes for digest, account/time subscription selection, monthly usage, account reveal history, and audit actor/time. `account_contact_reveals` initially contains a nullable `lookup_event_id bigint` without a foreign key; migration `006` will add its FK after creating `lookup_events`.

```sql
CREATE UNIQUE INDEX license_keys_key_digest_idx ON license_keys (key_version, key_digest);
CREATE UNIQUE INDEX account_contact_reveals_period_key
ON account_contact_reveals (account_id, facebook_user_id, period_start);
```

- [x] **Step 4: Re-run schema tests**

Run: `python -m pytest tests/unit/data_pipeline/test_migrations.py tests/integration/data_pipeline/test_license_schema.py -q`

Expected: PASS or configured database skip.

- [x] **Step 5: Commit**

```bash
git add src/fb_data_pipeline/migrations/005_product_licenses.sql tests/unit/data_pipeline/test_migrations.py tests/integration/data_pipeline/test_license_schema.py
git commit -m "feat: add license and quota schema"
```

### Task 2: License and entitlement domain models

**Files:**
- Create: `src/fb_crawl/licenses/__init__.py`
- Create: `src/fb_crawl/licenses/config.py`
- Create: `src/fb_crawl/licenses/models.py`
- Create: `src/fb_crawl/licenses/keys.py`
- Create: `src/fb_crawl/entitlements/__init__.py`
- Create: `src/fb_crawl/entitlements/models.py`
- Create: `src/fb_crawl/entitlements/time.py`
- Create: `tests/unit/licenses/test_keys.py`
- Create: `tests/unit/licenses/test_config.py`
- Create: `tests/unit/licenses/test_models.py`
- Create: `tests/unit/entitlements/test_time.py`

**Interfaces:**
- Produces `LicenseDuration(unit: Literal['day','month'], value: int)`, `LicenseGrant`, `Subscription`, and `Entitlements`.
- Produces `LicenseKeyRing(active_version: int, secrets: Mapping[int, bytes])` loaded from `LEAD_FINDER_LICENSE_HMAC_KEYS` and `LEAD_FINDER_LICENSE_HMAC_ACTIVE_VERSION`; each configured secret decodes to at least 32 bytes.
- Produces `LicenseKeyService.generate() -> GeneratedLicense(plaintext, digest, masked, key_version)` and `.digest(plaintext) -> str`.
- Produces `add_duration(start: datetime, duration: LicenseDuration) -> datetime` and `quota_period(now: datetime, timezone: ZoneInfo) -> date`.

- [x] **Step 1: Write duration/key tests**

```python
def test_calendar_month_clamps_end_of_month() -> None:
    start = datetime(2027, 1, 31, 9, tzinfo=UTC)
    assert add_duration(start, LicenseDuration("month", 1)) == datetime(2027, 2, 28, 9, tzinfo=UTC)

def test_generated_license_plaintext_is_not_digest() -> None:
    generated = keys.generate()
    assert generated.plaintext.startswith("LF-")
    assert generated.plaintext not in generated.digest
    assert keys.digest(generated.plaintext) == generated.digest

def test_license_keyring_requires_active_version() -> None:
    with pytest.raises(ConfigurationError, match="active license HMAC version"):
        load_license_keyring({"LEAD_FINDER_LICENSE_HMAC_KEYS": "1:c2VjcmV0"})
```

- [x] **Step 2: Verify red state**

Run: `python -m pytest tests/unit/licenses tests/unit/entitlements -q`

Expected: FAIL on missing packages.

- [x] **Step 3: Implement immutable validated models**

Generate at least 192 bits of randomness, format only for human transcription, normalize hyphens/case before HMAC, validate duration > 0/device limit >= 1/quota >= 0, and implement calendar-month addition with `calendar.monthrange` without introducing a date library.

```python
def add_duration(start: datetime, duration: LicenseDuration) -> datetime:
    if duration.unit == "day":
        return start + timedelta(days=duration.value)
    month_index = start.month - 1 + duration.value
    year, month0 = divmod(month_index, 12)
    day = min(start.day, monthrange(start.year + year, month0 + 1)[1])
    return start.replace(year=start.year + year, month=month0 + 1, day=day)
```

- [x] **Step 4: Verify green state**

Run: `python -m pytest tests/unit/licenses tests/unit/entitlements -q`

Expected: PASS, including leap year and `Asia/Ho_Chi_Minh` month-boundary cases.

- [x] **Step 5: Commit**

```bash
git add src/fb_crawl/licenses src/fb_crawl/entitlements tests/unit/licenses tests/unit/entitlements
git commit -m "feat: model licenses and entitlements"
```

### Task 3: Transactional license and entitlement repository

**Files:**
- Create: `src/fb_crawl/licenses/repository.py`
- Create: `src/fb_crawl/licenses/postgres.py`
- Create: `tests/unit/licenses/test_postgres.py`
- Create: `tests/integration/data_pipeline/test_license_repository.py`

**Interfaces:**
- Produces `PostgresLicenseRepository.create_key`, `redeem`, `effective_entitlements`, `list_subscriptions`, `revoke_key`, `start_subscription_now`, and `write_audit`.
- `redeem(account_id: int, key_digest: str, now: datetime) -> Subscription` locks the key and account subscription schedule.

- [x] **Step 1: Write concurrency and lifecycle tests**

```python
def test_second_account_cannot_redeem_used_key(repository, key) -> None:
    repository.redeem(ACCOUNT_A, key.digest, NOW)
    with pytest.raises(LicenseAlreadyRedeemed):
        repository.redeem(ACCOUNT_B, key.digest, NOW)

def test_second_key_starts_after_existing_end(repository) -> None:
    first = repository.redeem(ACCOUNT_A, KEY_1, NOW)
    second = repository.redeem(ACCOUNT_A, KEY_2, NOW + timedelta(days=1))
    assert second.starts_at == first.ends_at
```

- [x] **Step 2: Run repository tests**

Run: `python -m pytest tests/unit/licenses/test_postgres.py tests/integration/data_pipeline/test_license_repository.py -q`

Expected: FAIL because repository is missing.

- [x] **Step 3: Implement short transactions and effective selection**

Use `SELECT ... FOR UPDATE` on the license and an account-scoped advisory transaction lock for schedule serialization. Redemption writes the immutable entitlement snapshot and audit event. `effective_entitlements` selects `starts_at <= now < ends_at AND status='valid'`, otherwise selects the protected default plan. Immediate start is allowed only for the earliest scheduled row: end the current row at `now`, recalculate that row from its original duration, shift every later valid row to remain contiguous, and audit all affected IDs.

```python
def redeem(self, account_id: int, key_digest: str, now: datetime) -> Subscription:
    with self._transaction() as cursor:
        self._lock_account_schedule(cursor, account_id)
        grant = self._lock_available_key(cursor, key_digest)
        starts_at = self._latest_valid_end(cursor, account_id) or now
        return self._insert_subscription(cursor, account_id, grant, starts_at)
```

- [x] **Step 4: Re-run tests including two-connection race**

Run: `python -m pytest tests/unit/licenses/test_postgres.py tests/integration/data_pipeline/test_license_repository.py -q`

Expected: PASS or configured database skip; concurrent redemption has exactly one winner.

- [x] **Step 5: Commit**

```bash
git add src/fb_crawl/licenses tests/unit/licenses/test_postgres.py tests/integration/data_pipeline/test_license_repository.py
git commit -m "feat: persist license subscription lifecycle"
```

### Task 4: Entitlement and atomic contact quota services

**Files:**
- Create: `src/fb_crawl/entitlements/service.py`
- Create: `src/fb_crawl/entitlements/quota.py`
- Create: `tests/unit/entitlements/test_service.py`
- Create: `tests/unit/entitlements/test_quota.py`
- Create: `tests/integration/data_pipeline/test_contact_quota.py`

**Interfaces:**
- Produces `EntitlementService.for_account(account_id: int, now: datetime) -> Entitlements`.
- Produces `ContactQuotaService.precheck(account_id, facebook_user_id, now) -> QuotaPrecheck` and `reserve(account_id, facebook_user_id, phone_number_id, lookup_event_id, now) -> RevealDecision`.
- `RevealDecision` fields are `allowed`, `charged`, `reveal_id`, `used`, and `limit`.

- [x] **Step 1: Write quota invariants**

```python
def test_same_user_in_same_month_is_not_charged_twice(quota) -> None:
    first = quota.reserve(ACCOUNT, USER, PHONE, None, NOW)
    second = quota.reserve(ACCOUNT, USER, PHONE, None, NOW)
    assert (first.charged, second.charged) == (True, False)
    assert second.used == first.used

def test_upgrade_keeps_usage_and_raises_limit(quota, entitlements) -> None:
    entitlements.set_limit(ACCOUNT, 1000)
    decision = quota.precheck(ACCOUNT, NEW_USER, NOW)
    assert decision.used == 100
    assert decision.limit == 1000
```

- [x] **Step 2: Run service tests**

Run: `python -m pytest tests/unit/entitlements tests/integration/data_pipeline/test_contact_quota.py -q`

Expected: FAIL on missing services.

- [x] **Step 3: Implement atomic reservation**

Within one transaction, lock/upsert `(account_id, period_start)` in `usage_monthly`, return the existing reveal before checking the limit, reject at `used >= limit`, otherwise insert the unique reveal and increment exactly once. Resolve quota period with configured product timezone. Block over-limit devices for contact operations but allow account/device/license recovery operations.

```python
def reserve(self, account_id: int, facebook_user_id: int, phone_number_id: int, lookup_event_id: int | None, now: datetime) -> RevealDecision:
    period = quota_period(now, self.timezone)
    with self.repository.lock_usage(account_id, period) as usage:
        existing = usage.find_reveal(facebook_user_id)
        if existing: return usage.uncharged(existing)
        if usage.used >= usage.limit: return usage.denied()
        return usage.insert_and_increment(facebook_user_id, phone_number_id, lookup_event_id)
```

- [x] **Step 4: Re-run including concurrent final-slot test**

Run: `python -m pytest tests/unit/entitlements tests/integration/data_pipeline/test_contact_quota.py -q`

Expected: PASS or configured database skip; two concurrent reservations for the final unit yield one allowed result.

- [x] **Step 5: Commit**

```bash
git add src/fb_crawl/entitlements tests/unit/entitlements tests/integration/data_pipeline/test_contact_quota.py
git commit -m "feat: enforce account contact quotas"
```

### Task 5: User license/entitlement and administrator APIs

**Files:**
- Create: `src/fb_crawl/api/routes/licenses.py`
- Create: `src/fb_crawl/api/routes/product_admin.py`
- Modify: `src/fb_crawl/api/product_schemas.py`
- Modify: `src/fb_crawl/composition/product.py`
- Modify: `src/fb_crawl/api/app.py`
- Create: `tests/unit/api/test_license_routes.py`
- Create: `tests/unit/api/test_product_admin_routes.py`

**Interfaces:**
- User: `POST /api/v1/licenses/redeem`, `GET /api/v1/account/entitlements`.
- Admin: create/list/revoke keys, list/suspend accounts, revoke devices/sessions, inspect subscriptions, and start a scheduled subscription now.
- Create-key response contains plaintext exactly once; list responses contain only masked key.

- [x] **Step 1: Write API authorization and plaintext-once tests**

```python
def test_user_cannot_create_license(client, user_headers) -> None:
    response = client.post("/api/v1/admin/license-keys", headers=user_headers, json=GRANT)
    assert response.status_code == 403

def test_created_key_plaintext_is_not_returned_by_list(client, admin_headers) -> None:
    created = client.post("/api/v1/admin/license-keys", headers=admin_headers, json=GRANT).json()
    listed = client.get("/api/v1/admin/license-keys", headers=admin_headers).json()
    assert created["key"].startswith("LF-")
    assert all("key" not in row for row in listed["items"])

def test_oldest_devices_fill_reduced_entitlement_slots(client, account_with_two_devices) -> None:
    first, second = account_with_two_devices
    assert client.get("/api/v1/account/me", headers=first.headers).json()["device_allowed"] is True
    assert client.get("/api/v1/account/me", headers=second.headers).json()["device_allowed"] is False
```

- [x] **Step 2: Verify failing endpoints**

Run: `python -m pytest tests/unit/api/test_license_routes.py tests/unit/api/test_product_admin_routes.py -q`

Expected: FAIL with 404/missing routers.

- [x] **Step 3: Implement closed schemas and role checks**

Add duration discriminated validation, bounded quota/device values, exact crawl booleans, generic invalid-key response, recent-auth requirement for destructive device/session operations, cursor pagination, and immutable audit writes. Extend `ProductServices` with license, entitlement, and quota services. Update `CurrentAccount` loading to rank active devices by `first_seen_at,id`; blocked devices may call account/device/logout/redeem routes but receive `device_limit_exceeded` on contact/export routes.

```python
@router.post("/api/v1/licenses/redeem", response_model=SubscriptionResponse)
def redeem_license(request: RedeemLicenseRequest, current: CurrentAccount = Depends(require_verified)):
    return licenses.redeem(current.account.id, request.key, clock())
```

- [x] **Step 4: Run focused and API regression tests**

Run: `python -m pytest tests/unit/api -q`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/fb_crawl/api src/fb_crawl/composition/product.py tests/unit/api
git commit -m "feat: expose license and entitlement APIs"
```

### Task 6: Administrator dashboard license/account workspace

**Files:**
- Modify: `src/fb_ui/index.html`
- Modify: `src/fb_ui/js/app.js`
- Modify: `src/fb_ui/css/styles.css`
- Modify: `tests/unit/ui/test_dashboard_safety.py`
- Create: `tests/unit/ui/test_product_admin_dashboard.py`
- Modify: `README.md`

**Interfaces:**
- Consumes product admin APIs from Task 5 using the secure admin web session; existing crawler dashboard actions continue using the internal API key boundary.

- [x] **Step 1: Add DOM/safety tests**

```python
def test_admin_license_form_has_explicit_duration_and_entitlements() -> None:
    html = UI_INDEX.read_text(encoding="utf-8")
    for control in ("license-duration-unit", "monthly-contact-limit", "max-devices"):
        assert f'id="{control}"' in html

def test_dashboard_never_persists_plaintext_license() -> None:
    js = UI_APP.read_text(encoding="utf-8")
    assert "localStorage.setItem('license" not in js
```

- [x] **Step 2: Run UI tests**

Run: `python -m pytest tests/unit/ui -q`

Expected: FAIL because product admin workspace is absent.

- [x] **Step 3: Implement admin workspace**

Add login/session state, account/license/subscription tables, create-key modal with preset/custom duration, one-time key copy panel that clears on close/navigation, revoke confirmations, immediate-start forfeiture warning, and safe text-only rendering. Do not place credentials in URL/query/local storage or use `innerHTML` for API data.

```javascript
function renderGeneratedKey(key) {
  generatedKeyOutput.textContent = key;
  generatedKeyModal.addEventListener("close", () => {
    generatedKeyOutput.textContent = "";
  }, { once: true });
}
```

- [x] **Step 4: Run backend verification**

Run: `python -m pytest -q`

Expected: PASS or configured integration skips.

- [x] **Step 5: Commit**

```bash
git add src/fb_ui README.md tests/unit/ui
git commit -m "feat: manage lead finder accounts and licenses"
```
