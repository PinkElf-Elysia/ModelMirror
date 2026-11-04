import test from 'node:test';
import {request as httpRequest} from 'node:http';
import assert from 'node:assert/strict';
import {mkdir, mkdtemp, readFile} from 'node:fs/promises';
import {resolve, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startAuthoringServer} from './server.mjs';
import {blankDocument, sha256} from './contracts.mjs';

const parent = fileURLToPath(new URL('../.rpg04-work/authoring-b1b/', import.meta.url));
await mkdir(parent, {recursive: true});
const runRoot = await mkdtemp(join(parent, 'http-'));
const api = '/rpg-app/api/authoring';
const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII=';
const otherPng = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';
const document = () => { const doc = blankDocument(); doc.metadata.name = '接口雾港'; doc.metadata.audience = 'female'; doc.sources.system = '作者原文\r\n\r\n  保留空白 🐟'; return doc; };
async function fixture(t, options = {}) {
  const directory = await mkdtemp(join(runRoot, 'store-'));
  const host = await startAuthoringServer({directory, ...options});
  t.after(() => host.close());
  const request = async (path, body, headers = {}) => {
    const response = await fetch(host.origin + api + path, body === undefined ? {headers} : {
      method: 'POST', headers: {'Content-Type': 'application/json', Origin: host.origin, 'X-Authoring-Request': '1', ...headers},
      body: typeof body === 'string' ? body : JSON.stringify(body),
    });
    return {response, value: await response.json()};
  };
  return {host, directory, request};
}
const make = request => request('/works', {operationId: 'create', expectedRevision: null, document: document()});
const asset = (request, id, revision = 0, base64 = png, operationId = 'asset') => request('/works/' + id + '/assets', {
  operationId, expectedRevision: revision, alias: 'cover', fileName: '封面.png', mediaType: 'image/png', base64,
});

test('普通导航不需要 Origin；界面只连接自身、固定静态路径，不执行作者源码', async t => {
  const {host, request} = await fixture(t);
  const response = await fetch(host.origin + '/');
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /B1 作品与资源/);
  assert.match(html, /browser\.mjs/);
  assert.match(response.headers.get('content-security-policy'), /connect-src 'self'/);
  assert.ok(response.headers.get('content-security-policy').includes('frame-src '+host.origin+'/rpg-app/api/authoring/presentations/frames/'));
  assert.doesNotMatch(response.headers.get('content-security-policy'), /script-src[^;]*unsafe-inline/);
  assert.equal((await fetch(host.origin + '/store.mjs')).status, 404);
  assert.equal((await fetch(host.origin + '/%2e%2e%2fstore.mjs')).status, 422);
  const state = (await request('/state')).value;
  assert.equal(state.runtimeReady, false);
  assert.deepEqual(state.works, []);
  assert.deepEqual(state.blank, blankDocument());
});

test('跨站、错误 Host、无 Origin 写入、表单和未知字段均被拒绝', async t => {
  const {host, request} = await fixture(t);
  const body = {operationId: 'create', expectedRevision: null, document: document()};
  assert.equal((await request('/works', body, {Origin: 'https://outside.invalid'})).response.status, 403);
  assert.equal((await request('/works', body, {'X-Authoring-Request': ''})).response.status, 403);
  assert.equal((await fetch(host.origin + api + '/works', {method:'POST', body:JSON.stringify(body), headers:{'Content-Type':'application/json'}})).status, 403);
  const badHost = await new Promise((ok, no) => { const req = httpRequest(host.origin + '/', {headers:{Host:'rebinding.invalid'}}, res => { res.resume(); res.on('end', () => ok(res.statusCode)); }); req.on('error', no); req.end(); });
  assert.equal(badHost, 403);
  assert.equal((await request('/state', undefined, {'Sec-Fetch-Site':'cross-site'})).response.status, 403);
  assert.equal((await request('/works', body, {'Content-Type':'text/plain'})).response.status, 422);
  assert.equal((await request('/works', {...body, modelUrl:'https://outside.invalid'})).value.error.code, 'AUTHORING_UNKNOWN_FIELD');
  assert.equal((await request('/works', '{"operationId":"a","operationId":"b"}')).value.error.code, 'AUTHORING_JSON_DUPLICATE_KEY');
  assert.deepEqual((await request('/state')).value.works, []);
});

test('HTTP 创建、保存、原始文本恢复；重复操作幂等且多标签过期修改不覆盖', async t => {
  const {request} = await fixture(t);
  const receipt = (await make(request)).value, id = receipt.workId;
  const doc = document(); doc.sources.suffix = '\n 用户后置词 \r\n';
  const body = {operationId:'save-1',expectedRevision:0,document:doc};
  const saved = (await request('/works/' + id + '/save', body)).value;
  assert.equal(saved.revision, 1);
  assert.equal((await request('/works/' + id + '/save', body)).value.replayed, true);
  const conflict = await request('/works/' + id + '/save', {...body,operationId:'other-tab'});
  assert.equal(conflict.response.status, 409);
  assert.equal(conflict.value.error.code, 'AUTHORING_REVISION_CONFLICT');
  const work = (await request('/works/' + id)).value;
  assert.deepEqual(work.draft, doc);
  assert.equal(Object.hasOwn(work, 'operations'), false);
  assert.equal((await request('/works/' + id + '?ignored=1')).response.status, 422);
});

