import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';
import {openPreview} from './bootstrap.mjs';import {hash,clone} from '../contracts.mjs';
async function setup(t,options={}){const d=await mkdtemp(join(tmpdir(),'r8-life-')),rt=await openPreview(d,{offlinePreview:true,delayMs:1,...options});t.after(async()=>{await rt.close();await rm(d,{recursive:true,force:true});});const r=await rt.create({operationId:'start',adapterId:'lighthouse'});return {rt,r};}
const task=(rt,r,operationId,input,extra={})=>rt.submit(r.id,{operationId,ownerId:'world-dialogue',input,...extra});
async function action(rt,r){const v=await task(rt,r,'plan','拟定使用两份的方案'),p=v.world.proposals.plan;return rt.confirm(r.id,{operationId:'execute',revision:v.world.revision,proposalId:'plan',proposalHash:p.proposal.hash});}
test('settlement survives failed narration; receipt-only close and pending input rejection',async t=>{
 const {rt,r}=await setup(t);let v=await action(rt,r);assert.equal(v.world.resources[0].quantity,3);
 await assert.rejects(task(rt,r,'unsafe','继续'),/STATE_TURN_PENDING/);assert.equal((await rt.view(r.id)).tasks.length,1);
 v=await rt.narrate(r.id,{fault:'failed'});assert.equal(v.tasks.at(-1).adoption,'failed');
 v=await rt.finish(r.id,{operationId:'finish',reason:'只保留已确认回执'});assert.equal(v.world.pending,null);assert.equal(v.world.resources[0].quantity,3);
 v=await task(rt,r,'next','继续');assert.equal(v.tasks.at(-1).adoption,'completed');assert.equal(v.world.events.filter(e=>e.kind==='action').length,1);
});
test('unknown narration keeps settlement and forbids bypass; lookup-only recovery',async t=>{
 const {rt,r}=await setup(t);await action(rt,r);let v=await rt.narrate(r.id,{fault:'unknown'});const n=v.budget.calls;
 await assert.rejects(rt.finish(r.id,{operationId:'finish',reason:'跳过未知'}),/RECOVERY/);
 v=await rt.recover(r.id,v.tasks.at(-1).id);assert.equal(v.budget.calls,n);assert.equal(v.world.resources[0].quantity,3);assert.equal(v.world.turns.length,1);
});
test('stale proposal, changed repeated request and stale observation cannot be adopted',async t=>{
 const {rt,r}=await setup(t);let v=await task(rt,r,'plan','拟定使用两份的方案'),old=v;
 await assert.rejects(task(rt,r,'plan','使用 1 份'),/COLLISION/);v=await task(rt,r,'later','准备交接班');assert.equal(v.world.state.duty,'准备交接班');
 await assert.rejects(rt.confirm(r.id,{operationId:'execute',revision:v.world.revision,proposalId:'plan',proposalHash:old.world.proposals.plan.proposal.hash}),/ACTION_STALE/);
 v=await rt.submit(r.id,{operationId:'observe',ownerId:'observation-note',input:'刚才发生什么'});const n=v.tasks.at(-1);
 const p=rt.ports.get(r.adapterId),w=await p.store.read(r.worldId),s=clone(w.state);s.duty='用户明确修改';
 await p.store.override(w.id,{operationId:'override',revision:w.revision,state:s,reason:'离线验证晚到观察'});
 v=await rt.adopt(r.id,n.id);assert.equal(v.tasks.at(-1).adoption,'stale');assert.equal(v.world.state.duty,'用户明确修改');
});
for(const fault of ['truncated','foreign-tool','continuation-failed'])test('invalid or failed tool sequence '+fault+' does not alter state',async t=>{
 const {rt,r}=await setup(t);const v=await task(rt,r,'query','查看登记物资',{fault});assert.equal(hash(v.world.state),hash(r.world.state));assert.ok(['failed','rejected'].includes(v.tasks[0].adoption));
});
test('budget covers all stages, fallback once, serial same-run, bounded parallel runs',async t=>{
 const {rt,r}=await setup(t,{delayMs:100});const other=await rt.create({operationId:'other',adapterId:'lighthouse'});
 const first=task(rt,r,'first','你好',{strategy:'backup',fault:'failed'});
 await assert.rejects(task(rt,r,'competing','你好'),/RUN_BUSY/);
 await Promise.all([first,task(rt,other,'other','你好')]);let v=await rt.view(r.id);assert.equal(v.tasks[0].fallbackUsed,true);assert.equal(v.budget.calls,3);assert.equal(v.maxConcurrentTasks,2);
});
test('budget rejection dispatches nothing and disabled proposal cannot execute',async t=>{
 const {rt,r}=await setup(t,{limits:{calls:1,microUSD:150000}});let v=await task(rt,r,'plan','拟定使用两份的方案');
 await rt.capabilities(r.id,{operationId:'disable',revision:v.world.revision,names:[]});v=await rt.view(r.id);
 await assert.rejects(rt.confirm(r.id,{operationId:'execute',revision:v.world.revision,proposalId:'plan',proposalHash:v.world.proposals.plan.proposal.hash}),/CAPABILITY_DISABLED/);
 v=await task(rt,r,'next','你好');assert.equal(v.budget.calls,1);assert.equal(v.tasks.at(-1).adoption,'failed');assert.equal(v.world.resources[0].quantity,5);
});
