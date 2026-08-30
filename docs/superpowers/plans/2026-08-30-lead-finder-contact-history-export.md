# Lead Finder Contact Lookup, History, and Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver quota-safe single-profile FBNumber lookup with shared cache/single-flight coordination, account-isolated history, and durable CSV/XLSX exports.

**Architecture:** Reuse `FBNumberProvider`, normalization, evidence, and PostgreSQL identity storage. Add explicit contact cache/lease state and account lookup events; a service orchestrates cache, provider, persistence, and quota without holding database transactions during HTTP calls. Export jobs use a database lease and dedicated mode in the existing worker command.

**Tech Stack:** Python 3.12+, FastAPI, PostgreSQL/psycopg 3, httpx, openpyxl, pytest.

**Spec:** `docs/superpowers/specs/2026-08-30-lead-finder-phase-1-design.md`

## Global Constraints

- Execute after both auth/account and license/quota plans are complete.
- Shared identity/evidence may serve many accounts; lookup events, reveals, and exports are always account-owned.
- Fresh phone TTL is 30 days, negative cache TTL is 7 days, and manual refresh minimum interval is 24 hours.
- External HTTP never runs inside a database transaction or while a database lock is held.
- A unique expiring lease prevents duplicate `(facebook_user_id, provider, field)` calls across API processes.
- Provider failure and `not_found` never consume quota; the same UID/account/quota-month is charged once.
- Account A receives `404` for account B history/export identifiers.
- CSV/XLSX values beginning with `=`, `+`, `-`, or `@` are neutralized.
- Provider raw bodies and secrets never enter history, exports, or logs.

---

### Task 1: Contact cache, lookup history, and export schema

**Files:**
- Create: `src/fb_data_pipeline/migrations/006_product_contact_lookup.sql`
- Modify: `tests/unit/data_pipeline/test_migrations.py`
- Create: `tests/integration/data_pipeline/test_contact_lookup_schema.py`

**Interfaces:**
- Produces `provider_lookup_state`, `enrichment_leases`, `lookup_events`, and `export_jobs`.
- Adds the deferred foreign key from `account_contact_reveals.lookup_event_id` to `lookup_events(id) ON DELETE SET NULL`.

- [ ] **Step 1: Write migration contract tests**

```python
def test_contact_lookup_migration_adds_single_flight_and_history() -> None:
    migration = load_migrations()[-1]
    assert migration.version == 6
    assert "CREATE TABLE enrichment_leases" in migration.sql
    assert "CREATE TABLE lookup_events" in migration.sql
    assert "ON DELETE SET NULL" in migration.sql
```

- [ ] **Step 2: Run migration tests**

Run: `python -m pytest tests/unit/data_pipeline/test_migrations.py tests/integration/data_pipeline/test_contact_lookup_schema.py -q`

Expected: FAIL because migration `006` is absent.

- [ ] **Step 3: Implement indexed, closed-status schema**

Use unique keys `(facebook_user_id, provider, field)` for state/leases, account/time and account/user indexes for lookup history, account/status/created indexes for export claiming, bounded status checks from the spec, and `timestamptz` for every instant.

```sql
ALTER TABLE account_contact_reveals ADD CONSTRAINT account_contact_reveals_lookup_event_fk
FOREIGN KEY (lookup_event_id) REFERENCES lookup_events (id) ON DELETE SET NULL;
CREATE UNIQUE INDEX enrichment_leases_key ON enrichment_leases (facebook_user_id, provider, field);
```

- [ ] **Step 4: Re-run schema tests**

Run: `python -m pytest tests/unit/data_pipeline/test_migrations.py tests/integration/data_pipeline/test_contact_lookup_schema.py -q`

Expected: PASS or configured database skip.

- [ ] **Step 5: Commit**

```bash
git add src/fb_data_pipeline/migrations/006_product_contact_lookup.sql tests/unit/data_pipeline/test_migrations.py tests/integration/data_pipeline/test_contact_lookup_schema.py
git commit -m "feat: add contact lookup and export schema"
```

### Task 2: FBNumber provider identity completion

**Files:**
- Modify: `src/fb_data_pipeline/core/models.py`
- Modify: `src/fb_data_pipeline/providers/fbnumber.py`
- Modify: `src/fb_data_pipeline/services/pipeline.py`
- Modify: `tests/unit/data_pipeline/test_models.py`
- Modify: `tests/unit/data_pipeline/test_fbnumber.py`
- Modify: `tests/unit/data_pipeline/test_pipeline.py`

