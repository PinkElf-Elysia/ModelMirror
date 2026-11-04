import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir, mkdtemp, readFile, writeFile, rename, readdir, copyFile} from 'node:fs/promises';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {join, dirname} from 'node:path';
import {AuthoringStore} from './store.mjs';
import {blankDocument, canonical, sha256} from './contracts.mjs';
import {openPublishedRuntime, requirePublishedRuntime, runtimeReference} from './runtime-binding.mjs';

const root = fileURLToPath(new URL('../.rpg04-work/authoring-b6a-tests/binding/', import.meta.url));
await mkdir(root, {recursive: true});
const pngA = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII=', 'base64');
const pngB = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
function document(name = '灯塔档案') {
  const d = blankDocument(); d.metadata.name = name; d.metadata.audience = 'female';
  d.sources.system = '  作者原文\r\n\r\n<archive>🐟</archive>\n';
  d.sources.prefix = '前置\n'; d.sources.suffix = '\n补充要求  ';
  d.sources.startupHtml = '<details class="different-class"><summary>开局</summary><img src="asset://scene"></details>';
  d.sources.outputCss = '.different-class {color:rebeccapurple}'; d.settings.defaultMemory = 6;
  d.model = {selection:{catalogId:'catalog-reference', modelId:'unqualified-model'}, parameters:{temperature:.7, topP:.8, maxOutputTokens:16384}};
  return d;
}
async function fixture(t) {
  const directory = await mkdtemp(join(root, 'run-')), store = await AuthoringStore.open(directory);
  t.after(() => store.close());
  const doc = document(), made = await store.create({operationId:'create', expectedRevision:null, document:doc});
  await store.putAsset(made.workId, {operationId:'image', expectedRevision:0, alias:'scene', fileName:'scene.png', mediaType:'image/png', bytes:pngA});
  const published = await store.publish(made.workId, {operationId:'publish', expectedRevision:1});
  const ref = {workId:made.workId, versionId:published.result.versionId};
  return {store, directory, doc, ref, runtime:await openPublishedRuntime(store, ref)};
}
const reject = (action, code) => assert.rejects(action, error => error.code === code);

test('任意作品绑定精确封存版本；配置与开局源码逐字保留，不自动授权/选择/分配额度', async t => {
  const f = await fixture(t), before = await readFile(join(f.directory, 'works', f.ref.workId + '.json'));
  const openingText = '  姓名：青川\r\n\r\n观测员 🐟\n';
  const binding = f.runtime.bindSession({sessionId:'session-one', openingText});
  assert.equal(binding.openingText, openingText); assert.equal(binding.openingHash, sha256(openingText));
  assert.equal(binding.versionId, f.ref.versionId); assert.equal(f.runtime.configuration().document.sources.system, f.doc.sources.system);
  assert.deepEqual(f.runtime.configuration().document.model, f.doc.model);
  assert.equal(f.runtime.readiness.runtimeReady, false); assert.equal(f.runtime.descriptor.runtimeReady, false);
  assert.ok(f.runtime.readiness.remaining.includes('controlled-model-parameters'));
  assert.ok(f.runtime.readiness.required.includes('history-window-suggestion'));
  assert.equal(requirePublishedRuntime(f.runtime), f.runtime);
  for (const key of ['budget','ledger','grants','installed','modelState','requests','historyWindow']) assert.equal(Object.hasOwn(binding, key), false);
  assert.equal(Object.isFrozen(binding), true); assert.equal(Object.isFrozen(f.runtime.descriptor.artifactHashes), true);
  const exposed = f.runtime.configuration(); exposed.document.sources.system = 'changed';
  assert.equal(f.runtime.configuration().document.sources.system, f.doc.sources.system);
  assert.deepEqual(await readFile(join(f.directory,'works',f.ref.workId+'.json')), before);
});

