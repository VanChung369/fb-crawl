const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => {resolve=done;reject=fail;});
  return {promise,resolve,reject};
}

function setup() {
  const controls = {
    'maintenance-enabled': {checked:true},
    'maintenance-message': {value:'Initial message'},
    'maintenance-preset': {value:'custom'},
    'maintenance-preview-message': {},
    'maintenance-preview': {setAttribute(name,value){this[name]=value;}},
    'maintenance-preview-hint': {},
    'maintenance-character-count': {},
    'maintenance-state': {},
    'maintenance-error': {hidden:true},
    'btn-save-maintenance': {disabled:false},
  };
  const context = vm.createContext({document:{addEventListener(){},getElementById:id=>controls[id]},window:{}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../../src/fb_ui/js/app.js'),'utf8'),context);
  const app = Object.create(vm.runInContext('DashboardApp.prototype',context));
  Object.assign(app,{maintenanceDirty:true,maintenanceRevision:0,maintenancePresets:{},showToast(){}});
  return {app,controls};
}

test('preserves changes typed during an in-flight maintenance save', async () => {
  const {app,controls} = setup();
  const pending = deferred();
  app.fetchProductApi = () => pending.promise;
  const saved = app.saveMaintenance();
  controls['maintenance-message'].value = 'New unsaved message';
  controls['maintenance-enabled'].checked = false;
  app.maintenanceDirty = true;
  pending.resolve({enabled:true,message:'Initial message'});
  await saved;
  assert.equal(controls['maintenance-message'].value,'New unsaved message');
  assert.equal(controls['maintenance-enabled'].checked,false);
  assert.equal(app.maintenanceDirty,true);
  assert.equal(controls['maintenance-state'].textContent,'Đang bảo trì');
});

test('refresh does not unlock the save button or overwrite status while saving', async () => {
  const {app,controls} = setup();
  const pending = deferred();
  app.fetchProductApi = url => url.includes('/admin/') ? pending.promise : Promise.resolve({enabled:false,message:'Old message'});
  const saved = app.saveMaintenance();
  await app.loadMaintenance();
  const disabledDuringSave = controls['btn-save-maintenance'].disabled;
  pending.resolve({enabled:true,message:'Initial message'});
  await saved;
  assert.equal(disabledDuringSave,true);
});

test('a stale refresh error cannot replace a successful maintenance save', async () => {
  const {app,controls} = setup();
  const refresh = deferred();
  app.fetchProductApi = url => url.includes('/admin/') ? Promise.resolve({enabled:true,message:'Initial message'}) : refresh.promise;
  const loaded = app.loadMaintenance();
  await app.saveMaintenance();
  refresh.reject(new Error('Old failed refresh'));
  await loaded;
  assert.equal(controls['maintenance-error'].hidden,true);
});

test('previews the draft switch state and message length without changing saved status',()=>{
  const {app,controls}=setup();
  controls['maintenance-state'].textContent='Hoạt động bình thường';
  app.previewMaintenance();
  assert.equal(controls['maintenance-preview']['data-enabled'],'true');
  assert.equal(controls['maintenance-character-count'].textContent,'15/1000');
  assert.equal(controls['maintenance-state'].textContent,'Hoạt động bình thường');
  controls['maintenance-enabled'].checked=false;
  app.previewMaintenance();
  assert.equal(controls['maintenance-preview']['data-enabled'],'false');
  assert.match(controls['maintenance-preview-hint'].textContent,/chưa bật/);
});
