import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {ScenarioFixtureProvider,scenarioFixturePort} from './fixture-scenario.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6k/tests/',import.meta.url));await mkdir(root,{recursive:true});
const result=text=>({status:'completed',text,finishReason:'stop'});
const replies={story:result('固定剧情'),summary:result('固定摘要'),compression:result('固定压缩')};
const req=(id,kind)=>({id:'request-'+id,operationId:id,...(kind?{kind}:{}),messages:[{role:'user',content:'原始输入'}]});
async function setup(t,options={}){const directory=await mkdtemp(join(root,'case-'));let p=await ScenarioFixtureProvider.open(directory,{replies,...options});t.after(()=>p.close());return {get p(){return p;},async reopen(){await p.close();p=await ScenarioFixtureProvider.open(directory,{replies});}};}
test('固定样例按任务类型选择，原文不能改变样例；方法冻结且无任意传输对象',async t=>{
 const f=await setup(t);for(const kind of ['story','summary','compression']){const r=req(kind,kind==='story'?undefined:kind);assert.equal((await f.p.dispatch(r)).result.text,replies[kind].text);assert.deepEqual((await f.p.lookup(r)).result,replies[kind]);}
 assert.equal(f.p.dispatchCount,3);assert.equal(scenarioFixturePort({dispatch:()=>fetch('https://example.invalid')}),null);assert.throws(()=>new ScenarioFixtureProvider());assert.equal(Object.isFrozen(f.p),true);
 assert.throws(()=>f.p.dispatch(req('x','memory')),e=>e.code==='OFFLINE_FIXTURE_INVALID');
});
test('同操作与 request hash 对照，重复派发拒绝且核对不重发',async t=>{
 const f=await setup(t),r=req('first');await f.p.dispatch(r);await assert.rejects(f.p.dispatch(r),e=>e.code==='OFFLINE_FIXTURE_ALREADY_DISPATCHED');
 await assert.rejects(f.p.lookup({...r,messages:[]}),e=>e.code==='OFFLINE_RECEIPT_MISMATCH');await f.reopen();assert.equal((await f.p.lookup(r)).result.text,'固定剧情');assert.equal(f.p.dispatchCount,0);
});
for(const phase of ['afterAccepted','afterReceipt'])test(phase+' 故障不自动重放，重启仅恢复可确认的原回执',async t=>{
 const f=await setup(t,{fault:p=>{if(p===phase)throw Error('simulated');}}),r=req('lost');await assert.rejects(f.p.dispatch(r));assert.equal(f.p.dispatchCount,1);await f.reopen();
 const receipt=await f.p.lookup(r);assert.equal(!!receipt,phase==='afterReceipt');assert.equal(f.p.dispatchCount,0);await assert.rejects(f.p.dispatch(r),e=>e.code==='OFFLINE_FIXTURE_ALREADY_DISPATCHED');
});
test('任意地址/凭据字段、函数样例、截断字段及超长内容拒绝',async()=>{
 for(const bad of [{...replies,url:'https://example.invalid'},{...replies,story:()=>({})},{...replies,story:{...replies.story,finishReason:'invalid'}},{...replies,story:result('a'.repeat(200001))}]){
  const d=await mkdtemp(join(root,'invalid-'));await assert.rejects(ScenarioFixtureProvider.open(d,{replies:bad}));
 }
});
