# Lead Finder Browser Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the WXT starter with a production Chrome Side Panel extension that authenticates users, detects the current Facebook profile, performs explicit contact lookup, and exposes quota, private history, and CSV/XLSX export.

**Architecture:** Keep content code limited to route/identity detection, keep credentials and network orchestration in the Manifest V3 background service worker, and render product flows in a React Side Panel. Pure shared contracts and reducers make behavior unit-testable with WXT's Vitest plugin; Playwright verifies the built extension against controlled Facebook/backend fixtures.

**Tech Stack:** WXT 0.21.x, React 19, TypeScript 5.9, Manifest V3, Vitest/WXT fake browser, Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-30-lead-finder-phase-1-design.md`

## Global Constraints

- Execute after the three backend plans are complete and their API schemas are stable.
- Main UI is `entrypoints/sidepanel/index.html`, the supported WXT side-panel entrypoint.
- Content scripts never receive refresh/access tokens and never call the backend.
- Background stores only device UUID and refresh token in `storage.local`; access token remains in memory.
- Lookup occurs only after the user presses **Find Contact**.
- Resolver accepts numeric `profile.php` IDs or a UID paired with the expected vanity username; conflicting UIDs are rejected.
- Do not copy FBNumber's global GraphQL/XHR interception bridge in Phase 1.
- Permissions are exactly `storage`, `sidePanel`, Facebook host access, and the configured backend origin.
- API origin comes from non-secret `WXT_LEAD_FINDER_API_ORIGIN`; no token/key is compiled into the manifest.
- Official implementation references: `https://wxt.dev/guide/essentials/entrypoints`, `https://wxt.dev/guide/essentials/unit-testing`, `https://wxt.dev/guide/essentials/e2e-testing.html`, and `https://wxt.dev/guide/essentials/config/environment-variables.html`.

---

### Task 1: WXT Side Panel, manifest, and test foundation

**Files:**
- Modify: `package.json`
- Modify: `pnpm-lock.yaml`
- Modify: `wxt.config.ts`
- Create: `.env.example`
- Create: `app.config.ts`
- Create: `vitest.config.ts`
- Create: `tests/setup.ts`
- Create: `entrypoints/sidepanel/index.html`
- Create: `entrypoints/sidepanel/main.tsx`
- Create: `entrypoints/sidepanel/App.tsx`
- Create: `entrypoints/sidepanel/style.css`
- Delete: `entrypoints/popup/index.html`
- Delete: `entrypoints/popup/main.tsx`
- Delete: `entrypoints/popup/App.tsx`
- Delete: `entrypoints/popup/App.css`
- Delete: `entrypoints/popup/style.css`
- Create: `tests/unit/manifest.test.ts`
- Create: `tests/unit/sidepanel-smoke.test.tsx`

**Interfaces:**
- Produces a Chrome MV3 Side Panel build and typed `appConfig.apiOrigin`.
- Produces scripts `test`, `test:watch`, and `test:e2e` without changing existing build/compile scripts.

- [ ] **Step 1: Add failing manifest and render tests**

```tsx
it('renders the Lead Finder shell', () => {
  render(<App />);
  expect(screen.getByRole('heading', { name: 'Lead Finder' })).toBeVisible();
});

it('does not request cookies or unlimitedStorage', async () => {
  const manifest = await loadGeneratedManifest();
  expect(manifest.permissions).toEqual(['storage', 'sidePanel']);
});
```

- [ ] **Step 2: Run tests and verify missing setup**

Run: `pnpm test --run`

Expected: FAIL because Vitest/Side Panel do not exist.

- [ ] **Step 3: Install official WXT test tooling and create Side Panel**

Run: `pnpm add -D vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event @playwright/test`

Configure `WxtVitest()` from `wxt/testing/vitest-plugin`. Use manifest function syntax so the backend host permission is derived after WXT loads `.env`. Validate that `WXT_LEAD_FINDER_API_ORIGIN` is an absolute `https:` origin for production and allow explicit `http://127.0.0.1`/`localhost` only in development/test.

