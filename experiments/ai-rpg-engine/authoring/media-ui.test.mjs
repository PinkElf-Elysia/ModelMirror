import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {startAuthoringServer} from './server.mjs';
import {createGalleryClient,createMediaUI,mediaReference,selectedInGroup} from './media-ui.mjs';
const parent=fileURLToPath(new URL('../.rpg04-work/authoring-b2cb/',import.meta.url));
await mkdir(parent,{recursive:true});
const runRoot=await mkdtemp(join(parent,'tests-'));
const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII=';
const journalFixture=()=>{const records=new Map();return {records,journal:async(kind,value)=>kind==='read'?[...records.values()]:kind==='put'?records.set(value.operationId,structuredClone(value)):records.delete(value)};};
async function fixture(t, fault) {
  const directory=await mkdtemp(join(runRoot,'store-'));
  const host=await startAuthoringServer({directory,fault});t.after(()=>host.close());
  const calls=[];
  const api=async(path,body)=>{
    calls.push({path,body});
    const response=await fetch(host.origin+'/rpg-app/api/authoring'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Authoring-Request':'1',Origin:host.origin},body:JSON.stringify(body)});
    const value=await response.json();if(!response.ok)throw {...value.error,httpStatus:response.status};return value;
  };
  const local=journalFixture();const client=createGalleryClient({api,journal:local.journal});await client.refresh();
  return {host,api,calls,client,...local};
}

test('清除音频与缺失音频分别往返，不篡改其他媒体引用',()=>{
  assert.equal(mediaReference(null),null);
  assert.equal(mediaReference({alias:'missing-audio',url:null}),'missing-audio');
});
test('分组全选只计算可见范围，跨组已有选择仍保留',()=>{
  const catalog={images:[{id:'a',groupId:null},{id:'b',groupId:'island'},{id:'c',groupId:null}]};
  const selected=new Set(['a','b']);assert.deepEqual(selectedInGroup(catalog,null,selected).map(x=>x.id),['a']);assert.deepEqual([...selected],['a','b']);
});
test('操作保护存储失败不派发任何请求',async()=>{
  const calls=[];const client=createGalleryClient({api:async(...args)=>calls.push(args),journal:async()=>{throw {code:'LOCAL_JOURNAL_UNAVAILABLE'};}});
  await assert.rejects(client.update('/gallery/groups',{name:'港口'},0),e=>e.code==='LOCAL_JOURNAL_UNAVAILABLE');
  assert.equal(calls.length,0);assert.equal(client.pending,null);assert.equal(client.writing,false);
});
test('HTTP 图库创建、批量上传、改名移组与引用选用走真实回执',async t=>{
  const {client,api,records}=await fixture(t);
  const group=await client.update('/gallery/groups',{name:'港口'});const groupId=group.result.groupId;
  const uploaded=await client.update('/gallery/images',{images:[{name:'蓝海',groupId:null,fileName:'blue.png',mediaType:'image/png',base64:png},{name:'夜航',groupId:null,fileName:'night.png',mediaType:'image/png',base64:png}]});
  assert.equal(uploaded.result.imageIds.length,2);
  await client.update('/gallery/images/edit',{imageId:uploaded.result.imageIds[0],name:'蓝海新名',groupId});
  await client.update('/gallery/images/move',{imageIds:uploaded.result.imageIds,groupId});
  assert.equal(client.catalog.revision,4);assert.equal(client.catalog.images[0].name,'蓝海新名');assert.equal(records.size,0);
  const state=await api('/state');state.blank.metadata.name='图库作品';
  const created=await api('/works',{operationId:'new-work',expectedRevision:null,document:state.blank});
  const adopted=await api('/works/'+created.workId+'/assets/from-gallery',{operationId:'adopt',expectedRevision:0,imageId:uploaded.result.imageIds[0],galleryRevision:4,alias:'blue'});
  assert.equal(adopted.revision,1);
  const work=await api('/works/'+created.workId);assert.equal(work.assets[0].alias,'blue');assert.equal(work.draft.settings.media.cover,null);
});
test('多标签旧 revision 明确失败，输入由调用方持有，不自动重新提交',async t=>{
  const {client,api,calls,records}=await fixture(t);const other=createGalleryClient({api,journal:journalFixture().journal});await other.refresh();
  await client.update('/gallery/groups',{name:'先保存'});
  const stale={name:'仍保留'};const before=calls.length;
  await assert.rejects(other.update('/gallery/groups',stale),e=>e.code==='AUTHORING_REVISION_CONFLICT');
  assert.equal(calls.slice(before).filter(x=>x.body).length,1);assert.deepEqual(stale,{name:'仍保留'});assert.equal(other.pending,null);assert.equal(records.size,0);
});
test('提交后未知跨刷新恢复原操作，不会第二次派发上传',async t=>{
  let fail=true;const {client,api,calls,journal,records}=await fixture(t,async point=>{if(point==='afterGalleryCommit'&&fail){fail=false;throw Error('simulated interruption');}});
  await assert.rejects(client.update('/gallery/groups',{name:'已提交'}),e=>e.code==='AUTHORING_RESULT_UNKNOWN');
  const id=client.pending.operationId;assert.equal(records.size,1);
  const restored=createGalleryClient({api,journal});restored.hydrate(await journal('read'));
  await restored.refresh();const result=await restored.recover();assert.equal(result.status,'committed');assert.equal(result.receipt.operationId,id);
  assert.equal(restored.catalog.groups.length,1);assert.equal(records.size,0);assert.equal(calls.filter(x=>x.path==='/gallery/groups').length,1);
});
test('提交前落盘失败收束为明确失败，不伪装为成功或自动重试',async t=>{
  const {client,calls,records}=await fixture(t,async point=>{if(point==='beforeGalleryCommit')throw Error('simulated disk failure');});
  await assert.rejects(client.update('/gallery/groups',{name:'不应落盘'}),e=>e.code==='AUTHORING_WRITE_FAILED');
  assert.equal(records.size,0);assert.equal(client.pending,null);await client.refresh();assert.equal(client.catalog.groups.length,0);
  assert.equal(calls.filter(x=>x.path==='/gallery/groups').length,1);
});
test('丢失回执且服务器无记录时保持未知，不新 ID 重发',async()=>{
  const local=journalFixture(),calls=[];
  const api=async(path,body)=>{calls.push({path,body});if(path.endsWith('/recover'))return {status:'not_found'};if(path==='/gallery')return {revision:0,groups:[],images:[],pending:null};throw {code:'NETWORK_RESULT_UNKNOWN'};};
  const client=createGalleryClient({api,journal:local.journal,uuid:()=> 'original-id'});await client.refresh();
  await assert.rejects(client.update('/gallery/groups',{name:'海湾'}));
  assert.equal((await client.recover()).status,'not_found');assert.equal(client.pending.operationId,'original-id');
  await assert.rejects(client.update('/gallery/groups',{name:'海湾'}),e=>e.code==='AUTHORING_OPERATION_PENDING');
  assert.equal(calls.filter(x=>x.path==='/gallery/groups').length,1);assert.equal(local.records.size,1);
});
test('重复点击在首次请求待回执时被拦截',async()=>{
  let finish;const wait=new Promise(resolve=>{finish=resolve;});const local=journalFixture(),calls=[];
  const client=createGalleryClient({journal:local.journal,uuid:()=> 'once',api:async(path,body)=>{calls.push(path);if(path==='/gallery')return {revision:1,groups:[],images:[]};await wait;return {scope:'gallery',operationId:body.operationId,revision:1};}});
  const first=client.update('/gallery/groups',{name:'港'},0);
  await assert.rejects(client.update('/gallery/groups',{name:'港'},0),e=>e.code==='AUTHORING_OPERATION_PENDING');
  finish();await first;assert.equal(calls.filter(x=>x==='/gallery/groups').length,1);
});
test('图库核对只清除自身 journal，作品 pending 不被清除',async t=>{
  const {client,api,journal,records}=await fixture(t);await journal('put',{operationId:'unrelated-work',workId:'work-other'});
  const response=await client.update('/gallery/groups',{name:'组'});
  client.hydrate([{operationId:response.operationId,scope:'gallery'}]);await client.recover();assert.equal(records.has('unrelated-work'),true);
  const read=await api('/gallery');assert.equal(read.groups.length,1);
});
test('静态 UI 模块同源提供；父应用和其他文件不可任意读取',async t=>{
  const {host}=await fixture(t);const response=await fetch(host.origin+'/media-ui.mjs');assert.equal(response.status,200);assert.match(response.headers.get('content-type'),/javascript/);
  assert.match(response.headers.get('content-security-policy'),/connect-src 'self'/);assert.equal((await fetch(host.origin+'/gallery.mjs')).status,404);
});


test('加载无 BGM 的作品时，旧音频节点不阻断配置回显且缺失引用仍诊断',t=>{
  const descriptor=Object.getOwnPropertyDescriptor(globalThis,'document');
  let paused=0,bgm=null;
  const audio={pause(){paused++;},paused:true},play={setAttribute(){}},error={hidden:true,textContent:''};
  const container={querySelector:selector=>selector==='audio'?audio:selector==='[data-media="play-audio"]'?play:selector==='.media-audio-error'?error:null};
  Object.defineProperty(globalThis,'document',{configurable:true,value:{querySelectorAll:()=>[],querySelector:()=>container}});
  t.after(()=>{if(descriptor)Object.defineProperty(globalThis,'document',descriptor);else delete globalThis.document;});
  const ui=createMediaUI({editor:{read:()=>({bgm}),escapeHtml:String},api:()=>assert.fail('must not call API'),journal:()=>assert.fail('must not write journal')});
  assert.doesNotThrow(()=>ui.bindFields());
  assert.equal(paused,1);assert.equal(error.hidden,true);
  bgm={alias:'missing-audio',url:null};
  ui.bindFields();
  assert.equal(error.hidden,false);assert.match(error.textContent,/缺少音乐资源/);
});
