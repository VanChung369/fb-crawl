const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup(status = 200) {
  const calls = [], alerts = [], downloads = [];
  const link = { click() { downloads.push(this.download); }, remove() {} };
  class TestURL extends URL {
    static createObjectURL() { return 'blob:test-export'; }
    static revokeObjectURL() {}
  }
  const context = vm.createContext({
    document: { addEventListener() {}, createElement() { return link; }, body: { append() {} } },
    window: {}, URL: TestURL, URLSearchParams, console,
    setTimeout(callback) { callback(); }, alert(message) { alerts.push(message); },
    async fetch(url, options) { calls.push({url, options}); return {ok: status === 200, status, async blob() { return {}; }}; },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../../src/fb_ui/js/app.js'), 'utf8'), context);
  const run = vm.runInContext('DashboardApp.prototype.exportLeads', context);
  const state = { baseUrl: 'https://api.example.test', apiKey: 'test-key',
    leadsPagination: {filters: {q: 'Nguyễn', has_phone: 'false'}},
    btnExportCsv: {disabled: false}, btnExportJson: {disabled: false}, showToast() {} };
  return {run: format => run.call(state, format), calls, alerts, downloads, state};
}

test('exports with the API key and active filters and creates a download', async () => {
  const fixture = setup();
  await fixture.run('csv');
  const {url, options} = fixture.calls[0];
  assert.equal(options.headers['X-API-Key'], 'test-key');
  assert.equal(new URL(url).searchParams.get('q'), 'Nguyễn');
  assert.equal(new URL(url).searchParams.get('has_phone'), 'false');
  assert.deepEqual(fixture.downloads, ['users_export.csv']);
  assert.equal(fixture.state.exportingLeads, false);
  assert.equal(fixture.state.btnExportCsv.disabled, false);
});

test('does not save an error response as a file and re-enables controls', async () => {
  const fixture = setup(401);
  await fixture.run('json');
  assert.equal(fixture.downloads.length, 0);
  assert.equal(fixture.alerts.length, 1);
  assert.equal(fixture.state.btnExportJson.disabled, false);
});
