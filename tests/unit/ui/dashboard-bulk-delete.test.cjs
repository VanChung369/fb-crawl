const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup(status, body) {
  const button = { disabled: false }, alerts = [], calls = [], toasts = [];
  let reloads = 0;
  const context = vm.createContext({
    document: { addEventListener() {}, getElementById() { return button; } }, window: {},
    confirm: () => true, alert: message => alerts.push(message), console: { warn() {} },
    fetch: async (url, options) => { calls.push({ url, options }); return { ok: status === 200, status, json: async () => body }; },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../../src/fb_ui/js/app.js'), 'utf8'), context);
  const state = { baseUrl: 'https://api.example.test', apiKey: 'test-key',
    leadsPagination: { pageIndex: 4, cursorHistory: ['old'], nextCursor: 'old' },
    showToast: message => toasts.push(message), loadLeadsData: async () => { reloads++; }, setOnlineStatus() {},
    fetchApi: vm.runInContext('DashboardApp.prototype.fetchApi', context),
  };
  const run = vm.runInContext('DashboardApp.prototype.deleteUsersWithoutPhone', context);
  return { run: () => run.call(state), button, alerts, calls, toasts, state, reloads: () => reloads };
}

test('shows the database failure reason without reporting success and restores the delete button', async () => {
  const message = 'Bulk deletion timed out. No data was deleted.';
  const fixture = setup(503, { code: 'database_timeout', message });
  await fixture.run();
  assert.deepEqual(fixture.alerts, [message]);
  assert.equal(fixture.toasts.length, 0);
  assert.equal(fixture.reloads(), 0);
  assert.equal(fixture.state.leadsPagination.pageIndex, 4);
  assert.equal(fixture.button.disabled, false);
});

test('sends one authenticated confirmed delete despite a second click and refreshes only on success', async () => {
  const fixture = setup(200, { status: 'success', deleted_count: 12 });
  const pending = fixture.run();
  await fixture.run();
  await pending;
  assert.equal(fixture.calls.length, 1);
  assert.equal(fixture.calls[0].options.method, 'DELETE');
  assert.equal(fixture.calls[0].options.headers['X-API-Key'], 'test-key');
  assert.equal(new URL(fixture.calls[0].url).searchParams.get('confirm'), 'true');
  assert.equal(fixture.reloads(), 1);
  assert.equal(fixture.toasts.length, 1);
  assert.equal(fixture.button.disabled, false);
});