```ts
export default defineConfig({
  modules: ['@wxt-dev/module-react'],
  manifest: () => ({ permissions: ['storage', 'sidePanel'], host_permissions: ['*://*.facebook.com/*', `${import.meta.env.WXT_LEAD_FINDER_API_ORIGIN}/*`] }),
});
```

- [ ] **Step 4: Run tests, compile, and build**

Run: `pnpm test --run && pnpm compile && pnpm build`

Expected: PASS and `.output/chrome-mv3/sidepanel.html` exists.

- [ ] **Step 5: Commit in the `lead-finder` repository**

```bash
git add package.json pnpm-lock.yaml wxt.config.ts .env.example app.config.ts vitest.config.ts tests entrypoints
git commit -m "feat: initialize lead finder side panel"
```

### Task 2: Shared backend contracts and safe API transport

**Files:**
- Create: `lib/contracts/auth.ts`
- Create: `lib/contracts/account.ts`
- Create: `lib/contracts/contact.ts`
- Create: `lib/contracts/history.ts`
- Create: `lib/contracts/export.ts`
- Create: `lib/api/errors.ts`
- Create: `lib/api/client.ts`
- Create: `tests/unit/api-client.test.ts`

**Interfaces:**
- Produces exact TypeScript shapes for `AuthTokens`, `Account`, `Entitlements`, `ContactLookupRequest/Response`, `HistoryPage`, and `ExportJob` matching backend closed schemas.
- Produces `ApiClient.request<T>(path: ApiPath, init?: RequestInit, accessToken?: string) -> Promise<T>`; `ApiPath` must start with `/api/v1/`.

- [ ] **Step 1: Write origin/error tests**

```ts
it('rejects absolute or off-origin API paths', async () => {
  await expect(client.request('https://evil.test/token' as ApiPath)).rejects.toThrow('relative API path');
});

it('maps the safe backend error envelope', async () => {
  server.respond(429, { code: 'contact_quota_exceeded', message: 'Monthly contact quota exhausted.' });
  await expect(client.request('/api/v1/contacts/lookup')).rejects.toMatchObject({ code: 'contact_quota_exceeded', status: 429 });
});
```

- [ ] **Step 2: Run focused tests**

Run: `pnpm test --run tests/unit/api-client.test.ts`

Expected: FAIL on missing contracts/client.

- [ ] **Step 3: Implement strict transport**

Resolve only relative `/api/v1/` paths against `appConfig.apiOrigin`, set JSON headers, attach bearer only when supplied by background auth, parse JSON only for documented content types, enforce request timeout with `AbortController`, and surface only backend `code/message/status` without response headers or bodies containing secrets.

```ts
async request<T>(path: ApiPath, init: RequestInit = {}, accessToken?: string): Promise<T> {
  if (!path.startsWith('/api/v1/')) throw new Error('Expected relative API path');
  const response = await fetch(new URL(path, this.origin), this.authorized(init, accessToken));
  return this.parse<T>(response);
}
```

- [ ] **Step 4: Re-run test and compile**

Run: `pnpm test --run tests/unit/api-client.test.ts && pnpm compile`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/contracts lib/api tests/unit/api-client.test.ts
git commit -m "feat: add lead finder API contracts"
```

### Task 3: Device/session storage and background authentication manager

**Files:**
- Create: `lib/storage/device.ts`
- Create: `lib/storage/session.ts`
- Create: `lib/messaging/messages.ts`
- Create: `lib/auth/manager.ts`
- Modify: `entrypoints/background.ts`
- Create: `tests/unit/device-storage.test.ts`
- Create: `tests/unit/auth-manager.test.ts`
- Create: `tests/unit/background-auth.test.ts`

**Interfaces:**
- Produces `getOrCreateInstallationId() -> Promise<string>` using `storage.defineItem`.
- Produces `AuthManager.login`, `register`, `refresh`, `logout`, `getAccount`, and `withAccessToken`.
- Background accepts typed messages `AUTH_REGISTER`, `AUTH_LOGIN`, `AUTH_LOGOUT`, `AUTH_STATUS`, and `AUTH_RESEND_VERIFICATION`; message results are discriminated `{ok:true,data}|{ok:false,error}`.

- [ ] **Step 1: Write storage and refresh single-flight tests**

```ts
it('creates one stable installation UUID', async () => {
  expect(await getOrCreateInstallationId()).toBe(await getOrCreateInstallationId());
});

it('coalesces concurrent refresh calls', async () => {
  await Promise.all([manager.withAccessToken(call), manager.withAccessToken(call)]);
  expect(api.refreshCalls).toBe(1);
});
```

- [ ] **Step 2: Verify failures**

Run: `pnpm test --run tests/unit/device-storage.test.ts tests/unit/auth-manager.test.ts tests/unit/background-auth.test.ts`

Expected: FAIL because storage/manager/messages are absent.

- [ ] **Step 3: Implement background-only credential ownership**

Use `wxt/utils/storage` for installation UUID and refresh token, clear refresh state on rotation failure/logout, keep access token and expiry only in `AuthManager` memory, include installation ID in login/refresh requests, reject content-script callers for auth-token operations by checking sender context, and configure action click to open the Side Panel.

```ts
const installationId = storage.defineItem<string>('local:installation-id');
const refreshToken = storage.defineItem<string | null>('local:refresh-token', { fallback: null });
let access: { token: string; expiresAt: number } | null = null;
```

- [ ] **Step 4: Run tests and compile**

Run: `pnpm test --run tests/unit/device-storage.test.ts tests/unit/auth-manager.test.ts tests/unit/background-auth.test.ts && pnpm compile`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/storage lib/messaging lib/auth entrypoints/background.ts tests/unit
git commit -m "feat: manage extension authentication securely"
```

### Task 4: Facebook route detector and conservative identity resolver

**Files:**
- Create: `lib/facebook/routes.ts`
- Create: `lib/facebook/identity.ts`
- Create: `entrypoints/facebook.content/index.ts`
- Create: `tests/fixtures/facebook/numeric-profile.html`
- Create: `tests/fixtures/facebook/vanity-profile.html`
- Create: `tests/fixtures/facebook/conflicting-profile.html`
- Create: `tests/unit/facebook-routes.test.ts`
- Create: `tests/unit/facebook-identity.test.ts`
- Create: `tests/unit/facebook-content.test.ts`
- Delete: `entrypoints/content.ts`

**Interfaces:**
- Produces `classifyFacebookUrl(url: URL) -> FacebookSurface` and `resolveProfileIdentity(document, url) -> ProfileIdentityResult`.
- Result is `{kind:'resolved', identity}`, `{kind:'partial', identity}`, `{kind:'unsupported'}`, or `{kind:'conflict', reason:'multiple_uids'|'username_mismatch'}`.
- Content sends `PROFILE_CONTEXT_CHANGED` with tab-local identity context; it sends no page HTML/network payload.

- [ ] **Step 1: Write route and identity fixtures/tests**

```ts
it.each(['/groups/123', '/marketplace/item/1', '/sample/posts/2'])('rejects non-profile route %s', path => {
  expect(classifyFacebookUrl(new URL(`https://www.facebook.com${path}`)).kind).not.toBe('profile');
});

it('rejects two UIDs paired to one expected username', () => {
  expect(resolveFixture('conflicting-profile.html', '/sample.user')).toMatchObject({ kind: 'conflict', reason: 'multiple_uids' });
});
```

- [ ] **Step 2: Run focused tests**

Run: `pnpm test --run tests/unit/facebook-routes.test.ts tests/unit/facebook-identity.test.ts tests/unit/facebook-content.test.ts`

Expected: FAIL on missing resolver/content entrypoint.

- [ ] **Step 3: Implement detection without network interception**

Port the proven URL exclusions and username/UID pairing rules from `fb-crawl` rather than minified FBNumber selectors. Read numeric query IDs, canonical links, and narrowly matched embedded JSON pairs. Observe `popstate`, patched history changes, and a debounced `MutationObserver`; emit only when the normalized context changes and clean observers with the WXT content-script context lifecycle.

```ts
export default defineContentScript({
  matches: ['*://*.facebook.com/*'],
  main(ctx) {
    const stop = observeProfileContext(document, location, context => browser.runtime.sendMessage({ type: 'PROFILE_CONTEXT_CHANGED', context }));
    ctx.onInvalidated(stop);
  },
});
```

- [ ] **Step 4: Run tests, compile, and build manifest**

Run: `pnpm test --run tests/unit/facebook-*.test.ts tests/unit/facebook-content.test.ts && pnpm compile && pnpm build`

Expected: PASS; generated content script matches `*://*.facebook.com/*` and is not `all_frames`.

- [ ] **Step 5: Commit**

```bash
git add lib/facebook entrypoints/facebook.content tests/fixtures/facebook tests/unit entrypoints/content.ts
git commit -m "feat: detect facebook profile identity"
```

### Task 5: Background contact, history, and export orchestration

**Files:**
- Create: `lib/api/contact-client.ts`
- Create: `lib/api/history-client.ts`
- Create: `lib/api/export-client.ts`
- Create: `lib/background/profile-state.ts`
- Create: `lib/background/product-handlers.ts`
- Modify: `lib/messaging/messages.ts`
- Modify: `entrypoints/background.ts`
- Create: `tests/unit/product-handlers.test.ts`
- Create: `tests/unit/profile-state.test.ts`

**Interfaces:**
- Adds messages `GET_PROFILE_CONTEXT`, `FIND_CONTACT`, `GET_LOOKUP_HISTORY`, `CREATE_EXPORT`, `GET_EXPORT`, and `DELETE_EXPORT`.
- `FIND_CONTACT` is keyed by `(tabId, normalized identity)`; stale completions never replace newer profile state.

- [ ] **Step 1: Write explicit-click, polling, and stale-response tests**

```ts
it('profile changes never call lookup automatically', async () => {
  await handlers.onProfileChanged(TAB, PROFILE_A);
  expect(api.lookupCalls).toHaveLength(0);
});

it('drops profile A response after navigation to profile B', async () => {
  const pending = handlers.findContact(TAB, PROFILE_A);
  await handlers.onProfileChanged(TAB, PROFILE_B);
  api.resolveLookup(PROFILE_A);
  expect((await pending).error?.code).toBe('stale_profile_context');
});
```

- [ ] **Step 2: Run orchestration tests**

Run: `pnpm test --run tests/unit/product-handlers.test.ts tests/unit/profile-state.test.ts`

Expected: FAIL on missing handlers.

- [ ] **Step 3: Implement typed background handlers**

Authorize every API request through `AuthManager.withAccessToken`, send the current normalized profile only on user action, poll `202` event URLs with bounded backoff/cancellation, store no contact history locally, and forward account-owned history/export status. Validate message payloads before calling clients and return safe discriminated errors.

```ts
handlers.FIND_CONTACT = async (message, sender) => {
  const tabId = requireTabId(sender);
  const snapshot = profileState.requireCurrent(tabId, message.identity);
  return auth.withAccessToken(token => contacts.lookup(snapshot.identity, token));
};
```

- [ ] **Step 4: Run tests and compile**

Run: `pnpm test --run tests/unit/product-handlers.test.ts tests/unit/profile-state.test.ts && pnpm compile`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/api lib/background lib/messaging entrypoints/background.ts tests/unit
git commit -m "feat: orchestrate lead finder background requests"
```

### Task 6: Side Panel authentication, license, quota, and device UX

**Files:**
- Create: `entrypoints/sidepanel/state.ts`
- Create: `entrypoints/sidepanel/components/AuthScreen.tsx`
- Create: `entrypoints/sidepanel/components/VerificationScreen.tsx`
- Create: `entrypoints/sidepanel/components/LicenseScreen.tsx`
- Create: `entrypoints/sidepanel/components/AccountMenu.tsx`
- Create: `entrypoints/sidepanel/components/QuotaMeter.tsx`
- Modify: `entrypoints/sidepanel/App.tsx`
- Modify: `entrypoints/sidepanel/style.css`
- Create: `tests/unit/sidepanel-state.test.ts`
- Create: `tests/unit/sidepanel-auth.test.tsx`
- Create: `tests/unit/sidepanel-license.test.tsx`

**Interfaces:**
- Consumes background auth/account/license messages only; React components never instantiate `ApiClient`.

- [ ] **Step 1: Write state and accessible-form tests**

```tsx
it('shows verification guidance for an unverified account', async () => {
  renderPanel({ account: unverifiedAccount });
  expect(screen.getByRole('heading', { name: 'Verify your email' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Resend verification email' })).toBeEnabled();
});

it('shows default plan limit and locked crawl features', () => {
  renderPanel({ entitlements: defaultEntitlements });
  expect(screen.getByText('100 contacts / month')).toBeVisible();
  expect(screen.getByText('Group crawl locked')).toBeVisible();
});
```

- [ ] **Step 2: Run component tests**

Run: `pnpm test --run tests/unit/sidepanel-state.test.ts tests/unit/sidepanel-auth.test.tsx tests/unit/sidepanel-license.test.tsx`

Expected: FAIL because flows are absent.

- [ ] **Step 3: Implement compact Side Panel flows**

Use a discriminated reducer for loading/anonymous/unverified/authenticated/device-blocked states; accessible labels and inline safe errors; password fields never persisted; verification opens only the configured backend link; license input is cleared after redemption; quota/device/expiry data always comes from backend account state.

```ts
type PanelState =
  | { kind: 'loading' }
  | { kind: 'anonymous' }
  | { kind: 'unverified'; account: Account }
  | { kind: 'ready'; account: Account; entitlements: Entitlements }
  | { kind: 'device-blocked'; account: Account; devices: Device[] };
```

- [ ] **Step 4: Run component suite**

Run: `pnpm test --run tests/unit/sidepanel-*.test.tsx tests/unit/sidepanel-state.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add entrypoints/sidepanel tests/unit/sidepanel-state.test.ts tests/unit/sidepanel-auth.test.tsx tests/unit/sidepanel-license.test.tsx
git commit -m "feat: add lead finder account and license UX"
```

### Task 7: Profile lookup, private history, copy, and export UX

**Files:**
- Create: `entrypoints/sidepanel/components/ProfileCard.tsx`
- Create: `entrypoints/sidepanel/components/ContactResult.tsx`
- Create: `entrypoints/sidepanel/components/HistoryView.tsx`
- Create: `entrypoints/sidepanel/components/ExportMenu.tsx`
- Modify: `entrypoints/sidepanel/App.tsx`
- Modify: `entrypoints/sidepanel/state.ts`
- Modify: `entrypoints/sidepanel/style.css`
- Create: `tests/unit/sidepanel-contact.test.tsx`
- Create: `tests/unit/sidepanel-history.test.tsx`

**Interfaces:**
- `Find Contact` is enabled only for resolved/partial personal profile context and authenticated allowed device state.
- History supports outcome/identity/date filters and cursor “Load more”; export supports current filter as CSV or XLSX.

- [ ] **Step 1: Write lookup/history/export behavior tests**

```tsx
it('does not lookup until Find Contact is pressed', async () => {
  const user = userEvent.setup();
  renderPanel({ profile: resolvedProfile });
  expect(messages.findContact).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Find Contact' }));
  expect(messages.findContact).toHaveBeenCalledWith(resolvedProfile.identity);
});

it('copies only after a user gesture', async () => {
  await userEvent.click(screen.getByRole('button', { name: 'Copy phone' }));
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith('+84981234567');
});
```

- [ ] **Step 2: Run component tests**

Run: `pnpm test --run tests/unit/sidepanel-contact.test.tsx tests/unit/sidepanel-history.test.tsx`

Expected: FAIL on missing components.

- [ ] **Step 3: Implement all documented states**

Render unsupported/conflict/loading/processing/found/not-found/quota/provider/device states, show source/observed time/quota delta, require explicit copy/export actions, poll export until ready, download through an authorized background fetch/object URL, revoke object URL after use, and preserve history filters without persisting contact values locally.

```tsx
<button disabled={!canLookup(state)} onClick={() => dispatch({ type: 'FIND_CONTACT' })}>
  Find Contact
</button>
{state.contact?.kind === 'found' && <ContactResult result={state.contact.result} />}
```

- [ ] **Step 4: Run full unit/build verification**

Run: `pnpm test --run && pnpm compile && pnpm build`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add entrypoints/sidepanel tests/unit/sidepanel-contact.test.tsx tests/unit/sidepanel-history.test.tsx
git commit -m "feat: add contact history and export UX"
```

### Task 8: Built-extension end-to-end acceptance and release documentation

**Files:**
- Create: `playwright.config.ts`
- Create: `tests/e2e/fixtures/mock-backend.ts`
- Create: `tests/e2e/fixtures/facebook-profile.html`
- Create: `tests/e2e/lead-finder.spec.ts`
- Modify: `README.md`

**Interfaces:**
- Tests `.output/chrome-mv3` in a persistent Chromium context and opens `chrome-extension://<id>/sidepanel.html` directly for deterministic Side Panel coverage.

- [ ] **Step 1: Write end-to-end happy path and quota-repeat scenarios**

```ts
test('register login redeem lookup history and export', async ({ extensionPanel, facebookPage, backend }) => {
  await backend.seedVerifiedAccountAndLicense();
  await facebookPage.goto('https://www.facebook.com/sample.user');
  await extensionPanel.loginAndRedeem();
  await extensionPanel.findContact();
  await expect(extensionPanel.phone).toHaveText('+84981234567');
  await extensionPanel.openHistory();
  await extensionPanel.export('xlsx');
  expect(backend.uniqueRevealCount()).toBe(1);
});
```

- [ ] **Step 2: Run E2E and observe missing fixtures**

Run: `pnpm build && pnpm test:e2e`

Expected: FAIL because Playwright config/fixtures are missing.

- [ ] **Step 3: Implement deterministic extension fixture**

Launch Chromium persistent context with the built extension flags, derive extension ID from its service worker URL, serve a local mock backend matching the exact API schema, route the Facebook URL to the static profile fixture, and cover login/refresh, redeem, explicit lookup, repeat-without-charge, history isolation response, CSV/XLSX download initiation, navigation stale-response protection, and logout.

```ts
const context = await chromium.launchPersistentContext(profileDir, {
  headless: false,
  args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`],
});
const worker = context.serviceWorkers()[0] ?? await context.waitForEvent('serviceworker');
const extensionId = new URL(worker.url()).host;
```

- [ ] **Step 4: Run final extension verification**

Run: `pnpm test --run && pnpm compile && pnpm build && pnpm test:e2e && pnpm zip`

Expected: all commands PASS and the Chrome release zip is created without `.env`, tests, or source credentials.

- [ ] **Step 5: Commit**

```bash
git add playwright.config.ts tests/e2e README.md package.json pnpm-lock.yaml
git commit -m "test: verify lead finder extension workflow"
```