test('必须指定本作品封存版本；不接受草稿/latest、跨作品版本或外部配置对象', async t => {
  const f = await fixture(t);
  for (const versionId of [null, 'latest', '', '../version.json']) await reject(() => openPublishedRuntime(f.store,{...f.ref,versionId}), 'AUTHORING_VERSION_ID_INVALID');
  await reject(() => openPublishedRuntime(f.store,{...f.ref,versionId:undefined}), 'AUTHORING_JSON_INVALID');
  assert.throws(() => runtimeReference({...f.ref, system:'injected'}), {code:'AUTHORING_UNKNOWN_FIELD'});
  await reject(() => openPublishedRuntime({},f.ref), 'AUTHORING_RUNTIME_STORE_REQUIRED');
  assert.throws(() => requirePublishedRuntime({...f.runtime}), {code:'AUTHORING_RUNTIME_HOST_REQUIRED'});
  await reject(() => openPublishedRuntime(f.store,{...f.ref,versionId:'version-'+'f'.repeat(64)}), 'AUTHORING_VERSION_NOT_FOUND');
  const other = await f.store.create({operationId:'second',expectedRevision:null,document:blankDocument()});
  await reject(() => openPublishedRuntime(f.store,{workId:other.workId,versionId:f.ref.versionId}), 'AUTHORING_VERSION_NOT_FOUND');
});

test('绑定篡改、陌生字段、非法开局及不同会话不能混用', async t => {
  const f = await fixture(t), b = f.runtime.bindSession({sessionId:'original',openingText:'开局'});
  for (const [field,value] of [['sessionId','other'],['openingText','不同开局'],['runtimeHash','0'.repeat(64)],['versionId','version-'+'0'.repeat(64)],['resourceHash','1'.repeat(64)],['format','other']]) {
    assert.throws(() => f.runtime.requireBinding({...b,[field]:value}), {code:'AUTHORING_RUNTIME_BINDING_MISMATCH'});
  }
  assert.throws(() => f.runtime.requireBinding({...b,quota:100}), {code:'AUTHORING_UNKNOWN_FIELD'});
  for (const text of ['', ' ', '字'.repeat(200001)]) assert.throws(() => f.runtime.bindSession({sessionId:'one',openingText:text}), {code:'AUTHORING_RUNTIME_OPENING_INVALID'});
  assert.throws(() => f.runtime.bindSession({sessionId:'one',openingText:'\ud800'}), {code:'AUTHORING_UNICODE_INVALID'});
  assert.throws(() => f.runtime.bindSession({sessionId:'../escape',openingText:'开局'}), {code:'AUTHORING_RUNTIME_SESSION_ID_INVALID'});
  const s = {id:'original',revision:0,binding:b,completedTurns:0,busy:false,pending:false};
  await reject(() => f.runtime.projectSession({...s,id:'other'}), 'AUTHORING_RUNTIME_SESSION_INVALID');
  await reject(() => f.runtime.projectSession({...s,revision:-1}), 'AUTHORING_RUNTIME_SESSION_INVALID');
  await reject(() => f.runtime.projectSession({...s,completedTurns:.5}), 'AUTHORING_RUNTIME_SESSION_INVALID');
  await reject(() => f.runtime.projectSession({...s,pending:'no'}), 'AUTHORING_RUNTIME_SESSION_INVALID');
  await reject(() => f.runtime.projectSession({...s,history:[]}), 'AUTHORING_UNKNOWN_FIELD');
});