test('图库字节、别名替换、旧版本资源和缺失引用检查；完整包可进入空数据目录', async t => {
  const {request, host} = await fixture(t), fresh = await fixture(t);
  const id = (await make(request)).value.workId;
  assert.equal((await asset(request, id)).value.revision, 1);
  const doc = document(); doc.settings.media.cover = 'cover'; doc.sources.introduction = '<img src="asset://cover">';
  await request('/works/' + id + '/save', {operationId:'set-cover',expectedRevision:1,document:doc});
  const published = (await request('/works/' + id + '/publish', {operationId:'publish',expectedRevision:2})).value;
  assert.equal(published.result.runtimeReady, false);
  const version = published.result.versionId;
  await asset(request, id, 3, otherPng, 'replace');
  const url = host.origin + api + '/works/' + id + '/assets/cover';
  const original = await fetch(url + '?version=' + version);
  assert.deepEqual(Buffer.from(await original.arrayBuffer()), Buffer.from(png, 'base64'));
  assert.equal(original.headers.get('content-type'), 'image/png');
  assert.match(original.headers.get('content-security-policy'), /sandbox/);
  assert.equal((await fetch(url + '?hash=' + sha256(Buffer.from(png, 'base64')))).status, 409);
  const exported = (await request('/works/' + id + '/export?kind=package&version=' + version)).value;
  const inspected = (await fresh.request('/imports/inspect', {payload:exported})).value;
  assert.equal(inspected.valid, true);
  const imported = (await fresh.request('/imports', {operationId:'import',expectedRevision:null,workId:null,payload:exported,expectedImportHash:inspected.payloadHash})).value;
  assert.deepEqual((await fresh.request('/works/' + imported.workId + '/export?kind=package')).value, exported);
  const bytes = await fetch(fresh.host.origin + api + '/works/' + imported.workId + '/assets/cover');
  assert.deepEqual(Buffer.from(await bytes.arrayBuffer()), Buffer.from(png, 'base64'));
  const removed = (await request('/works/' + id + '/assets/remove', {operationId:'remove',expectedRevision:4,alias:'cover'})).value;
  assert.equal(removed.revision, 5);
  assert.equal((await request('/works/' + id + '/validation')).value.valid, false);
  assert.equal((await fetch(url + '?version=' + version)).status, 200);
});

test('非法媒体、坏包、未确认/变更后的导入、无资源配置均不能替换作品', async t => {
  const {request} = await fixture(t), fresh = await fixture(t);
  const id = (await make(request)).value.workId;
  const invalid = await asset(request, id, 0, Buffer.from('<script>bad()</script>').toString('base64'));
  assert.equal(invalid.response.status, 422);
  assert.equal((await asset(request, id, 0, png + '!')).value.error.code, 'AUTHORING_BASE64_INVALID');
  await asset(request, id);
  const config = (await request('/works/' + id + '/export')).value;
  assert.equal((await fresh.request('/imports/inspect', {payload:config})).value.valid, false);
  const pack = (await request('/works/' + id + '/export?kind=package')).value;
  const report = (await request('/imports/inspect', {payload:pack})).value;
  assert.equal(report.valid, true);
  pack.configuration.document.metadata.name = '预检后篡改';
  const changed = await request('/imports', {operationId:'changed',expectedRevision:1,workId:id,payload:pack,expectedImportHash:report.payloadHash});
  assert.equal(changed.value.error.code, 'AUTHORING_IMPORT_PREVIEW_CHANGED');
  pack.extra = true;
  assert.equal((await request('/imports/inspect', {payload:pack})).value.valid, false);
  assert.equal((await request('/works/' + id)).value.draft.metadata.name, '接口雾港');
});

test('复制只复制草稿与资源，版本和调用权限不继承；空白发布报告缺失字段', async t => {
  const {request} = await fixture(t);
  const id = (await make(request)).value.workId;
  await request('/works/' + id + '/publish', {operationId:'publish',expectedRevision:0});
  const copied = (await request('/works/' + id + '/copy', {operationId:'copy',expectedRevision:1,name:'副本'})).value;
  const work = (await request('/works/' + copied.workId)).value;
  assert.equal(work.draft.metadata.name,'副本');
  assert.deepEqual(work.versions,[]);
  assert.equal(Object.hasOwn(work, 'budget'), false);
  const blank = (await request('/works', {operationId:'blank',expectedRevision:null,document:blankDocument()})).value;
  const result = await request('/works/' + blank.workId + '/publish', {operationId:'bad-publish',expectedRevision:0});
  assert.equal(result.response.status,422);
  assert.deepEqual(result.value.error.details.issues.map(issue=>issue.code),['AUTHORING_NAME_REQUIRED','AUTHORING_SYSTEM_REQUIRED','AUTHORING_AUDIENCE_REQUIRED']);
});

