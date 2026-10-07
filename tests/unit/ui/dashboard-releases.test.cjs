const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup() {
  const controls = Object.fromEntries(['release-active','release-announce','release-enforce','release-minimum','release-message','release-preset','release-preview','release-state','release-error','btn-save-release-policy','btn-upload-release'].map(id=>[id,{value:'',checked:false,disabled:false,hidden:true}]));
  const context=vm.createContext({document:{addEventListener(){},getElementById:id=>controls[id]},window:{}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../../src/fb_ui/js/app.js'),'utf8'),context);
  const app=Object.create(vm.runInContext('DashboardApp.prototype',context));
  Object.assign(app,{releaseDirty:false,releaseBusy:false,releaseRevision:0,releasePresets:{},releases:[],showToast(){}});
  return {app,controls};
}

test('shows a version-substituted preview using plain text',()=>{
  const {app,controls}=setup();
  app.releases=[{id:'one',version:'0.3.0'}];
  controls['release-active'].value='one';
  controls['release-message'].value='<script>alert(1)</script> {version}';
  app.previewReleasePolicy();
  assert.equal(controls['release-preview'].textContent,'<script>alert(1)</script> 0.3.0');
});

test('retains edits made while saving the policy',async()=>{
  const {app,controls}=setup();
  controls['release-message'].value='Before';
  let resolve;
  app.fetchProductApi=()=>new Promise(done=>{resolve=done;});
  app.renderReleasePolicy=()=>{};
  app.renderReleaseList=()=>{};
  const saved=app.saveReleasePolicy();
  controls['release-message'].value='After';
  resolve({});
  await saved;
  assert.equal(controls['release-message'].value,'After');
  assert.equal(app.releaseDirty,true);
  assert.equal(app.releaseBusy,false);
});