**Interfaces:**
- Extends `ProviderResult` with `resolved_identity: FacebookIdentity | None = None`.
- Provider accepts returned UID/username only when consistent with non-empty requested aliases.

- [ ] **Step 1: Write mismatch and successful-resolution tests**

```python
def test_provider_returns_consistent_resolved_uid(client) -> None:
    client.response_json = {"data": {"uid": "100123", "username": "sample.user", "phone": "0981234567"}}
    result = provider(client).search(FacebookIdentity(username="sample.user"))
    assert result.resolved_identity == FacebookIdentity(uid="100123", username="sample.user")

def test_provider_rejects_conflicting_username(client) -> None:
    client.response_json = {"uid": "100123", "username": "other.user", "phone": "0981234567"}
    result = provider(client).search(FacebookIdentity(username="sample.user"))
    assert result.status is ProviderStatus.FAILED
    assert result.error_code == "provider_identity_conflict"
```

- [ ] **Step 2: Run provider tests**

Run: `python -m pytest tests/unit/data_pipeline/test_models.py tests/unit/data_pipeline/test_fbnumber.py -q`

Expected: FAIL because `resolved_identity` is absent.

- [ ] **Step 3: Implement conservative identity extraction**

Recognize only documented `uid|user_id|facebook_uid` and `username|facebook_username` fields inside the selected provider data object, normalize numeric UID and Facebook username, reject conflicts, and never scan arbitrary nested values for identity. Update `EnrichmentPipeline` to merge a consistent `resolved_identity` into the original identity before persistence. Keep existing phone/profile behavior unchanged.

```python
resolved = FacebookIdentity(uid=_uid(data), username=_provider_username(data))
if identity.username and resolved.username and identity.username.casefold() != resolved.username.casefold():
    return ProviderResult(provider=self.name, status=ProviderStatus.FAILED, checked_at=checked_at, error_code="provider_identity_conflict")
```

- [ ] **Step 4: Re-run provider and pipeline regression tests**

Run: `python -m pytest tests/unit/data_pipeline -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fb_data_pipeline/core/models.py src/fb_data_pipeline/providers/fbnumber.py src/fb_data_pipeline/services/pipeline.py tests/unit/data_pipeline
git commit -m "feat: resolve provider facebook identity safely"
```

### Task 3: Contact repository, cache state, and expiring leases

**Files:**
- Create: `src/fb_crawl/contacts/__init__.py`
- Create: `src/fb_crawl/contacts/models.py`
- Create: `src/fb_crawl/contacts/repository.py`
- Create: `src/fb_crawl/contacts/postgres.py`
- Modify: `src/fb_data_pipeline/repositories/postgres.py`
- Create: `tests/unit/contacts/test_postgres.py`
- Create: `tests/integration/data_pipeline/test_contact_repository.py`

**Interfaces:**
- Produces `ContactIdentity`, `CachedContact`, `LookupState`, `EnrichmentLease`, and `LookupEvent`.
- Produces `PostgresContactRepository.resolve_identity`, `get_cached_contact`, `create_lookup_event`, `complete_lookup_event`, `claim_lease`, `release_lease`, `wait_for_state`, and `update_provider_state`.
- Exposes `PostgresRepository.upsert_identity(identity: FacebookIdentity) -> int` so contact and pipeline persistence share identical alias-lock/conflict behavior.

- [ ] **Step 1: Write identity and lease tests**

```python
def test_only_one_owner_claims_live_lease(repository) -> None:
    first = repository.claim_lease(USER_ID, "fbnumber", "phone", "owner-a", NOW, NOW + timedelta(seconds=30))
    second = repository.claim_lease(USER_ID, "fbnumber", "phone", "owner-b", NOW, NOW + timedelta(seconds=30))
    assert (first.acquired, second.acquired) == (True, False)

def test_expired_lease_can_be_reclaimed(repository) -> None:
    repository.claim_lease(USER_ID, "fbnumber", "phone", "dead", NOW, NOW + timedelta(seconds=1))
    assert repository.claim_lease(USER_ID, "fbnumber", "phone", "next", NOW + timedelta(seconds=2), NOW + timedelta(seconds=32)).acquired
```

- [ ] **Step 2: Run repository tests**

Run: `python -m pytest tests/unit/contacts/test_postgres.py tests/integration/data_pipeline/test_contact_repository.py -q`

Expected: FAIL on missing contact package.

- [ ] **Step 3: Implement repository with no HTTP dependency**

