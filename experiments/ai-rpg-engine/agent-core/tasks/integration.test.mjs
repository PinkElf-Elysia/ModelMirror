import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';import {openRuntime} from './composition.mjs';import {clone,hash} from '../contracts.mjs';
async function setup(t,options={}){const d=await mkdtemp(join(tmpdir(),'r7-in-'));let rt=await openRuntime(d,{offlinePreview:true,...options});t.after(async()=>{await rt.close();await rm(d,{recursive:true,force:true});});return {get rt(){return rt;},async reopen(){await rt.close();rt=await openRuntime(d,{offlinePreview:true,...options});}};}
test('same executor: confirmed world action + parallel actor, query, next turn and reopen',async t=>{
 const f=await setup(t,{delayMs:150}),r=await f.rt.create({operationId:'start'}),world=f.rt.worlds.get('cultivation'),before=clone((await world.store.read(r.worldId)).state);
 let v=await f.rt.execute(r.id,{operationId:'use-two',revision:r.world.revision,parallel:true,strategy:'split'});
 assert.equal(v.world.resources[0].quantity,3);assert.equal(v.world.events.length,1);assert.equal(v.tasks.length,2);assert.ok(v.tasks.every(t=>t.adoption==='completed'));assert.equal(v.maxConcurrentRequests,2);
 assert.equal(v.tasks.find(t=>t.purpose==='actor-response').stages[0].attempts[0].model.id,'fixture-swift');
 const expected=clone(before);expected.items.find(i=>i.id==='medicine').quantity=3;assert.deepEqual((await world.store.read(r.worldId)).state,expected);
 v=await f.rt.respond(r.id,{operationId:'query',input:'还剩多少？'});assert.equal(v.tasks.at(-1).stages.length,2);assert.match(v.tasks.at(-1).receipt.body,/还剩3份/);
 const signature=v.tasks.at(-1).stages[1].request.messages.find(m=>m.tool_calls).tool_calls[0].extra_content.google.thought_signature;assert.equal(signature,'offline-r6-signature');
 const calls=v.budget.calls;v=await f.rt.execute(r.id,{operationId:'use-two',revision:0,parallel:true});assert.equal(v.budget.calls,calls);assert.equal(v.world.events.length,1);
 await f.reopen();v=await f.rt.view(r.id);assert.equal(v.budget.calls,calls);assert.equal(v.world.resources[0].quantity,3);
});
test('direct and query dialogue reuse both card adapters',async t=>{const f=await setup(t);for(const adapterId of ['cultivation','lighthouse']){
 const r=await f.rt.create({operationId:'start-'+adapterId,adapterId});const original=hash((await f.rt.worlds.get(adapterId).store.read(r.worldId)).state);let v=await f.rt.dialogue(r.id,{operationId:'direct',input:'聊聊今日安排'});assert.equal(v.tasks[0].stages.length,1);
 v=await f.rt.dialogue(r.id,{operationId:'query',input:'查看登记物资'});assert.equal(v.tasks[1].stages.length,2);assert.equal(hash((await f.rt.worlds.get(adapterId).store.read(r.worldId)).state),original);
 v=await f.rt.execute(r.id,{operationId:'action',revision:v.world.revision});assert.equal(v.world.events.filter(e=>e.kind==='action').length,1);assert.equal(v.tasks.at(-1).adoption,'completed');
}});
test('unknown survives restart, lookup adopts once and does not execute action again',async t=>{const f=await setup(t);const r=await f.rt.create({operationId:'start'});
 let v=await f.rt.execute(r.id,{operationId:'action',revision:0,fault:'unknown'});const task=v.tasks[0];assert.equal(task.adoption,'unknown');const calls=v.budget.calls;
 await f.reopen();v=await f.rt.recover(r.id,task.id);assert.equal(v.tasks[0].adoption,'completed');assert.equal(v.budget.calls,calls);assert.equal(v.world.events.length,1);
});
for(const fault of ['failed','backup-failed','unknown-sticky','continuation-failed','write','foreign-tool','truncated'])test('actor outcome '+fault+' preserves authority',async t=>{
 const f=await setup(t),r=await f.rt.create({operationId:'start'}),before=hash((await f.rt.worlds.get('cultivation').store.read(r.worldId)).state);
 const v=await f.rt.respond(r.id,{operationId:'reply',input:fault==='continuation-failed'?'还剩多少？':'刚才发生了什么？',strategy:'backup',fault});
 assert.equal(hash((await f.rt.worlds.get('cultivation').store.read(r.worldId)).state),before);
 if(fault==='failed'){assert.equal(v.tasks[0].adoption,'completed');assert.equal(v.budget.calls,2);}
 else assert.ok(['failed','unknown','rejected'].includes(v.tasks[0].adoption));
 if(fault==='continuation-failed')assert.equal(v.tasks[0].fallbackUsed,false);
});
test('late actor receipt does not overwrite explicit changed state',async t=>{
 const f=await setup(t),r=await f.rt.create({operationId:'start'});let v=await f.rt.respond(r.id,{operationId:'late',input:'刚才发生了什么？',fault:'unknown'});
 const w=f.rt.worlds.get('cultivation'),record=await w.store.read(r.worldId),state=clone(record.state);state.items.find(i=>i.id==='medicine').quantity=9;
 await w.action(r.worldId,'override',{operationId:'override',revision:record.revision,state,reason:'离线明确改写'});
 v=await f.rt.recover(r.id,v.tasks[0].id);assert.equal(v.tasks[0].adoption,'stale');assert.equal(v.actors[0].history.length,0);assert.equal(v.world.resources[0].quantity,9);
});

