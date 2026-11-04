import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {startAuthoringServer} from './server.mjs';
import {blockedWorkRecord,recoveryTarget,resolveWorkRecovery,documentsEqual} from './recovery.mjs';

const parent=fileURLToPath(new URL('../.rpg04-work/authoring-b2d/',import.meta.url));
await mkdir(parent,{recursive:true});
const runRoot=await mkdtemp(join(parent,'recovery-tests-'));
async function fixture(t,fault) {
  const directory=await mkdtemp(join(runRoot,'store-'));
  const host=await startAuthoringServer({directory,fault});t.after(()=>host.close());
  const calls=[];
  const api=async(path,body)=>{
    calls.push({path,body:structuredClone(body)});
    const response=await fetch(host.origin+'/rpg-app/api/authoring'+path,body===undefined?{}:{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(body)});
    const value=await response.json();if(!response.ok)throw {...value.error,httpStatus:response.status};return value;
  };
  const create=async name=>{const doc=(await api('/state')).blank;doc.metadata.name=name;const receipt=await api('/works',{operationId:'create-'+name,expectedRevision:null,document:doc});return api('/works/'+receipt.workId);};
  return {host,api,calls,create};
}

test('真实宿主：B 被 A 的未知操作阻断后，两个草稿日志不互相覆盖，恢复不派发第二次保存',async t=>{
  const f=await fixture(t,(point,meta)=>{if(point==='afterCommit'&&meta.operationId==='save-a')throw Error('receipt lost');});
  const a=await f.create('A'),b=await f.create('B');
  a.draft.sources.description='A 原文\n\n  保留';b.draft.sources.description='B 未保存草稿';
  const original={operationId:'save-a',workId:a.id,sourceId:a.id,document:a.draft,kind:'/works/'+a.id+'/save',revision:0};
  const attempted={operationId:'save-b',workId:b.id,sourceId:b.id,document:b.draft,kind:'/works/'+b.id+'/save',revision:0};
  const journal=new Map([[original.operationId,structuredClone(original)]]);
  await assert.rejects(f.api('/works/'+a.id+'/save',{operationId:original.operationId,expectedRevision:0,document:a.draft}),e=>e.code==='AUTHORING_RESULT_UNKNOWN');
  let error;try{await f.api('/works/'+b.id+'/save',{operationId:attempted.operationId,expectedRevision:0,document:b.draft});}catch(e){error=e;}
  assert.equal(error.code,'AUTHORING_OPERATION_PENDING');
  const observer=blockedWorkRecord(attempted,error.details);journal.set(observer.operationId,observer);
  assert.equal(journal.size,2);assert.deepEqual(journal.get('save-a'),original);
  assert.equal(observer.document.sources.description,'B 未保存草稿');assert.deepEqual(recoveryTarget(observer),{workId:a.id,operationId:'save-a'});
  const before=f.calls.length;
  const result=await resolveWorkRecovery({record:structuredClone(observer),currentWorkId:b.id,api:f.api});
  assert.equal(result.status,'committed');assert.equal(result.view,'stay');assert.equal(result.work,undefined);
  assert.deepEqual(f.calls.slice(before),[{path:'/operations/recover',body:{workId:a.id,operationId:'save-a'}}]);
  journal.delete(observer.operationId);assert.equal(journal.get('save-a').document.sources.description,a.draft.sources.description);
  assert.equal((await f.api('/works/'+b.id)).revision,0);assert.equal((await f.api('/works/'+a.id)).revision,1);
  const own=await resolveWorkRecovery({record:original,currentWorkId:a.id,api:f.api});
  assert.equal(own.view,'apply');assert.equal(own.work.draft.sources.description,a.draft.sources.description);
  assert.equal(f.calls.filter(c=>c.path.endsWith('/save')).length,2);
});

test('新页面只有服务器待核对记录时，不跟随其他作品；本作品仍可恢复',async()=>{
  const receipt={operationId:'pending',workId:'A',revision:2};const calls=[];
  const api=async(path)=>{calls.push(path);return path==='/operations/recover'?{status:'committed',receipt}:{id:'A',revision:2};};
  const record={operationId:'pending',workId:'A',sourceId:'B',serverOnly:true};
  assert.equal((await resolveWorkRecovery({record,currentWorkId:'B',api})).view,'stay');
  assert.deepEqual(calls,['/operations/recover']);
  assert.equal((await resolveWorkRecovery({record,currentWorkId:'A',api})).view,'apply');
});