Refactor existing alias upsert into the public `upsert_identity` method without changing persistence order. Lease claim uses one `INSERT ... ON CONFLICT ... DO UPDATE ... WHERE leased_until <= now` statement. Cache reads join the preferred FBNumber slot and state. Event completion only permits the owning account/event transition from `processing` once.

```sql
INSERT INTO enrichment_leases (facebook_user_id, provider, field, owner_token, leased_until)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (facebook_user_id, provider, field) DO UPDATE
SET owner_token = EXCLUDED.owner_token, leased_until = EXCLUDED.leased_until
WHERE enrichment_leases.leased_until <= %s
RETURNING owner_token;
```

- [ ] **Step 4: Run contact and existing persistence tests**

Run: `python -m pytest tests/unit/contacts tests/unit/data_pipeline/test_postgres_repository.py tests/integration/data_pipeline/test_contact_repository.py -q`

Expected: PASS or configured database skip.

- [ ] **Step 5: Commit**

```bash
git add src/fb_crawl/contacts src/fb_data_pipeline/repositories/postgres.py tests/unit/contacts tests/integration/data_pipeline/test_contact_repository.py
git commit -m "feat: coordinate shared contact cache"
```

### Task 4: Contact lookup orchestration service

**Files:**
- Create: `src/fb_crawl/contacts/service.py`
- Create: `tests/unit/contacts/test_service.py`

**Interfaces:**
- Consumes `EntitlementService`, `ContactQuotaService`, `PostgresContactRepository`, `EnrichmentPipeline`, and `PipelinePersistenceService` through narrow protocols.
- Produces `ContactLookupService.lookup(account, device, ContactLookupRequest, now, force_refresh=False) -> ContactLookupResult` and `.get_event(account_id, event_id) -> ContactLookupResult`.
- Result state is `found|not_found|processing|quota_exceeded|failed` with safe metadata and no raw provider payload.

- [ ] **Step 1: Write branch-complete service tests**

```python
def test_fresh_cache_never_calls_provider(service, provider) -> None:
    result = service.lookup(ACCOUNT, DEVICE, REQUEST, NOW)
    assert result.state == "found"
    assert result.source == "cache"
    assert provider.calls == []

def test_non_owner_returns_processing_after_bounded_wait(service, repo) -> None:
    repo.lease_owned_by_other = True
    repo.wait_result = None
    assert service.lookup(ACCOUNT, DEVICE, REQUEST, NOW).state == "processing"
```

- [ ] **Step 2: Run service tests**

Run: `python -m pytest tests/unit/contacts/test_service.py -q`

Expected: FAIL because service is missing.

- [ ] **Step 3: Implement the exact lookup sequence**

Implement the 14-step flow in the spec. Precheck existing reveal/quota before provider, evaluate fresh/negative/manual-refresh state, call provider only as lease owner, always release lease in `finally`, persist provider attempts/evidence through the existing pipeline, update state TTLs, reserve quota before exposing phone, and map provider auth/rate/transport failures to safe codes.

```python
lease = self.contacts.claim_lease(user.id, "fbnumber", "phone", owner, now, now + self.lease_ttl)
if not lease.acquired:
    return self._wait_or_processing(account.id, event.id, user.id)
try:
    run = self.pipeline.run_bundles((UserBundle(identity=user.identity),))
    self.persistence.persist(run)
    return self._finish_from_provider(account, event, run, now)
finally:
    self.contacts.release_lease(user.id, "fbnumber", "phone", owner)
```

- [ ] **Step 4: Run service and provider regression tests**

Run: `python -m pytest tests/unit/contacts tests/unit/data_pipeline -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fb_crawl/contacts/service.py tests/unit/contacts/test_service.py
git commit -m "feat: orchestrate contact lookup enrichment"
```

### Task 5: Contact lookup and polling API

**Files:**
- Create: `src/fb_crawl/api/routes/contacts.py`
- Create: `src/fb_crawl/api/correlation.py`
- Modify: `src/fb_crawl/api/product_schemas.py`
- Modify: `src/fb_crawl/composition/product.py`
- Modify: `src/fb_crawl/api/app.py`
- Create: `tests/unit/api/test_contact_routes.py`
- Create: `tests/unit/api/test_product_correlation.py`
- Create: `tests/integration/test_contact_lookup_api.py`

**Interfaces:**
- Produces `POST /api/v1/contacts/lookup` and `GET /api/v1/contacts/lookups/{event_id}`.
- Returns `200` found/not-found, `202` processing, `409` identity conflict, and `429` quota/rate-limit with the closed response envelope from the spec.

