import test from 'node:test';import assert from 'node:assert/strict';
import {fixture} from './test-support.mjs';import {GroupHost} from './host.mjs';import {digest,requestBody} from './contracts.mjs';
const evidence=r=>({format:'modelmirror.rpg.group-receipt/1',requestId:r.id,requestHash:digest(requestBody(r)),targetId:r.targetId,modelHash:digest(r.model),status:'completed',dispatched:true,actualModel:'fixture',content:'中性完整回复',partial:'',costMicroUsd:0,evidenceHash:digest('mock')});
test('one dispatch per action, continue has no player turn, duplicate and restart never replay',async t=>{
 const f=await fixture(t);let calls=0;const provider={mode:'fixture',async dispatch(r){calls++;return evidence(r);},async lookup(){throw Error('unused');}},host=new GroupHost(f.store,provider),s=await f.store.read(f.id),input={operationId:'send',expectedRevision:0,payload:{targetId:s.members[1].id,input:'问题'}};
 await host.send(f.id,input);await host.send(f.id,input);assert.equal(calls,1);let a=await f.store.read(f.id);assert.equal(a.messages.length,3);await host.send(f.id,{operationId:'continue',expectedRevision:a.revision,payload:{targetId:s.members[2].id,input:null}});a=await f.store.read(f.id);assert.equal(a.messages.length,4);assert.equal(a.messages.filter(m=>m.source.kind==='player-input').length,1);await f.reopen();assert.equal(calls,2);assert.deepEqual(await f.store.read(f.id),a);
});
test('unknown dispatch reserves budget, refresh does not run, lookup adopts exactly once',async t=>{
 const f=await fixture(t);let calls=0,lookups=0;const provider={mode:'fixture',async dispatch(){calls++;throw Error('timeout');},async lookup(r){lookups++;return evidence(r);}};
 const s=await f.store.read(f.id);await new GroupHost(f.store,provider).send(f.id,{operationId:'send',expectedRevision:0,payload:{targetId:s.members[1].id,input:'input'}});const u=await f.store.read(f.id);assert.equal(u.requests[0].status,'unknown');assert.equal(u.messages.length,2);assert.equal(u.budget.usedCalls,1);await f.reopen();assert.equal(calls,1);const host=new GroupHost(f.store,provider);await host.recover(f.id,u.requests[0].id);await host.recover(f.id,u.requests[0].id);assert.equal(lookups,1);assert.equal((await f.store.read(f.id)).messages.length,3);
});
test('participation intervals exclude absence and preserve earlier history',async t=>{
 const f=await fixture(t),host=new GroupHost(f.store,{mode:'fixture',async dispatch(r){return evidence(r);}}),initial=await f.store.read(f.id),a=initial.members[1].id,b=initial.members[2].id;
 const change=async(action,id)=>f.store.change(f.id,action,{operationId:id,expectedRevision:(await f.store.read(f.id)).revision,payload:{memberId:b}});
 await change('leave','leave');await host.send(f.id,{operationId:'secret',expectedRevision:(await f.store.read(f.id)).revision,payload:{targetId:a,input:'WHILE_ABSENT'}});await change('rejoin','rejoin');const view=await f.store.memberView(f.id,b);assert.equal(view.messages.length,1);assert.doesNotMatch(JSON.stringify(await f.store.preview(f.id,b)),/WHILE_ABSENT/);
});
test('tampered reply identity is not adopted; invalid local commit requires explicit recovery',async t=>{
 const f=await fixture(t),s=await f.store.read(f.id),host=new GroupHost(f.store,{mode:'fixture',async dispatch(r){return {...evidence(r),targetId:s.members[2].id};}});
 await assert.rejects(host.send(f.id,{operationId:'send',expectedRevision:0,payload:{targetId:s.members[1].id,input:'hi'}}),{code:'GROUP_RECEIPT_MISMATCH'});const state=await f.store.read(f.id);assert.equal(state.requests[0].status,'unknown');assert.equal(state.messages.length,2);
});