test('草稿修改、同别名替换和新版本不会改变既有绑定及媒体字节', async t => {
  const f = await fixture(t), b = f.runtime.bindSession({sessionId:'one',openingText:'开局'});
  const d2 = structuredClone(f.doc); d2.sources.system += '未来信息';
  await f.store.save(f.ref.workId,{operationId:'save',expectedRevision:2,document:d2});
  await f.store.putAsset(f.ref.workId,{operationId:'replace',expectedRevision:3,alias:'scene',fileName:'new.png',mediaType:'image/png',bytes:pngB});
  const v2 = await f.store.publish(f.ref.workId,{operationId:'publish2',expectedRevision:4});
  assert.equal(await f.runtime.verify(), true); assert.deepEqual(f.runtime.requireBinding(b), b);
  assert.deepEqual((await f.runtime.asset('scene')).bytes, pngA);
  assert.equal(f.runtime.configuration().document.sources.system, f.doc.sources.system);
  const newer = await openPublishedRuntime(f.store,{...f.ref,versionId:v2.result.versionId});
  assert.notEqual(newer.hash,f.runtime.hash); assert.deepEqual((await newer.asset('scene')).bytes,pngB);
  assert.throws(() => newer.requireBinding(b), {code:'AUTHORING_RUNTIME_BINDING_MISMATCH'});
  await f.store.removeAsset(f.ref.workId,{operationId:'remove-draft-ref',expectedRevision:5,alias:'scene'});
  assert.equal(await f.runtime.verify(),true);
  await reject(() => f.runtime.asset('not-present'), 'AUTHORING_ASSET_NOT_FOUND');
});

test('绑定序列化与存储重开恢复相同身份；不重建任务或增加任何账本', async t => {
  const f = await fixture(t), binding = f.runtime.bindSession({sessionId:'persisted',openingText:'玩家原文'});
  const file = join(f.directory,'binding-evidence.json'); await writeFile(file,canonical(binding));
  const before = (await readdir(f.directory)).sort(); await f.store.close();
  const reopened = await AuthoringStore.open(f.directory); t.after(() => reopened.close());
  const runtime = await openPublishedRuntime(reopened,f.ref);
  const restored = JSON.parse(await readFile(file,'utf8'));
  assert.equal(runtime.hash,f.runtime.hash); assert.deepEqual(runtime.requireBinding(restored),binding);
  assert.deepEqual((await readdir(f.directory)).sort(),before);
  assert.equal(runtime.readiness.runtimeReady,false);
});

test('复制及完整包导入获得独立作品绑定，不借用原版本或原会话权限', async t => {
  const f = await fixture(t), old = f.runtime.bindSession({sessionId:'parent',openingText:'开局'});
  const copy = await f.store.copy(f.ref.workId,{operationId:'copy',expectedRevision:2,name:'灯塔副本'});
  const pack = await f.store.exportPackage(f.ref.workId,f.ref.versionId), check = await f.store.inspectImport(pack);
  const imported = await f.store.importPackage({operationId:'import',workId:null,expectedRevision:null,expectedImportHash:check.payloadHash,payload:pack});
  for (const [index, made] of [copy,imported].entries()) {
    const work = await f.store.read(made.workId); assert.equal(work.versions.length,0);
    const v = await f.store.publish(made.workId,{operationId:'seal-'+index,expectedRevision:0});
    const runtime = await openPublishedRuntime(f.store,{workId:made.workId,versionId:v.result.versionId});
    const fresh = runtime.bindSession({sessionId:'copy-'+index,openingText:'开局'});
    assert.notEqual(fresh.resourceHash,old.resourceHash); assert.notEqual(fresh.workId,old.workId);
    assert.throws(() => runtime.requireBinding(old),{code:'AUTHORING_RUNTIME_BINDING_MISMATCH'});
    for(const key of ['budget','grants','requests']) assert.equal(Object.hasOwn(work,key),false);
  }
});

test('封存版本与资源损坏或缺失明确失败，不回退到新草稿资源', async t => {
  const a = await fixture(t), asset = a.runtime.configuration().assets[0];
  await writeFile(join(a.directory,'blobs',asset.sha256),pngB);
  await reject(() => a.runtime.verify(),'AUTHORING_ASSET_CORRUPT');
  const b = await fixture(t), hash = b.runtime.configuration().assets[0].sha256;
  await rename(join(b.directory,'blobs',hash),join(b.directory,'blobs',hash+'.missing-evidence'));
  await reject(() => openPublishedRuntime(b.store,b.ref),'AUTHORING_ASSET_MISSING');
  const c = await fixture(t), file = join(c.directory,'versions',c.ref.versionId.slice(8)+'.json');
  const bad = JSON.parse(await readFile(file,'utf8')); bad.configuration.document.sources.system = 'changed';
  await writeFile(file,canonical(bad));
  await reject(() => c.runtime.verify(),'AUTHORING_VERSION_CORRUPT');
});