test('原始新建和复制操作允许跟随目标；从无关作品核对不会切走',async()=>{
  const api=async path=>path==='/operations/recover'?{status:'committed',receipt:{operationId:'create',workId:'new',revision:0}}:{id:'new',revision:0};
  const created={operationId:'create',workId:null,sourceId:null};
  assert.equal((await resolveWorkRecovery({record:created,currentWorkId:null,api})).view,'apply');
  const copied={...created,sourceId:'source'};
  assert.equal((await resolveWorkRecovery({record:copied,currentWorkId:'source',api})).view,'apply');
  assert.equal((await resolveWorkRecovery({record:copied,currentWorkId:'unrelated',api})).view,'stay');
});

test('尚无结果与确认未提交不会读取作品或伪装成恢复成功',async()=>{
  for(const status of ['not_found','not_committed']){
    const calls=[];const record={operationId:'original',workId:'A',sourceId:'A'};
    const result=await resolveWorkRecovery({record,currentWorkId:'A',api:async(path,body)=>{calls.push({path,body});return {status};}});
    assert.deepEqual(result,{status});assert.equal(calls.length,1);assert.equal(calls[0].body.operationId,'original');
  }
});

test('错误操作回执、错误作品和滞后版本保持未知，不返回可应用状态',async()=>{
  const record={operationId:'original',workId:'A',sourceId:'A'};
  for(const receipt of [{operationId:'other',workId:'A',revision:1},{operationId:'original',workId:'B',revision:1},{operationId:'original',workId:'A',revision:1.5}]){
    let calls=0;await assert.rejects(resolveWorkRecovery({record,currentWorkId:'A',api:async()=>{calls++;return {status:'committed',receipt};}}),e=>e.code==='NETWORK_RESULT_UNKNOWN');assert.equal(calls,1);
  }
  for(const work of [{id:'B',revision:2},{id:'A',revision:0}]){
    await assert.rejects(resolveWorkRecovery({record,currentWorkId:'A',api:async path=>path==='/operations/recover'?{status:'committed',receipt:{operationId:'original',workId:'A',revision:1}}:work}),e=>e.code==='NETWORK_RESULT_UNKNOWN');
  }
});

test('被拒绝的同作品写入也不是原操作，不自动套用原结果覆盖较新草稿',async()=>{
  const original={operationId:'rejected',workId:'A',sourceId:'A',document:{text:'new draft'}};
  const observer=blockedWorkRecord(original,{operationId:'prior',workId:'A'});
  const result=await resolveWorkRecovery({record:observer,currentWorkId:'A',api:async()=>({status:'committed',receipt:{operationId:'prior',workId:'A',revision:1}})});
  assert.equal(result.view,'stay');assert.equal(observer.document.text,'new draft');assert.equal(original.blockedBy,undefined);
});

test('恢复模块通过正式静态路由提供，不开放任意文件',async t=>{
  const {host}=await fixture(t);
  const response=await fetch(host.origin+'/recovery.mjs');assert.equal(response.status,200);assert.match(response.headers.get('content-type'),/javascript/);assert.match(await response.text(),/export async function resolveWorkRecovery/);
  assert.equal((await fetch(host.origin+'/recovery.test.mjs')).status,404);
});

test('恢复内容比较忽略对象键顺序，但保留源码空行和列表顺序的真实变化',()=>{
  const a={metadata:{name:'原文',tags:['甲','乙'],language:'zh-CN'},sources:{description:'正文\n\n  缩进'}};
  const b={sources:{description:'正文\n\n  缩进'},metadata:{language:'zh-CN',tags:['甲','乙'],name:'原文'}};
  assert.equal(documentsEqual(a,b),true);
  const moved=structuredClone(b);moved.metadata.tags.reverse();assert.equal(documentsEqual(a,moved),false);
  const text=structuredClone(b);text.sources.description='正文\n缩进';assert.equal(documentsEqual(a,text),false);
  assert.equal(documentsEqual({value:[]},{value:{}}),false);
  assert.equal(documentsEqual({a:1},{b:1}),false);
});