- [ ] **Step 1: Write response and tenant tests**

```python
def test_processing_lookup_returns_202_and_poll_url(client, auth_headers) -> None:
    response = client.post("/api/v1/contacts/lookup", headers=auth_headers, json=REQUEST)
    assert response.status_code == 202
    assert response.json()["meta"]["poll_url"].startswith("/api/v1/contacts/lookups/")

def test_other_account_cannot_poll_event(client, account_b_headers, event_a) -> None:
    assert client.get(f"/api/v1/contacts/lookups/{event_a}", headers=account_b_headers).status_code == 404

def test_contact_response_has_safe_correlation_id(client, auth_headers) -> None:
    response = client.post("/api/v1/contacts/lookup", headers=auth_headers, json=REQUEST)
    assert UUID(response.headers["X-Request-ID"])
```

- [ ] **Step 2: Run API tests**

Run: `python -m pytest tests/unit/api/test_contact_routes.py tests/integration/test_contact_lookup_api.py -q`

Expected: FAIL on missing router.

- [ ] **Step 3: Implement closed Pydantic contracts**

Validate UID digits/length, username grammar, Facebook profile URLs, optional `force_refresh`, and conflicting aliases before service calls. Response includes normalized/display phone, observed time, source, quota charged/used/limit, and safe state only. Add exact bearer dependency and no internal API-key fallback. Install request-correlation middleware that accepts only valid UUID input or generates one, returns it as `X-Request-ID`, and places it in sanitized structured log context.

```python
@router.post("/api/v1/contacts/lookup", response_model=ContactLookupResponse)
def lookup(request: ContactLookupRequest, current: CurrentAccount = Depends(require_contact_device)):
    result = service.lookup(current.account, current.device, request.to_domain(), clock(), force_refresh=request.force_refresh)
    return response_for_lookup(result)
```

- [ ] **Step 4: Run API tests**

Run: `python -m pytest tests/unit/api tests/integration/test_contact_lookup_api.py -q`

Expected: PASS or configured database skip.

- [ ] **Step 5: Commit**

```bash
git add src/fb_crawl/api src/fb_crawl/composition/product.py tests/unit/api/test_contact_routes.py tests/integration/test_contact_lookup_api.py
git commit -m "feat: expose account contact lookup API"
```

### Task 6: Account-isolated lookup history

**Files:**
- Create: `src/fb_crawl/history/__init__.py`
- Create: `src/fb_crawl/history/models.py`
- Create: `src/fb_crawl/history/repository.py`
- Create: `src/fb_crawl/history/postgres.py`
- Create: `src/fb_crawl/api/routes/history.py`
- Modify: `src/fb_crawl/api/product_schemas.py`
- Modify: `src/fb_crawl/api/app.py`
- Create: `tests/unit/history/test_postgres.py`
- Create: `tests/unit/api/test_history_routes.py`
- Create: `tests/integration/data_pipeline/test_history_isolation.py`

**Interfaces:**
- Produces cursor-paginated list/get/delete-one/delete-filtered history APIs and filters `outcome`, `name`, `uid`, `username`, `phone`, `created_from`, `created_to`.

- [ ] **Step 1: Write isolation and deletion tests**

```python
def test_history_query_always_binds_account(repository) -> None:
    page = repository.list(AccountHistoryQuery(account_id=ACCOUNT_A, uid="100"))
    assert all(item.account_id == ACCOUNT_A for item in page.items)

def test_deleting_event_does_not_restore_quota(repository, quota) -> None:
    repository.delete_one(ACCOUNT_A, EVENT_ID)
    assert quota.precheck(ACCOUNT_A, USER_ID, NOW).already_revealed is True
```

- [ ] **Step 2: Run history tests**

Run: `python -m pytest tests/unit/history tests/unit/api/test_history_routes.py tests/integration/data_pipeline/test_history_isolation.py -q`

Expected: FAIL on missing history package/router.

- [ ] **Step 3: Implement account-bound SQL and opaque cursors**

Follow existing user query cursor patterns, bind account as the first required repository field, join only account reveal/current shared identity data, return `404` for foreign IDs, use `ON DELETE SET NULL` reveal link behavior, and require explicit confirmation body for filtered bulk delete.

```python
@dataclass(frozen=True, slots=True)
class AccountHistoryQuery:
    account_id: int
    uid: str | None = None
    outcome: str | None = None
    cursor: str | None = None
    limit: int = 20
```

- [ ] **Step 4: Re-run tests**