test('落盘失败、提交后未知和 HTTP 恢复只核对原 ID，没有重放', async t => {
  let mode = '';
  const {request, directory} = await fixture(t, {fault: phase => {
    if (mode === phase) { mode = ''; throw new Error('private-local-path-must-not-leak'); }
  }});
  const id = (await make(request)).value.workId;
  mode = 'beforeCommit';
  const failed = await request('/works/' + id + '/save', {operationId:'before',expectedRevision:0,document:document()});
  assert.equal(failed.value.error.code,'AUTHORING_WRITE_FAILED');
  assert.equal(JSON.stringify(failed.value).includes('private-local'),false);
  assert.equal((await request('/works/' + id)).value.revision,0);
  mode = 'afterCommit';
  const unknown = await request('/works/' + id + '/save', {operationId:'after',expectedRevision:0,document:document()});
  assert.equal(unknown.value.error.code,'AUTHORING_RESULT_UNKNOWN');
  assert.equal((await request('/works/' + id + '/save', {operationId:'new',expectedRevision:1,document:document()})).value.error.code,'AUTHORING_OPERATION_PENDING');
  const recovered = (await request('/operations/recover', {workId:id,operationId:'after'})).value;
  assert.equal(recovered.status,'committed');
  assert.equal(recovered.receipt.revision,1);
  assert.equal((await request('/operations/recover', {workId:id,operationId:'after'})).value.status,'committed');
  const record = JSON.parse(await readFile(join(directory,'works',id + '.json'),'utf8'));
  assert.equal(record.data.operations.length,2);
});

test('首次创建回复未知可用原操作恢复身份，服务关闭重开不重放', async t => {
  let once = true;
  const {host, directory, request} = await fixture(t, {fault: phase => {
    if (phase === 'afterCommit' && once) { once = false; throw new Error('lost response'); }
  }});
  const unknown = await make(request);
  assert.equal(unknown.value.error.code,'AUTHORING_RESULT_UNKNOWN');
  const recovered = (await request('/operations/recover', {workId:null,operationId:'create'})).value;
  assert.equal(recovered.status,'committed');
  const id = recovered.receipt.workId;
  const record = await readFile(join(directory,'works',id + '.json'),'utf8');
  await host.close();
  const reopened = await startAuthoringServer({directory});
  t.after(() => reopened.close());
  assert.equal(await readFile(join(directory,'works',id + '.json'),'utf8'),record);
  const work = await (await fetch(reopened.origin + api + '/works/' + id)).json();
  assert.equal(work.revision,0);
});

test('配置编辑预检严格解析全文并由宿主重算能力声明，预检不写入', async t => {
  const {request} = await fixture(t);
  const id = (await make(request)).value.workId;
  const doc = document(); doc.sources.startupJs = 'throw new Error("must not execute");';
  const prepared = (await request('/configurations/prepare', {document:JSON.stringify(doc),assets:[]})).value;
  assert.equal(prepared.capabilities.runtimeReady,false);
  assert.deepEqual(prepared.document,doc);
  assert.equal((await request('/imports/inspect',{payload:prepared})).value.valid,true);
  assert.equal((await request('/works/' + id)).value.revision,0);
  assert.equal((await request('/configurations/prepare',{document:'{"format":1,"format":2}',assets:[]})).value.error.code,'AUTHORING_JSON_DUPLICATE_KEY');
  assert.equal((await request('/configurations/prepare',{document:JSON.stringify({...doc,extra:true}),assets:[]})).value.error.code,'AUTHORING_UNKNOWN_FIELD');
  assert.equal((await request('/configurations/prepare',{document:JSON.stringify(doc),assets:[],runtimeReady:true})).value.error.code,'AUTHORING_UNKNOWN_FIELD');
});

test('B2-B HTTP 保存与导入使用同一元数据约束，目录只返回安全标签', async t => {
  const {host,request}=await fixture(t);
  const module=await fetch(host.origin+'/metadata.mjs');assert.equal(module.status,200);assert.match(await module.text(),/LANGUAGES/);
  const created=(await make(request)).value,id=created.workId;
  const doc=document();doc.metadata.language='ko';doc.metadata.audience='male';doc.metadata.tags=['科幻','星际科幻','银河邮局'];
  assert.equal((await request('/works/'+id+'/save',{operationId:'metadata-save',expectedRevision:0,document:doc})).response.status,200);
  assert.deepEqual((await request('/state')).value.works[0].tags,doc.metadata.tags);
  assert.deepEqual((await request('/works/'+id)).value.draft.metadata,doc.metadata);
  for(const patch of [{language:'de'},{audience:'general'},{tags:['乱伦']},{tags:['RPG','ＲＰＧ']}]){
    const changed=structuredClone(doc);Object.assign(changed.metadata,patch);
    const result=await request('/works/'+id+'/save',{operationId:'bad-'+sha256(JSON.stringify(patch)).slice(0,16),expectedRevision:1,document:changed});
    assert.equal(result.response.status,422);
    assert.match(result.value.error.path,/^\/metadata\//);
    assert.equal((await request('/works/'+id)).value.revision,1);
  }
  const blank=(await request('/state')).value.blank;assert.equal(blank.metadata.audience,'');
});
