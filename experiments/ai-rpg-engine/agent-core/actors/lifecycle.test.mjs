import test from 'node:test';import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {openRuntime} from './composition.mjs';import {clone,hash} from '../contracts.mjs';
import {query} from './observations.mjs';
const root=fileURLToPath(new URL('../../.rpg03-work/actors-r6-tests/',import.meta.url));await mkdir(root,{recursive:true});
async function setup(t,inventory=true){
 const dir=await mkdtemp(root+'life-');let rt=await openRuntime(dir,{offlinePreview:true});const r=await rt.host.create({operationId:'world',inventory});
 t.after(()=>rt.close());return {r,get h(){return rt.host;},async reopen(){await rt.close();rt=await openRuntime(dir,{offlinePreview:true});}};
}
async function action(f){await f.h.worldAction(f.r.id,{operationId:'execute',revision:0});await f.h.narrateWorld(f.r.id);await f.h.deliver(f.r.id);}
test('world owner commit survives lost coordinator receipt, recovery never executes twice',async t=>{
 const f=await setup(t),capture=f.h.captureWorld.bind(f.h);
 f.h.captureWorld=async()=>{throw Error('simulated coordinator receipt loss');};
 let r=await f.h.worldAction(f.r.id,{operationId:'execute',revision:0});assert.equal(r.tasks.execute.status,'unknown');
 assert.equal((await f.h.world.store.read(f.r.worldId)).state.items[0].quantity,3);
 f.h.captureWorld=capture;await f.reopen();r=await f.h.recover(f.r.id,'execute');assert.equal(r.tasks.execute.status,'completed');
 await f.h.narrateWorld(f.r.id);await f.h.deliver(f.r.id);await f.h.deliver(f.r.id);
 assert.equal((await f.h.read(f.r.id)).actors.witness.observations.length,1);
 assert.equal((await f.h.world.store.read(f.r.worldId)).events.length,1);
});
test('saved native query survives lost final response; explicit lookup only',async t=>{
 const f=await setup(t);await action(f);const original=f.h.provider.generate.bind(f.h.provider);
 f.h.provider.generate=(session,id,request,meta)=>original(session,id,request,{...meta,fault:request.tool_choice==='none'?'unknown':null});
 let r=await f.h.respond(f.r.id,{operationId:'query-final',actorId:'witness',input:'还剩多少？'});
 assert.equal(r.tasks['query-final'].status,'unknown');assert.equal(r.tasks['query-final'].stages[0].toolResult.items[0].quantity,3);
 await f.reopen();r=await f.h.recover(f.r.id,'query-final');assert.equal(f.h.provider.calls,0);
 assert.equal(r.actors.witness.responses.length,1);assert.match(r.tasks['query-final'].receipt.body,/还剩3份/);
});
test('private world and sibling canaries never enter actor request, query or accepted history',async t=>{
 const f=await setup(t);let w=await f.h.world.store.read(f.r.worldId);const state=clone(w.state);state.traits.innerThought='WORLD_PRIVATE_CANARY';
 await f.h.world.action(w.id,'override',{operationId:'private',revision:w.revision,state,reason:'owner-only test fixture'});
 await f.h.records.update(f.r.id,r=>{r.actors.outsider.history=[{role:'user',content:'SIBLING_PRIVATE_CANARY'},{role:'assistant',content:'SIBLING_REPLY_CANARY'}];});
 let r=await f.h.respond(f.r.id,{operationId:'isolated',actorId:'witness',input:'还剩多少？'});
 assert.doesNotMatch(JSON.stringify(r.tasks.isolated),/WORLD_PRIVATE_CANARY|SIBLING_PRIVATE_CANARY|SIBLING_REPLY_CANARY/);
 assert.doesNotMatch(JSON.stringify(r.actors.witness.history),/CANARY/);
 const frame=r.tasks.isolated.input.frame;
 for(const args of [{domain:'inventory',name:'WORLD_PRIVATE_CANARY'},{domain:'inventory',id:'other-person'},{domain:'observations'}])
  assert.doesNotMatch(JSON.stringify(query(frame,args)),/CANARY/);
});
test('template draft edits do not mutate sealed role, new worlds do not share experiences',async t=>{
 const f=await setup(t);await action(f);await f.h.respond(f.r.id,{operationId:'remember',actorId:'witness',input:'刚才发生了什么？'});
 const config=await f.h.works.verifyConfiguration(f.h.reference.workId,f.h.reference.versionId),doc=clone(config.document);
 doc.character.card.data.name='草稿新人物';doc.metadata.name='草稿新人物';
 await f.h.works.save(f.h.reference.workId,{operationId:'draft-edit',expectedRevision:1,document:doc});
 const second=await f.h.create({operationId:'second',inventory:false});
 assert.equal(second.actors.witness.card.data.name,'林舟');assert.equal(second.actors.witness.history.length,0);
 assert.notEqual(second.worldId,f.r.worldId);assert.equal(second.actors.witness.binding.configurationHash,f.r.actors.witness.binding.configurationHash);
 assert.equal((await f.h.world.store.read(second.worldId)).state.items[0].quantity,5);
});
test('same operation cannot change request; two submissions finish one actor response',async t=>{
 const f=await setup(t);const req={operationId:'same',actorId:'witness',input:'刚才发生了什么？'};
 await Promise.all([f.h.respond(f.r.id,req),f.h.respond(f.r.id,req)]);
 assert.equal(f.h.provider.calls,1);assert.equal((await f.h.read(f.r.id)).actors.witness.responses.length,1);
 await assert.rejects(f.h.respond(f.r.id,{...req,input:'改成另一请求'}),{code:'STATE_OPERATION_COLLISION'});
});
test('state change while actor generation is in flight discards final answer',async t=>{
 const f=await setup(t);await action(f);const original=f.h.provider.generate.bind(f.h.provider);
 f.h.provider.generate=async(...args)=>{
  const result=await original(...args),w=await f.h.world.store.read(f.r.worldId),state=clone(w.state);state.traits.mental='专注';
  await f.h.world.action(w.id,'override',{operationId:'midflight',revision:w.revision,state,reason:'明确改写，测试旧响应隔离'});return result;
 };
 const r=await f.h.respond(f.r.id,{operationId:'late',actorId:'witness',input:'刚才发生了什么？'});
 assert.equal(r.tasks.late.status,'stale');assert.equal(r.actors.witness.responses.length,0);
});
test('accepted response remains historical after rewrite; no implicit memory import',async t=>{
 const f=await setup(t);await action(f);await f.h.respond(f.r.id,{operationId:'answer',actorId:'witness',input:'还剩多少？'});
 const w=await f.h.world.store.read(f.r.worldId),state=clone(w.state);state.items[0].quantity=7;
 await f.h.world.action(w.id,'override',{operationId:'later',revision:w.revision,state,reason:'确认改写'});
 const v=await f.h.view(f.r.id);assert.equal(v.actors.find(a=>a.id==='witness').responses[0].current,false);
 assert.equal(v.actors.find(a=>a.id==='outsider').history.length,0);
});
