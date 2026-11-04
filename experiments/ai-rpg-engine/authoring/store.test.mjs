import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, writeFile, readdir, unlink, symlink, link} from 'node:fs/promises';
import {join, resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';
import {AuthoringStore} from './store.mjs';
import {LANGUAGES, AUDIENCES, tagIssue, tagCatalog, metadataIssues} from './metadata.mjs';
import {
  FORMAT, LIMITS, blankDocument, canonical, parseJson, sha256, validateDocument, validateManifest, inspectMedia,
} from './contracts.mjs';

const parent = resolve(fileURLToPath(new URL('../.rpg04-work/authoring-b1/', import.meta.url)));
await mkdir(parent, {recursive: true});
const runRoot = await mkdtemp(join(parent, 'run-'));
const pngA = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII=', 'base64');
const pngB = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
const document = () => {
  const value = blankDocument();
  value.metadata.name = '雾港来信'; value.metadata.audience = 'male';
  value.sources.system = '作者原文\r\n\r\n  <system-data>不要重排空白 🐟</system-data>\n';
  value.sources.suffix = '补充要求：\n每轮只包裹一次。';
  value.sources.startupHtml = '<details><summary>开局</summary><input value="林舟"></details>';
  return value;
};
const errorCode = code => error => error?.code === code;
const rejected = (action, code) => assert.rejects(async () => action(), errorCode(code));
async function fixture(t, options = {}) {
  const directory = await mkdtemp(join(runRoot, 'store-'));
  const store = await AuthoringStore.open(directory, options);
  t.after(() => store.close());
  return {store, directory};
}
const create = (store, operationId = 'create', doc = document()) => store.create({operationId, expectedRevision: null, document: doc});
const put = (store, workId, revision, operationId = 'asset', bytes = pngA, alias = 'cover') =>
  store.putAsset(workId, {operationId, expectedRevision: revision, alias, fileName: '封面.png', mediaType: 'image/png', bytes});
const save = (store, workId, revision, doc, operationId = 'save') =>
  store.save(workId, {operationId, expectedRevision: revision, document: doc});
const publish = (store, workId, revision, operationId = 'publish') => store.publish(workId, {operationId, expectedRevision: revision});

test('源码逐字保存、重启恢复；空白草稿可保存但不能封存版本', async t => {
  const {store, directory} = await fixture(t);
  const original = document(), made = await create(store, 'source', original);
  original.sources.system = '调用后修改不得污染已接受的输入';
  assert.equal((await store.read(made.workId)).draft.sources.system, document().sources.system);
  const blank = await create(store, 'blank', blankDocument());
  assert.deepEqual((await store.validate(blank.workId)).issues.map(item => item.code), ['AUTHORING_NAME_REQUIRED', 'AUTHORING_SYSTEM_REQUIRED', 'AUTHORING_AUDIENCE_REQUIRED']);
  await rejected(() => publish(store, blank.workId, 0), 'AUTHORING_PUBLICATION_INVALID');
  await store.close();
  const reopened = await AuthoringStore.open(directory); t.after(() => reopened.close());
  assert.deepEqual((await reopened.read(made.workId)).draft, document());
  assert.equal((await reopened.list()).length, 2);
  assert.equal((await reopened.recover(made.workId, 'source')).status, 'committed');
});

test('资源替换与草稿修改不会改变已封存版本；复制相互隔离', async t => {
  const {store} = await fixture(t), made = await create(store), id = made.workId;
  await put(store, id, 0);
  const d1 = document(); d1.settings.media.cover = 'cover'; d1.sources.startupHtml += '<img src="asset://cover">';
  await save(store, id, 1, d1);
  const first = await publish(store, id, 2);
  const v1 = await store.exportConfiguration(id, first.result.versionId);
  assert.equal(first.result.runtimeReady, false);
  const d2 = structuredClone(d1); d2.sources.system += '\n新版本正文';
  await save(store, id, 3, d2, 'save2');
  await put(store, id, 4, 'replace', pngB);
  const second = await publish(store, id, 5, 'publish2');
  assert.notEqual(first.result.versionId, second.result.versionId);
  assert.deepEqual(await store.exportConfiguration(id, first.result.versionId), v1);
  assert.deepEqual((await store.getAsset(id, 'cover', first.result.versionId)).bytes, pngA);
  assert.deepEqual((await store.getAsset(id, 'cover')).bytes, pngB);
  const copied = await store.copy(id, {operationId: 'copy', expectedRevision: 6, name: '副本'});
  const copy = await store.read(copied.workId);
  assert.equal(copy.revision, 0); assert.equal(copy.versions.length, 0);
  assert.equal(copy.draft.metadata.name, '副本'); assert.equal(copy.draft.sources.system, d2.sources.system);
  assert.equal(copy.origin.revision, 6);
  for (const forbidden of ['budget', 'ledger', 'grants', 'sessions', 'provider']) assert.equal(Object.hasOwn(copy, forbidden), false);
  await put(store, id, 6, 'replace3', pngA);
  assert.deepEqual((await store.getAsset(copied.workId, 'cover')).bytes, pngB);
  await store.removeAsset(id, {operationId: 'remove', expectedRevision: 7, alias: 'cover'});
  assert.equal((await store.validate(id)).valid, false);
  assert.deepEqual((await store.getAsset(id, 'cover', first.result.versionId)).bytes, pngA);
});

test('完整作品包进入新目录，文本、资源和配置往返一致；配置单独导入报告缺失资源', async t => {
  const a = await fixture(t), b = await fixture(t), made = await create(a.store);
  await put(a.store, made.workId, 0);
  const config = await a.store.exportConfiguration(made.workId);
  const missing = await b.store.inspectImport(config);
  assert.equal(missing.valid, false); assert.equal(missing.issues[0].code, 'AUTHORING_ASSET_MISSING');
  assert.equal((await b.store.list()).length, 0);
  const pack = await a.store.exportPackage(made.workId);
  const preflight = await b.store.inspectImport(JSON.stringify(pack, null, 2));
  assert.equal(preflight.valid, true); assert.equal(preflight.assets, 1);
  const imported = await b.store.importPackage({operationId: 'import', workId: null, expectedRevision: null, expectedImportHash: preflight.payloadHash, payload: JSON.stringify(pack)});
  assert.notEqual(imported.workId, made.workId);
  assert.deepEqual(await b.store.exportConfiguration(imported.workId), config);
  assert.deepEqual((await b.store.getAsset(imported.workId, 'cover')).bytes, pngA);
  assert.equal(canonical(await b.store.exportPackage(imported.workId)), canonical(pack));
  assert.equal((await b.store.inspectImport(config)).valid, true);
});

test('配置 JSON 保留新增功能的数据而不执行脚本、正则、概率或模型参数', async t => {
  const {store} = await fixture(t), d = document();
  d.sources.startupJs = 'throw new Error("不应被存储层执行");';
  d.worldbook.categories = [{id: 'places', name: '地点'}];
  d.worldbook.entries = [{id: 'lighthouse', name: '灯塔', enabled: true, categoryId: 'places', roles: ['user', 'assistant'], rule: 'and', regex: true, keywords: ['(a+)+$'], targets: ['system', 'suffix'], order: 12, depth: 20, chance: 25.5, mode: 'weighted', body: '', contents: [{text: 'A', weight: 1}, {text: 'B', weight: 2}]}];
  d.enhancements.shortcuts = [{id: 'look', name: '观察', text: '观察四周'}];
  d.enhancements.displayRules = [{id: 'display', name: '展示', enabled: true, pattern: '不执行', flags: 'g', replacement: '<details>$1</details>'}];
  d.enhancements.dialogue.presets = [{id: 'first', title: '来信', messages: [{user: '问', assistant: '答'}], openings: ['进入']}];
  d.model = {selection: {catalogId: 'controlled', modelId: 'example-model'}, parameters: {temperature: 0.7, topP: 0.8, maxOutputTokens: 16384}};
  const made = await create(store, 'features', d);
  assert.deepEqual((await store.exportConfiguration(made.workId)).document, d);
  assert.equal((await publish(store, made.workId, 0)).result.runtimeReady, false);
});

test('创建、保存、上传、封存和复制的重复操作只返回原回执', async t => {
  const {store} = await fixture(t), made = await create(store);
  assert.equal((await create(store)).replayed, true);
  const uploaded = await put(store, made.workId, 0);
  assert.equal((await put(store, made.workId, 0)).requestHash, uploaded.requestHash);
  const saved = await save(store, made.workId, 1, document());
  assert.equal((await save(store, made.workId, 1, document())).revision, saved.revision);
  const published = await publish(store, made.workId, 2);
  assert.equal((await publish(store, made.workId, 2)).result.versionId, published.result.versionId);
  const request = {operationId: 'copy', expectedRevision: 3, name: '副本'};
  const copy = await store.copy(made.workId, request);
  await save(store, made.workId, 3, document(), 'later');
  assert.equal((await store.copy(made.workId, request)).workId, copy.workId);
  assert.equal((await store.read(made.workId)).versions.length, 1);
  await rejected(() => save(store, made.workId, 4, {...document(), sources: {...document().sources, suffix: '变更'}}, 'save'), 'AUTHORING_OPERATION_COLLISION');
  await rejected(() => put(store, made.workId, 0, 'asset', pngB), 'AUTHORING_OPERATION_COLLISION');
  assert.equal((await store.list()).length, 2);
});

test('多标签保存竞争只有一份成功，过期 revision 不覆盖；排队输入立即冻结', async t => {
  const {store} = await fixture(t), made = await create(store), a = document(), b = document();
  a.metadata.name = '甲'; b.metadata.name = '乙';
  const saving = save(store, made.workId, 0, a, 'tab-a');
  a.metadata.name = '外部变异';
  const results = await Promise.allSettled([saving, save(store, made.workId, 0, b, 'tab-b')]);
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
  assert.equal(results.find(result => result.status === 'rejected').reason.code, 'AUTHORING_REVISION_CONFLICT');
  assert.equal((await store.read(made.workId)).draft.metadata.name, '甲');
});

test('坏包、未知字段、预检查后改包均不写入，替换导入保护旧版本与旧草稿', async t => {
  const {store, directory} = await fixture(t), made = await create(store);
  await put(store, made.workId, 0);
  const version = await publish(store, made.workId, 1);
  const pack = await store.exportPackage(made.workId), preview = await store.inspectImport(pack);
  const bad = structuredClone(pack); bad.configuration.document.metadata.surprise = true;
  const report = await store.inspectImport(bad);
  assert.equal(report.valid, false); assert.equal(report.issues[0].code, 'AUTHORING_UNKNOWN_FIELD');
  assert.equal(report.issues[0].path, '/metadata/surprise');
  await rejected(() => store.importPackage({operationId: 'changed', workId: made.workId, expectedRevision: 2, expectedImportHash: preview.payloadHash, payload: bad}), 'AUTHORING_IMPORT_PREVIEW_CHANGED');
  const corrupt = structuredClone(pack); corrupt.blobs[0].base64 = pngB.toString('base64');
  const before = await store.read(made.workId), blobsBefore = await readdir(join(directory, 'blobs'));
  await rejected(() => store.importPackage({operationId: 'corrupt', workId: made.workId, expectedRevision: 2, expectedImportHash: sha256(canonical(corrupt)), payload: corrupt}), 'AUTHORING_PACKAGE_HASH');
  assert.deepEqual(await store.read(made.workId), before);
  assert.deepEqual(await readdir(join(directory, 'blobs')), blobsBefore);
  const modified = structuredClone(pack); modified.configuration.document.metadata.name = '导入后的草稿';
  const checked = await store.inspectImport(modified);
  const request = {operationId: 'replace-import', workId: made.workId, expectedRevision: 2, expectedImportHash: checked.payloadHash, payload: modified};
  await store.importPackage(request);
  assert.equal((await store.importPackage(request)).replayed, true);
  assert.equal((await store.read(made.workId)).draft.metadata.name, '导入后的草稿');
  assert.equal((await store.exportConfiguration(made.workId, version.result.versionId)).document.metadata.name, '雾港来信');
});

test('提交前落盘故障保留旧草稿，同 ID 可明确重试', async t => {
  let failOnce = true;
  const {store} = await fixture(t, {fault: async (phase, operation) => {
    if (phase === 'beforeCommit' && operation.operationId === 'write-failure' && failOnce) { failOnce = false; throw Object.assign(new Error('disk full'), {code: 'ENOSPC'}); }
  }});
  const made = await create(store), d = document(); d.metadata.name = '新草稿';
  await rejected(() => save(store, made.workId, 0, d, 'write-failure'), 'AUTHORING_WRITE_FAILED');
  const state = await store.read(made.workId);
  assert.equal(state.revision, 0); assert.equal(state.pending, null); assert.equal(state.draft.metadata.name, '雾港来信');
  assert.equal((await store.recover(made.workId, 'write-failure')).status, 'not_found');
  await save(store, made.workId, 0, d, 'write-failure');
  assert.equal((await store.read(made.workId)).revision, 1);
});

test('提交后回执丢失阻断新写入；重启只恢复原操作，不重放或重复创建版本', async t => {
  const {store, directory} = await fixture(t, {fault: async (phase, operation) => {
    if (phase === 'afterCommit' && operation.operationId === 'lost-receipt') throw new Error('connection lost after rename');
  }});
  const made = await create(store);
  await rejected(() => publish(store, made.workId, 0, 'lost-receipt'), 'AUTHORING_RESULT_UNKNOWN');
  await rejected(() => save(store, made.workId, 1, document(), 'new-id'), 'AUTHORING_OPERATION_PENDING');
  assert.equal((await store.read(made.workId)).versions.length, 1);
  await store.close();
  const reopened = await AuthoringStore.open(directory); t.after(() => reopened.close());
  await rejected(() => create(reopened, 'unrelated'), 'AUTHORING_OPERATION_PENDING');
  const recovered = await reopened.recover(made.workId, 'lost-receipt');
  assert.equal(recovered.status, 'committed'); assert.equal(recovered.receipt.revision, 1);
  const replayed = await publish(reopened, made.workId, 0, 'lost-receipt');
  assert.equal(replayed.replayed, true); assert.equal((await reopened.read(made.workId)).versions.length, 1);
});

test('真实子进程在提交前退出：不自动接管/重放，明确恢复后报告未提交', async t => {
  const {store, directory} = await fixture(t), made = await create(store);
  await store.close();
  const moduleUrl = pathToFileURL(fileURLToPath(new URL('./store.mjs', import.meta.url))).href;
  const child = [
    'const {AuthoringStore} = await import(' + JSON.stringify(moduleUrl) + ');',
    'const store = await AuthoringStore.open(' + JSON.stringify(directory) + ', {fault: async (phase) => { if (phase === "beforeCommit") process.exit(0); }});',
    'const work = await store.read(' + JSON.stringify(made.workId) + ');',
    'await store.save(work.id, {operationId:"crash", expectedRevision:0, document:work.draft});',
  ].join('\n');
  const run = spawnSync(process.execPath, ['--input-type=module', '-e', child], {encoding: 'utf8', timeout: 10000, windowsHide: true});
  assert.equal(run.status, 0, run.stderr);
  await rejected(() => AuthoringStore.open(directory), 'AUTHORING_OWNER_RECOVERY_REQUIRED');
  const reopened = await AuthoringStore.open(directory, {recoverDeadOwner: true}); t.after(() => reopened.close());
  await rejected(() => save(reopened, made.workId, 0, document()), 'AUTHORING_OPERATION_PENDING');
  assert.equal((await reopened.recover(made.workId, 'crash')).status, 'not_committed');
  assert.equal((await reopened.read(made.workId)).revision, 0);
  await save(reopened, made.workId, 0, document(), 'crash');
  assert.equal((await reopened.read(made.workId)).revision, 1);
});

test('同目录单写入所有者、关闭后拒绝操作；不能把旧数据目录当作新作品库', async t => {
  const {store, directory} = await fixture(t);
  await rejected(() => AuthoringStore.open(directory), 'AUTHORING_STORE_BUSY');
  await rejected(() => AuthoringStore.open(directory, {recoverDeadOwner: true}), 'AUTHORING_STORE_BUSY');
  await create(store); await store.close();
  await rejected(() => store.list(), 'AUTHORING_STORE_CLOSED');
  const foreign = await mkdtemp(join(runRoot, 'foreign-'));
  await writeFile(join(foreign, 'ledger.json'), '{"budget":"do-not-touch"}');
  await rejected(() => AuthoringStore.open(foreign), 'AUTHORING_ROOT_NOT_EMPTY');
  assert.equal(await readFile(join(foreign, 'ledger.json'), 'utf8'), '{"budget":"do-not-touch"}');
  assert.deepEqual(await readdir(foreign), ['ledger.json']);
});

test('缺失/损坏媒体阻止版本封存，保留原资源声明；未引用版本对象不被公开', async t => {
  const {store, directory} = await fixture(t), made = await create(store);
  const upload = await put(store, made.workId, 0), blob = join(directory, 'blobs', upload.result.asset.sha256);
  await writeFile(blob, pngB);
  assert.equal((await store.validate(made.workId)).issues[0].code, 'AUTHORING_ASSET_CORRUPT');
  await rejected(() => publish(store, made.workId, 1), 'AUTHORING_ASSET_CORRUPT');
  await unlink(blob);
  await rejected(() => store.exportPackage(made.workId), 'AUTHORING_ASSET_MISSING');
  assert.equal((await store.read(made.workId)).revision, 1);
  await rejected(() => store.exportConfiguration(made.workId, 'version-' + 'a'.repeat(64)), 'AUTHORING_VERSION_NOT_FOUND');
});

test('资源路径、文件类型和容量严格拒绝；资源内容去重且别名独立', async t => {
  const {store, directory} = await fixture(t), made = await create(store);
  await rejected(() => put(store, made.workId, 0, 'path', pngA, '../cover'), 'AUTHORING_ALIAS_INVALID');
  await rejected(() => store.putAsset(made.workId, {operationId: 'name', expectedRevision: 0, alias: 'cover', fileName: '..\\outside.png', mediaType: 'image/png', bytes: pngA}), 'AUTHORING_FILENAME_INVALID');
  await rejected(() => store.putAsset(made.workId, {operationId: 'svg', expectedRevision: 0, alias: 'cover', fileName: 'a.svg', mediaType: 'image/svg+xml', bytes: Buffer.from('<svg onload="alert(1)"/>')}), 'AUTHORING_ASSET_TYPE');
  assert.throws(() => inspectMedia(Buffer.alloc(LIMITS.imageBytes + 1), 'image/png'), errorCode('AUTHORING_ASSET_SIZE'));
  await put(store, made.workId, 0); await put(store, made.workId, 1, 'alias', pngA, 'another');
  assert.equal((await readdir(join(directory, 'blobs'))).length, 1);
  const pack = await store.exportPackage(made.workId);
  assert.equal(pack.configuration.assets.length, 2); assert.equal(pack.blobs.length, 1);
});

test('拒绝符号链接根目录/资源与硬链接资源，不读取目标内容', async t => {
  const target = await mkdtemp(join(runRoot, 'target-')), linkedRoot = join(runRoot, 'linked-' + Date.now());
  await symlink(target, linkedRoot, process.platform === 'win32' ? 'junction' : 'dir');
  await rejected(() => AuthoringStore.open(linkedRoot), 'AUTHORING_PATH_UNSAFE');
  const {store, directory} = await fixture(t), made = await create(store), asset = await put(store, made.workId, 0);
  const blob = join(directory, 'blobs', asset.result.asset.sha256), outside = join(target, 'owned-fixture.png');
  await writeFile(outside, pngA); await unlink(blob); await link(outside, blob);
  await rejected(() => store.getAsset(made.workId, 'cover'), 'AUTHORING_PATH_UNSAFE');
  await unlink(blob);
  // Junction the blob directory; directory check happens before any asset lookup.
  const moved = join(directory, 'blobs-fixture');
  const {rename} = await import('node:fs/promises'); await rename(join(directory, 'blobs'), moved);
  await symlink(target, join(directory, 'blobs'), process.platform === 'win32' ? 'junction' : 'dir');
  await rejected(() => store.getAsset(made.workId, 'cover'), 'AUTHORING_PATH_UNSAFE');
  assert.deepEqual(await readFile(outside), pngA);
});

test('坏校验和不会被默认为空作品，严格 JSON 不丢弃重复或未知字段', async t => {
  const {store, directory} = await fixture(t), made = await create(store);
  const file = join(directory, 'works', made.workId + '.json'), envelope = JSON.parse(await readFile(file, 'utf8'));
  envelope.data.draft.metadata.name = '篡改';
  await writeFile(file, JSON.stringify(envelope));
  await rejected(() => store.read(made.workId), 'AUTHORING_STORE_CORRUPT');
  await rejected(() => save(store, made.workId, 0, document()), 'AUTHORING_STORE_CORRUPT');
  assert.throws(() => parseJson('{"a":1,"\\u0061":2}'), errorCode('AUTHORING_JSON_DUPLICATE_KEY'));
  assert.throws(() => parseJson('{"__proto__":{}}'), errorCode('AUTHORING_JSON_KEY'));
  assert.throws(() => parseJson('{"a":1,}'), errorCode('AUTHORING_JSON_SYNTAX'));
  assert.throws(() => parseJson('"\\ud800"'), errorCode('AUTHORING_UNICODE_INVALID'));
  assert.throws(() => validateDocument({...document(), budget: 100}), errorCode('AUTHORING_UNKNOWN_FIELD'));
  assert.throws(() => validateDocument({...document(), model: {selection: null, parameters: null, apiKey: 'test-not-a-secret'}}), errorCode('AUTHORING_UNKNOWN_FIELD'));
  assert.equal(parseJson(' { "a" : [true, null, -12, "\\\\"] } ').a[2], -12);
});

test('世界书零权重草稿可保存，封存明确诊断；非法数值与额外权限字段不能存入', async t => {
  const {store} = await fixture(t), d = document();
  const entry = {id: 'entry', name: '', enabled: true, categoryId: null, roles: ['user'], rule: 'or', regex: false, keywords: ['雾港'], targets: ['system'], order: 0, depth: 0, chance: 100, mode: 'weighted', body: '', contents: [{text: '候选', weight: 0}]};
  d.worldbook.entries = [entry]; const made = await create(store, 'weights', d);
  assert.equal((await store.validate(made.workId)).issues[0].code, 'AUTHORING_WEIGHT_TOTAL_ZERO');
  await rejected(() => publish(store, made.workId, 0), 'AUTHORING_PUBLICATION_INVALID');
  for (const depth of [-1, 21, 1.5, '1']) {
    const invalid = structuredClone(d); invalid.worldbook.entries[0].depth = depth;
    assert.throws(() => validateDocument(invalid), errorCode('AUTHORING_NUMBER_INVALID'));
  }
  entry.contents[0].weight = NaN;
  assert.throws(() => validateDocument(d), errorCode('AUTHORING_JSON_INVALID'));
  const manifest = Array.from({length: LIMITS.assets + 1}, (_, i) => ({alias: 'asset' + i, fileName: 'a.png', mediaType: 'image/png', size: pngA.length, sha256: sha256(pngA)}));
  assert.throws(() => validateManifest(manifest), errorCode('AUTHORING_ARRAY_INVALID'));
});

test('音频角色与图片角色分别校验；包不接受多余或缺失 blob', async t => {
  const {store} = await fixture(t), made = await create(store);
  const audio = Buffer.concat([Buffer.from('ID3'), Buffer.alloc(10)]);
  await store.putAsset(made.workId, {operationId: 'audio', expectedRevision: 0, alias: 'bgm', fileName: '音乐.mp3', mediaType: 'audio/mpeg', bytes: audio});
  const d = document(); d.settings.media.cover = 'bgm'; await save(store, made.workId, 1, d);
  assert.equal((await store.validate(made.workId)).issues[0].code, 'AUTHORING_ASSET_ROLE');
  const pack = await store.exportPackage(made.workId);
  const missing = structuredClone(pack); missing.blobs = [];
  assert.equal((await store.inspectImport(missing)).issues[0].code, 'AUTHORING_PACKAGE_INCOMPLETE');
  const extra = structuredClone(pack); extra.blobs.push({sha256: sha256(pngA), base64: pngA.toString('base64')});
  assert.equal((await store.inspectImport(extra)).issues[0].code, 'AUTHORING_PACKAGE_BLOBS');
});

test.after(async () => {
  await writeFile(join(runRoot, 'scope.json'), JSON.stringify({providerCalls: 0, sharedStackChanges: false, dataRoot: runRoot, description: 'B1-A isolated storage test artifacts; no production data'}, null, 2));
});


test('版本封存提交失败仅留未引用对象；原操作明确重试只公开一个版本', async t => {
  let failOnce = true;
  const {store, directory} = await fixture(t, {fault: async (phase, operation) => {
    if (phase === 'beforeCommit' && operation.operationId === 'version-failure' && failOnce) { failOnce = false; throw new Error('simulated I/O failure'); }
  }});
  const made = await create(store);
  await rejected(() => publish(store, made.workId, 0, 'version-failure'), 'AUTHORING_WRITE_FAILED');
  assert.equal((await store.read(made.workId)).versions.length, 0);
  const staged = await readdir(join(directory, 'versions'));
  assert.equal(staged.length, 1);
  await rejected(() => store.exportConfiguration(made.workId, 'version-' + staged[0].slice(0, -5)), 'AUTHORING_VERSION_NOT_FOUND');
  await publish(store, made.workId, 0, 'version-failure');
  assert.equal((await store.read(made.workId)).versions.length, 1);
  assert.equal((await store.read(made.workId)).revision, 1);
});

test('损坏版本拒绝导出，不静默改为最新草稿；回执恢复返回原版本', async t => {
  const {store, directory} = await fixture(t), made = await create(store);
  const receipt = await publish(store, made.workId, 0);
  await writeFile(join(directory, 'versions', receipt.result.sha256 + '.json'), '{"format":"damaged"}');
  await rejected(() => store.exportPackage(made.workId, receipt.result.versionId), 'AUTHORING_VERSION_CORRUPT');
  assert.equal((await store.recover(made.workId, 'publish')).receipt.result.versionId, receipt.result.versionId);
  assert.equal((await store.read(made.workId)).versions.length, 1);
});

test('新建作品提交后丢失回执也可恢复；所有权丢失时不能继续写', async t => {
  const {store, directory} = await fixture(t, {fault: async phase => { if (phase === 'afterCommit') throw new Error('lost'); }});
  let caught;
  try { await create(store, 'lost-create'); } catch (error) { caught = error; }
  assert.equal(caught.code, 'AUTHORING_RESULT_UNKNOWN');
  const recovered = await store.recover(caught.details.workId, 'lost-create');
  assert.equal(recovered.status, 'committed'); assert.equal(recovered.receipt.revision, 0);
  assert.equal((await create(store, 'lost-create')).replayed, true);
  const ownerFile = join(directory, 'owner.json'), original = await readFile(ownerFile);
  await writeFile(ownerFile, JSON.stringify({pid: process.pid, token: 'different-owner'}));
  try { await rejected(() => save(store, caught.details.workId, 0, document()), 'AUTHORING_OWNER_LOST'); }
  finally { await writeFile(ownerFile, original); }
});

test('非法导入不会部分登记合法资源；完整包拒绝额外字段与伪造能力', async t => {
  const a = await fixture(t), b = await fixture(t), made = await create(a.store);
  await put(a.store, made.workId, 0);
  await put(a.store, made.workId, 1, 'second', pngB, 'second');
  const pack = await a.store.exportPackage(made.workId);
  pack.blobs[1].base64 = Buffer.from('not a valid image').toString('base64');
  await rejected(() => b.store.importPackage({operationId: 'partial', workId: null, expectedRevision: null, expectedImportHash: sha256(canonical(pack)), payload: pack}), 'AUTHORING_PACKAGE_HASH');
  assert.equal((await b.store.list()).length, 0);
  assert.deepEqual(await readdir(join(b.directory, 'blobs')), []);
  const config = await a.store.exportConfiguration(made.workId);
  config.capabilities.runtimeReady = true;
  assert.equal((await b.store.inspectImport(config)).issues[0].code, 'AUTHORING_CAPABILITIES_INVALID');
  assert.equal((await b.store.inspectImport({...pack, grants: ['all']})).issues[0].code, 'AUTHORING_UNKNOWN_FIELD');
});

test('拒绝隐式丢失的数据属性、非法 JSON 值和无效标识', async t => {
  const d = document(); Object.defineProperty(d, 'hidden', {value: 1});
  assert.throws(() => validateDocument(d), errorCode('AUTHORING_JSON_KEY'));
  assert.throws(() => canonical({[Symbol('unknown')]: true}), errorCode('AUTHORING_JSON_KEY'));
  assert.throws(() => canonical([, 1]), errorCode('AUTHORING_JSON_INVALID'));
  assert.throws(() => canonical({a: Infinity}), errorCode('AUTHORING_JSON_INVALID'));
  assert.throws(() => canonical({get a() { throw new Error('must not execute'); }}), errorCode('AUTHORING_JSON_KEY'));
  const {store} = await fixture(t), made = await create(store);
  await rejected(() => store.read('../outside'), 'AUTHORING_WORK_ID_INVALID');
  await rejected(() => store.read({toString() { return made.workId; }}), 'AUTHORING_WORK_ID_INVALID');
  await rejected(() => save(store, made.workId, -1, document()), 'AUTHORING_REVISION_INVALID');
});


test('标识、资源别名和正则标志末尾的换行不能被校验放行', async t => {
  const {store} = await fixture(t), made = await create(store);
  await rejected(() => create(store, 'invalid-id\n'), 'AUTHORING_OPERATION_ID_INVALID');
  await rejected(() => store.read(made.workId + '\n'), 'AUTHORING_WORK_ID_INVALID');
  await rejected(() => put(store, made.workId, 0, 'bad-alias', pngA, 'cover\n'), 'AUTHORING_ALIAS_INVALID');
  const d = document();
  d.enhancements.displayRules = [{id: 'display', name: '', enabled: true, pattern: 'a', flags: 'g\n', replacement: 'b'}];
  assert.throws(() => validateDocument(d), errorCode('AUTHORING_REGEX_FLAGS'));
});


test('真实子进程提交后退出：保留已提交版本，显式恢复后同 ID 不重复封存', async t => {
  const {store, directory} = await fixture(t), made = await create(store);
  await store.close();
  const moduleUrl = new URL('./store.mjs', import.meta.url).href;
  const child = [
    'const {AuthoringStore} = await import(' + JSON.stringify(moduleUrl) + ');',
    'const store = await AuthoringStore.open(' + JSON.stringify(directory) + ', {fault: async (phase) => { if (phase === "afterCommit") process.exit(0); }});',
    'await store.publish(' + JSON.stringify(made.workId) + ', {operationId:"crash-published", expectedRevision:0});',
  ].join('\n');
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', child], {encoding: 'utf8', timeout: 10000, windowsHide: true});
  assert.equal(result.status, 0, result.stderr);
  const reopened = await AuthoringStore.open(directory, {recoverDeadOwner: true}); t.after(() => reopened.close());
  assert.equal((await reopened.read(made.workId)).pending.operationId, 'crash-published');
  const recovered = await reopened.recover(made.workId, 'crash-published');
  assert.equal(recovered.status, 'committed');
  assert.equal((await publish(reopened, made.workId, 0, 'crash-published')).replayed, true);
  assert.equal((await reopened.read(made.workId)).versions.length, 1);
});


test('B2-B 五种语言和两类受众逐项保存、封存、复制与跨库导入；源码保持逐字一致', async t => {
  const a=await fixture(t), b=await fixture(t);
  assert.deepEqual(LANGUAGES.map(([,label])=>label),['English','简体中文','繁體中文','日本語','한국어']);
  assert.deepEqual(AUDIENCES.map(([,label])=>label),['男性向','女性向']);
  for (const [language] of LANGUAGES) for (const [audience] of AUDIENCES) {
    const doc=document(); doc.metadata={...doc.metadata,language,audience,tags:['科幻','自定义','UI样例']};
    const made=await create(a.store,language+'-'+audience,doc);
    const updated=structuredClone(doc);updated.metadata.tags.push('银河邮局');
    const receipt=await save(a.store,made.workId,0,updated,'save-'+language+'-'+audience);
    assert.equal((await save(a.store,made.workId,0,updated,'save-'+language+'-'+audience)).replayed,true);
    const v=await publish(a.store,made.workId,receipt.revision,'publish-'+language+'-'+audience);
    assert.deepEqual((await a.store.exportConfiguration(made.workId,v.result.versionId)).document,updated);
    const pack=await a.store.exportPackage(made.workId), preview=await b.store.inspectImport(pack);
    assert.equal(preview.valid,true);assert.deepEqual(preview.publicationIssues,[]);
    const imported=await b.store.importPackage({operationId:'import-'+language+'-'+audience,expectedRevision:null,workId:null,payload:pack,expectedImportHash:preview.payloadHash});
    assert.deepEqual((await b.store.read(imported.workId)).draft,updated);
    const copied=await a.store.copy(made.workId,{operationId:'copy-'+language+'-'+audience,expectedRevision:2,name:'副本'});
    assert.deepEqual((await a.store.read(copied.workId)).draft.metadata,{...updated.metadata,name:'副本'});
  }
});

test('B2-B 旧资料可读、可复制导出但不会被自动映射；导入和新封存要求人工修正', async t => {
  const {store,directory}=await fixture(t),made=await create(store),id=made.workId;
  await publish(store,id,0);
  const oldVersion=(await store.read(id)).versions[0].id;
  const originalVersion=await store.exportConfiguration(id,oldVersion);
  await store.close();
  // A hash-valid pre-B2-B record, equivalent to the old accepted host's durable format.
  const path=join(directory,'works',id+'.json'), envelope=JSON.parse(await readFile(path,'utf8'));
  envelope.data.draft.metadata={...envelope.data.draft.metadata,language:'legacy-language',audience:'general',tags:['科幻','日常','历史','探索','自定义','悬疑','冒险','经营','第九项','乱伦']};
  envelope.hash=sha256(canonical(envelope.data));await writeFile(path,canonical(envelope)+'\n');
  const reopened=await AuthoringStore.open(directory);t.after(()=>reopened.close());
  const old=(await reopened.read(id)).draft;
  assert.deepEqual(old.metadata,envelope.data.draft.metadata);
  assert.deepEqual((await reopened.exportPackage(id)).configuration.document,old);
  assert.deepEqual(await reopened.exportConfiguration(id,oldVersion),originalVersion);
  assert.equal((await reopened.list())[0].tags.includes('乱伦'),false);
  await rejected(()=>save(reopened,id,1,old),'AUTHORING_LANGUAGE_INVALID');
  await rejected(()=>publish(reopened,id,1,'new-publish'),'AUTHORING_PUBLICATION_INVALID');
  const pack=await reopened.exportPackage(id),preview=await reopened.inspectImport(pack);
  assert.equal(preview.valid,false);assert.equal(preview.issues[0].code,'AUTHORING_LANGUAGE_INVALID');
  const copied=await reopened.copy(id,{operationId:'legacy-copy',expectedRevision:1,name:'旧资料副本'});
  assert.deepEqual((await reopened.read(copied.workId)).draft.metadata,{...old.metadata,name:'旧资料副本'});
  const fixed=structuredClone(old);fixed.metadata={...fixed.metadata,language:'zh-CN',audience:'female',tags:['科幻']};
  await save(reopened,id,1,fixed,'explicit-fix');
  assert.deepEqual((await reopened.read(id)).draft.metadata,fixed.metadata);
  assert.equal((await reopened.read(copied.workId)).draft.metadata.audience,'general');
});

test('B2-B 八标签边界、Unicode 重复及不当内容拦截；普通题材不会被当作不当标签', async t => {
  const {store}=await fixture(t),made=await create(store),id=made.workId;
  const safe=['科幻','校园','百合','纯爱','LGBTQ','男性向','女性向','治愈'];
  const doc=document();doc.metadata.tags=safe;
  await save(store,id,0,doc);
  assert.deepEqual((await store.read(id)).draft.metadata.tags,safe);
  for(const [tags,code] of [
    [[...safe,'第九项'],'AUTHORING_TAG_LIMIT'],
    [['RPG','ｒｐｇ'],'AUTHORING_TAG_DUPLICATE'],
    [['AI 作家','ai作家'],'AUTHORING_TAG_DUPLICATE'],
    [[''],'AUTHORING_TAG_TEXT'],[['分'.repeat(81)],'AUTHORING_TEXT_INVALID'],
    [['科幻🎉'],'AUTHORING_TAG_CHARACTERS'],[['科幻,历史'],'AUTHORING_TAG_CHARACTERS'],
    [['科\u200b幻'],'AUTHORING_TAG_CHARACTERS'],[['科\n幻'],'AUTHORING_TAG_CHARACTERS'],
    [['乱伦'],'AUTHORING_TAG_RESTRICTED'],[['亂倫'],'AUTHORING_TAG_RESTRICTED'],
    [['乱 伦'],'AUTHORING_TAG_RESTRICTED'],[['乱\u200b伦'],'AUTHORING_TAG_RESTRICTED'],
    [['Adult Sex'],'AUTHORING_TAG_RESTRICTED'],[['ＮＳＦＷ'],'AUTHORING_TAG_RESTRICTED'],[['Ｒ１８'],'AUTHORING_TAG_RESTRICTED'],
  ]) {
    const bad=structuredClone(doc);bad.metadata.tags=tags;
    await rejected(()=>save(store,id,1,bad,'invalid-'+sha256(JSON.stringify(tags)).slice(0,16)),code);
    assert.equal((await store.read(id)).revision,1);
  }
  for(const tag of [...safe,'Sussex','Country','星际科幻与时间旅行的故事'])assert.equal(tagIssue(tag),null);
  assert.deepEqual(tagCatalog(['乱伦','科幻','ｒｐｇ','RPG']).slice(-1),['ｒｐｇ']);
  assert.equal(tagIssue('分'.repeat(81)),'AUTHORING_TAG_TEXT');
  assert.equal(metadataIssues({...doc.metadata,audience:''}).length,0);
  assert.equal(metadataIssues({...doc.metadata,audience:''},{publication:true})[0].code,'AUTHORING_AUDIENCE_REQUIRED');
});

test('B2-B 无效元数据不能从导入绕过或部分覆盖；草稿空受众可保存', async t => {
  const {store}=await fixture(t),made=await create(store),id=made.workId;
  const pack=await store.exportPackage(id);
  for (const patch of [{language:'fr'},{audience:'adults'},{tags:['乱伦']},{tags:Array.from({length:9},(_,i)=>'分类'+i)}]) {
    const bad=structuredClone(pack);Object.assign(bad.configuration.document.metadata,patch);
    assert.equal((await store.inspectImport(bad)).valid,false);
    await assert.rejects(async ()=>store.importPackage({operationId:'bad-'+sha256(JSON.stringify(patch)).slice(0,16),expectedRevision:0,workId:id,payload:bad,expectedImportHash:sha256(canonical(bad))}));
    assert.deepEqual((await store.read(id)).draft,document());assert.equal((await store.read(id)).revision,0);
  }
  const draft=document();draft.metadata.audience='';await save(store,id,0,draft);
  await rejected(()=>publish(store,id,1),'AUTHORING_PUBLICATION_INVALID');
});
