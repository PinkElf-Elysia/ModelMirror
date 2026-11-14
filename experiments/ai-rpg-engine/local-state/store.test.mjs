import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {LocalStateStore} from './store.mjs';import {exampleState} from './example.mjs';
const binding={adapter:'test'},receipt=(operations=[],body='林清整理好药囊。')=>({status:'completed',finishReason:'stop',text:body+'<state_delta>'+JSON.stringify({operations})+'</state_delta>'});
const use={type:'quantity',id:'medicine',delta:-2,reason:'服用两份测试丹药'};
async function setup(t,fault){const dir=await mkdtemp(join(tmpdir(),'rpg-local-state-'));let store=await LocalStateStore.open({directory:dir,binding,fault});t.after(async()=>{await store.close();assert.ok(dir.startsWith(join(tmpdir(),'rpg-local-state-')));await rm(dir,{recursive:true,force:true});});
 const r=await store.create({operationId:'init',initial:exampleState(),confirmed:true});return {store,r,dir,reopen:async()=>{await store.close();store=await LocalStateStore.open({directory:dir,binding});return store;}};}
const start=(s,r,operationId='turn-1')=>s.startTurn(r.id,{operationId,revision:r.revision,input:'服用两份养息丹'},{messages:[],hash:'frozen'});
test('one atomic adopted reply, duplicate and restart do not double apply',async t=>{
 const {store,r,reopen}=await setup(t);const a=await start(store,r);assert.equal(a.dispatch,true);
 assert.equal((await start(store,r)).dispatch,false);await store.receive(r.id,'turn-1',receipt([use]));
 const b=await store.finish(r.id,'turn-1');assert.equal(b.state.items[0].quantity,3);assert.equal(b.turns.length,1);
 assert.equal((await store.finish(r.id,'turn-1')).state.items[0].quantity,3);const again=await reopen();assert.equal((await again.read(r.id)).state.items[0].quantity,3);
 await assert.rejects(()=>again.startTurn(r.id,{operationId:'turn-1',revision:0,input:'另一个请求'},{}),{code:'STATE_OPERATION_COLLISION'});
});
test('invalid/truncated receipts preserve raw and block; only explicit discard clears failure',async t=>{
 const {store,r}=await setup(t);await start(store,r);await store.receive(r.id,'turn-1',{...receipt([use]),finishReason:'length'});
 const b=await store.finish(r.id,'turn-1');assert.equal(b.state.items[0].quantity,5);assert.equal(b.turns.length,0);assert.equal(b.operations['turn-1'].status,'rejected');assert.ok(b.operations['turn-1'].receipt.text);
 const c=await store.discard(r.id,{operationId:'discard-1',revision:b.revision,pendingId:'turn-1',reason:'舍弃未完成回复'});assert.equal(c.pending,null);
});
test('unknown is reconciled, never discarded or redispatched',async t=>{
 const {store,r}=await setup(t);await start(store,r);let b=await store.markFailure(r.id,'turn-1',{message:'connection lost'});
 await assert.rejects(()=>store.discard(r.id,{operationId:'discard',revision:b.revision,pendingId:'turn-1',reason:'不能绕过'}),{code:'STATE_UNKNOWN_CANNOT_DISCARD'});
 assert.equal((await start(store,r)).dispatch,false);
 // A confirmed receipt is delivered by the original provider, not a new generation.
 await store.receive(r.id,'turn-1',receipt([use]));b=await store.finish(r.id,'turn-1');assert.equal(b.state.items[0].quantity,3);
});
test('override, disabled state, checkpoint fork and candidate preview are isolated',async t=>{
 const {store,r}=await setup(t);const p=await store.previewCandidate(r.id,receipt([use]));assert.equal(p.state.items[0].quantity,3);assert.equal((await store.read(r.id)).state.items[0].quantity,5);
 await start(store,r);await store.receive(r.id,'turn-1',receipt([use]));let b=await store.finish(r.id,'turn-1');
 const desired=structuredClone(b.state);desired.balances.low=99;
 b=await store.override(r.id,{operationId:'rewrite',revision:b.revision,state:desired,reason:'用户创作改写'});
 assert.equal(b.events.at(-1).before.balances.low,20);assert.equal(b.events.at(-1).after.balances.low,99);
 const child=await store.fork(r.id,{operationId:'fork',revision:b.revision,eventId:'turn-1'});assert.equal(child.state.balances.low,20);assert.equal(child.state.items[0].quantity,3);
 b=await store.enabled(r.id,{operationId:'off',revision:b.revision,enabled:false});assert.equal(b.state.balances.low,99);
 await assert.rejects(()=>start(store,b,'turn-2'),{code:'STATE_DISABLED'});
});
test('concurrent requests and stale state cannot both start',async t=>{
 const {store,r}=await setup(t);const results=await Promise.allSettled([start(store,r,'a'),start(store,r,'b')]);assert.equal(results.filter(x=>x.status==='fulfilled').length,1);
});
for(const stage of ['beforeCommit','afterCommit'])test('storage fault '+stage+' reconciles without double state change',async t=>{
 let fire=false;const {store,r}=await setup(t,async s=>{if(fire&&s===stage){fire=false;throw Error('fault');}});
 await start(store,r);await store.receive(r.id,'turn-1',receipt([use]));fire=true;
 await assert.rejects(()=>store.finish(r.id,'turn-1'));await store.reconcile(r.id);
 const b=await store.finish(r.id,'turn-1');assert.equal(b.state.items[0].quantity,3);assert.equal(b.turns.length,1);
});