Run: `python -m pytest tests/unit/history tests/unit/api/test_history_routes.py tests/integration/data_pipeline/test_history_isolation.py -q`

Expected: PASS or configured database skip.

- [ ] **Step 5: Commit**

```bash
git add src/fb_crawl/history src/fb_crawl/api tests/unit/history tests/unit/api/test_history_routes.py tests/integration/data_pipeline/test_history_isolation.py
git commit -m "feat: add private contact lookup history"
```

### Task 7: Durable CSV/XLSX export jobs and worker

**Files:**
- Modify: `pyproject.toml`
- Create: `src/fb_crawl/exports/__init__.py`
- Create: `src/fb_crawl/exports/models.py`
- Create: `src/fb_crawl/exports/formatters.py`
- Create: `src/fb_crawl/exports/artifacts.py`
- Create: `src/fb_crawl/exports/postgres.py`
- Create: `src/fb_crawl/exports/service.py`
- Create: `src/fb_crawl/api/routes/product_exports.py`
- Create: `src/fb_crawl/api/routes/product_metrics.py`
- Modify: `src/fb_crawl/api/product_schemas.py`
- Modify: `src/fb_crawl/api/app.py`
- Modify: `src/fb_crawl/cli/worker.py`
- Create: `tests/unit/exports/test_formatters.py`
- Create: `tests/unit/exports/test_service.py`
- Create: `tests/unit/api/test_product_export_routes.py`
- Create: `tests/unit/api/test_product_metrics_routes.py`
- Modify: `tests/integration/test_worker_cli.py`
- Create: `tests/integration/data_pipeline/test_export_jobs.py`

**Interfaces:**
- Produces create/status/download/delete export APIs.
- Produces admin-only `GET /api/v1/admin/product-metrics` for lookup, unique reveal, cache, negative-cache, provider outcome/latency, quota rejection, account/license/device, and export counters.
- Adds `fb-crawl worker run --kind crawl|export [--once]` with backward-compatible default `crawl`; `--once` performs one poll and exits for operations/tests.
- Produces `ExportWorker.run_once() -> bool` using expiring database claims and `ExportArtifactStore`.

- [ ] **Step 1: Write formatter, ownership, and worker-mode tests**

```python
@pytest.mark.parametrize("value", ["=1+1", "+cmd", "-2", "@SUM(A1:A2)"])
def test_spreadsheet_formula_values_are_neutralized(value: str) -> None:
    assert safe_cell(value).startswith("'")

def test_export_worker_mode_does_not_require_browser_session(monkeypatch) -> None:
    assert app.main(["worker", "run", "--kind", "export", "--once"]) == 0

def test_product_metrics_reject_regular_user(client, user_headers) -> None:
    assert client.get("/api/v1/admin/product-metrics", headers=user_headers).status_code == 403
```

- [ ] **Step 2: Run export tests**

Run: `python -m pytest tests/unit/exports tests/unit/api/test_product_export_routes.py tests/unit/api/test_product_metrics_routes.py tests/integration/test_worker_cli.py tests/integration/data_pipeline/test_export_jobs.py -q`

Expected: FAIL because export components and worker kind are absent.

- [ ] **Step 3: Implement durable export path**

Add `openpyxl>=3.1,<4` to `api` and `dev` extras. Freeze validated account-owned filters in the job, stream UTF-8 CSV, write XLSX in write-only mode, neutralize formula prefixes, write artifacts atomically below configured `LEAD_FINDER_EXPORT_DIR`, claim with owner/lease/attempt fields, recover expired claims, authorize every status/download/delete, and delete artifacts after 24 hours. Add indexed aggregate queries for the admin product metrics without exposing contact values. `--kind export` requires only database/product/export settings; it must not load Selenium, Facebook session, or FBNumber.

```python
def safe_cell(value: str) -> str:
    return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value

class ExportWorker:
    def run_once(self) -> bool:
        job = self.repository.claim_next(self.worker_id, self.clock())
        return False if job is None else self._generate_and_complete(job)
```

- [ ] **Step 4: Run full backend verification**

Run: `python -m pytest -q`

Expected: PASS or configured database skips.

Run: `python -m build`

Expected: package contains migrations `004`–`006` and all product modules.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/fb_crawl/exports src/fb_crawl/api src/fb_crawl/cli/worker.py tests/unit/exports tests/unit/api/test_product_export_routes.py tests/integration/test_worker_cli.py tests/integration/data_pipeline/test_export_jobs.py
git commit -m "feat: export account contact history"
```