test('parallel failure is isolated, receipt-only finish never executes twice',async t=>{
 const f=await setup(t),r=await f.rt.create({operationId:'start'});
 let v=await f.rt.execute(r.id,{operationId:'action',revision:0,parallel:true,fault:'failed'});
 assert.equal(v.tasks.find(t=>t.purpose==='world-narration').adoption,'failed');assert.equal(v.tasks.find(t=>t.purpose==='actor-response').adoption,'completed');
 const calls=v.budget.calls;v=await f.rt.finishReceipt(r.id,{operationId:'finish',reason:'保留回执'});
 assert.equal(v.world.pending,null);assert.equal(v.world.resources[0].quantity,3);assert.equal(v.budget.calls,calls);
});
test('unknown blocks receipt-only finish; repeated operation rejects changed policy',async t=>{
 const f=await setup(t),r=await f.rt.create({operationId:'start'});
 await f.rt.execute(r.id,{operationId:'action',revision:0,fault:'unknown'});
 await assert.rejects(()=>f.rt.finishReceipt(r.id,{operationId:'finish',reason:'不能绕过'}),/R7_RECOVERY_REQUIRED/);
 const r2=await f.rt.create({operationId:'second'});await f.rt.respond(r2.id,{operationId:'reply',input:'刚才发生了什么？'});
 await assert.rejects(()=>f.rt.respond(r2.id,{operationId:'reply',input:'刚才发生了什么？',strategy:'split'}),/R7_TASK_COLLISION/);
});
test('confirmed action before task registration can resume narration alone after restart',async t=>{
 const f=await setup(t),r=await f.rt.create({operationId:'start'});
 await f.rt.bridge.worldAction(r.bridgeId,{operationId:'action',revision:0});await f.reopen();
 let v=await f.rt.view(r.id);assert.equal(v.canResumeNarration,true);assert.equal(v.budget.calls,0);assert.equal(v.world.resources[0].quantity,3);
 v=await f.rt.resumeNarration(r.id);assert.equal(v.canResumeNarration,false);assert.equal(v.tasks.length,1);assert.equal(v.tasks[0].adoption,'completed');assert.equal(v.budget.calls,1);
 assert.equal(v.world.events.filter(e=>e.kind==='action').length,1);assert.equal(v.actors[0].history.length,0);
 await assert.rejects(()=>f.rt.resumeNarration(r.id),/R7_NARRATION_ALREADY_STARTED/);
});
