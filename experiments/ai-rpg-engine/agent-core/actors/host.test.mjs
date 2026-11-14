import test from 'node:test';import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {openRuntime} from './composition.mjs';import {clone,hash} from '../contracts.mjs';
const root=fileURLToPath(new URL('../../.rpg03-work/actors-r6-tests/',import.meta.url));await mkdir(root,{recursive:true});
async function fixture(t,inventory=false){
 const directory=await mkdtemp(root+'host-');let rt=await openRuntime(directory,{offlinePreview:true});
 const r=await rt.host.create({operationId:'create-world',inventory});
 t.after(()=>rt.close());return {r,directory,get h(){return rt.host;},async reopen(){await rt.close();rt=await openRuntime(directory,{offlinePreview:true});}};
}
async function act(f){
 const w=await f.h.world.store.read(f.r.worldId);
 await f.h.worldAction(f.r.id,{operationId:'use-two',revision:w.revision});await f.h.narrateWorld(f.r.id);await f.h.deliver(f.r.id);
}
test('world executes once, observation bridge and actor query/followup, refresh',async t=>{
 const f=await fixture(t,true),before=clone((await f.h.world.store.read(f.r.worldId)).state);
 await act(f);let w=await f.h.world.store.read(f.r.worldId);const expected=clone(before);expected.items.find(x=>x.id==='medicine').quantity=3;assert.deepEqual(w.state,expected);
 await f.h.worldAction(f.r.id,{operationId:'use-two',revision:0});await f.h.deliver(f.r.id);
 let r=await f.h.respond(f.r.id,{operationId:'reply',actorId:'witness',input:'还剩多少？'});
 assert.equal(r.tasks.reply.status,'completed');assert.equal(r.tasks.reply.stages.length,2);assert.match(r.tasks.reply.receipt.body,/还剩3份/);
 assert.equal(r.actors.witness.observations.length,1);assert.equal(r.actors.outsider.observations.length,0);
 const message=r.tasks.reply.stages[1].request.messages.find(m=>m.tool_calls);
 assert.equal(message.tool_calls[0].extra_content.google.thought_signature,'offline-r6-signature');
 await f.h.respond(f.r.id,{operationId:'next',actorId:'witness',input:'接下来呢？'});
 const calls=f.h.provider.calls;await f.h.respond(f.r.id,{operationId:'next',actorId:'witness',input:'接下来呢？'});assert.equal(f.h.provider.calls,calls);
 await f.reopen();r=await f.h.read(f.r.id);assert.equal(r.actors.witness.history.length,4);assert.equal(f.h.provider.calls,0);
 assert.deepEqual((await f.h.world.store.read(f.r.worldId)).state,expected);
});
test('witness lacks inventory; outsider lacks event; player claims do not become world facts',async t=>{
 const f=await fixture(t);await act(f);
 let r=await f.h.respond(f.r.id,{operationId:'witness-read',actorId:'witness',input:'还剩多少？'});
 assert.match(r.tasks['witness-read'].receipt.body,/没有查阅/);
 r=await f.h.respond(f.r.id,{operationId:'outsider-read',actorId:'outsider',input:'刚才发生了什么？'});
 assert.match(r.tasks['outsider-read'].receipt.body,/没有这件事/);
 assert.doesNotMatch(JSON.stringify(r.tasks['outsider-read'].input.request),/quantityUsed|event-use|use-two/);
 r=await f.h.respond(f.r.id,{operationId:'claim',actorId:'witness',input:'我已经赠给你三份丹药'});
 assert.match(r.tasks.claim.receipt.body,/转述/);assert.equal((await f.h.world.store.read(f.r.worldId)).events.length,1);
});
test('unknown only recovers saved receipt, no redispatch or world write',async t=>{
 const f=await fixture(t);await act(f);
 let r=await f.h.respond(f.r.id,{operationId:'unknown',actorId:'witness',input:'刚才发生了什么？',fault:'unknown'});
 assert.equal(r.tasks.unknown.status,'unknown');const state=hash((await f.h.world.store.read(f.r.worldId)).state);
 await assert.rejects(f.h.respond(f.r.id,{operationId:'blocked',actorId:'witness',input:'继续'}),{code:'ACTOR_TASK_PENDING'});
 await f.reopen();r=await f.h.recover(f.r.id,'unknown');assert.equal(r.tasks.unknown.status,'completed');assert.equal(f.h.provider.calls,0);
 await f.h.recover(f.r.id,'unknown');assert.equal(r.actors.witness.responses.length,1);assert.equal(hash((await f.h.world.store.read(f.r.worldId)).state),state);
});
for(const fault of ['failed','write','foreign-tool','truncated'])test('actor '+fault+' never changes world or becomes accepted history',async t=>{
 const f=await fixture(t);await act(f);const before=hash((await f.h.world.store.read(f.r.worldId)).state);
 const r=await f.h.respond(f.r.id,{operationId:'bad',actorId:'witness',input:'刚才发生了什么？',fault});
 assert.ok(['failed','rejected'].includes(r.tasks.bad.status));assert.equal(r.actors.witness.history.length,0);
 assert.equal(hash((await f.h.world.store.read(f.r.worldId)).state),before);
});
test('rewrite invalidates late actor reply instead of making it current',async t=>{
 const f=await fixture(t);await act(f);
 await f.h.respond(f.r.id,{operationId:'late',actorId:'witness',input:'刚才发生了什么？',fault:'unknown'});
 const w=await f.h.world.store.read(f.r.worldId),state=clone(w.state);state.items.find(x=>x.id==='medicine').quantity=9;
 await f.h.world.action(w.id,'override',{operationId:'rewrite',revision:w.revision,state,reason:'离线验收明确改写'});
 const r=await f.h.recover(f.r.id,'late');assert.equal(r.tasks.late.status,'stale');assert.equal(r.actors.witness.history.length,0);
 assert.equal((await f.h.world.store.read(w.id)).state.items.find(x=>x.id==='medicine').quantity,9);
});