test('异步校验前固定会话投影，调用方后续修改不能混入同一份授权依据', async t => {
  const f = await fixture(t), s = {id:'original',revision:2,completedTurns:1,busy:false,pending:false,
    binding:f.runtime.bindSession({sessionId:'original',openingText:'开局'})};
  const result = f.runtime.projectSession(s);
  s.id='changed';s.revision=99;s.pending=true;s.binding=null;
  const projected=await result;
  assert.equal(projected.id,'original');assert.equal(projected.revision,2);assert.equal(projected.pending,false);
});

test('宿主源码漂移要求重启；新实现不接受旧绑定（只改隔离源码副本）', async t => {
  const f = await fixture(t), source = join(f.directory,'source-copy');
  for(const path of Object.keys(f.runtime.descriptor.artifactHashes)){
    const target=join(source,path);await mkdir(dirname(target),{recursive:true});
    await copyFile(new URL('../'+path,import.meta.url),target);
  }
  const module=await import(pathToFileURL(join(source,'authoring/runtime-binding.mjs')));
  const {AuthoringStore:CopyStore}=await import(pathToFileURL(join(source,'authoring/store.mjs')));
  const store=await CopyStore.open(join(f.directory,'source-test-store'));t.after(()=>store.close());
  const doc=blankDocument();doc.metadata.name='Source check';doc.metadata.audience='male';doc.sources.system='text';
  const made=await store.create({operationId:'source-create',expectedRevision:null,document:doc});
  const version=await store.publish(made.workId,{operationId:'source-publish',expectedRevision:0});
  const ref={workId:made.workId,versionId:version.result.versionId};
  const before=await module.openPublishedRuntime(store,ref), binding=before.bindSession({sessionId:'one',openingText:'开局'});
  const altered=join(source,'plugins/branch-save.mjs');await writeFile(altered,(await readFile(altered,'utf8'))+'\n// isolated revision\n');
  await reject(()=>before.verify(),'AUTHORING_RUNTIME_FILES_CHANGED_RESTART_REQUIRED');
  const reloaded=await import(pathToFileURL(join(source,'authoring/runtime-binding.mjs')).href+'?restart=1');
  const after=await reloaded.openPublishedRuntime(store,ref);
  assert.notEqual(after.hash,before.hash);
  assert.throws(()=>after.requireBinding(binding),{code:'AUTHORING_RUNTIME_BINDING_MISMATCH'});
});


test('整份版本校验返回独立快照，每次重读资源并拒绝迟到损坏', async t => {
  const f=await fixture(t), expected=await f.store.exportConfiguration(f.ref.workId,f.ref.versionId);
  const snapshot=await f.store.verifyConfiguration(f.ref.workId,f.ref.versionId);
  assert.deepEqual(snapshot,expected);snapshot.document.sources.system='外部改写';
  assert.deepEqual(await f.store.verifyConfiguration(f.ref.workId,f.ref.versionId),expected);
  const asset=expected.assets[0],path=join(f.directory,'blobs',asset.sha256),original=await readFile(path);
  await writeFile(path,Buffer.from('changed'));
  await reject(()=>f.store.verifyConfiguration(f.ref.workId,f.ref.versionId),'AUTHORING_ASSET_CORRUPT');
  await writeFile(path,original);assert.equal(await f.runtime.verify(),true);
  await rename(path,path+'.missing');
  await reject(()=>f.store.verifyConfiguration(f.ref.workId,f.ref.versionId),'AUTHORING_ASSET_MISSING');
});
