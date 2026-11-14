import test from 'node:test';import assert from 'node:assert/strict';import {readFile,writeFile} from 'node:fs/promises';import {join} from 'node:path';
import {fixture} from './test-support.mjs';import {GroupHost} from './host.mjs';import {digest,requestBody} from './contracts.mjs';
const done=r=>({format:'modelmirror.rpg.group-receipt/1',requestId:r.id,requestHash:digest(requestBody(r)),targetId:r.targetId,modelHash:digest(r.model),status:'completed',dispatched:true,actualModel:'fixture',content:'完成',partial:'',costMicroUsd:0,evidenceHash:digest('evidence')});
for(const stage of ['beforeCommit','afterCommit'])test(`${stage} prepare failure reconciles without dispatch or duplicate player input`,async t=>{
 let armed=false;const f=await fixture(t,{fault:async point=>{if(armed&&point===stage){armed=false;throw Error('injected');}}});let calls=0;const host=new GroupHost(f.store,{mode:'fixture',async dispatch(r){calls++;return done(r);}}),s=await f.store.read(f.id),input={operationId:'send',expectedRevision:0,payload:{targetId:s.members[1].id,input:'once'}};
 armed=true;await assert.rejects(host.send(f.id,input),{code:stage==='beforeCommit'?'OFFLINE_WRITE_FAILED':'OFFLINE_WRITE_UNKNOWN'});assert.equal(calls,0);await assert.rejects(f.store.read(f.id),{code:'GROUP_RECOVERY_REQUIRED'});await f.store.recover();const recovered=await f.store.read(f.id);assert.equal(recovered.messages.length,stage==='beforeCommit'?1:2);assert.equal(recovered.budget.usedCalls,stage==='beforeCommit'?0:1);await f.reopen();assert.equal(calls,0);
});
for(const stage of ['beforeCommit','afterCommit'])test(`${stage} adoption failure retains remote receipt and recovers once`,async t=>{
 let armed=false;const f=await fixture(t,{fault:async(point,{data})=>{if(armed&&point===stage){armed=false;throw Error('injected-adoption');}}});let calls=0,receipt;const p={mode:'fixture',async dispatch(r){calls++;receipt=done(r);armed=true;return receipt;},async lookup(){return receipt;}},host=new GroupHost(f.store,p),s=await f.store.read(f.id);
 await assert.rejects(host.send(f.id,{operationId:'send',expectedRevision:0,payload:{targetId:s.members[1].id,input:'once'}}),{code:stage==='beforeCommit'?'OFFLINE_WRITE_FAILED':'OFFLINE_WRITE_UNKNOWN'});await f.store.recover();await f.reopen();const r=(await f.store.read(f.id)).requests[0];await new GroupHost(f.store,p).recover(f.id,r.id);assert.equal(calls,1);assert.equal((await f.store.read(f.id)).messages.length,3);
});
test('close prepared versus claim is CAS guarded; close retains input and releases reservation',async t=>{
 const f=await fixture(t),s=await f.store.read(f.id),p=await f.op('prepare','send',{targetId:s.members[1].id,input:'keep input'}),input={operationId:'close',expectedRevision:1,payload:{requestId:p.requestId}};
 await f.store.closePrepared(f.id,input);await assert.rejects(f.store.claim(f.id,{...input,operationId:'claim'}),{code:'GROUP_REVISION_CONFLICT'});const after=await f.store.read(f.id);assert.equal(after.messages.length,2);assert.equal(after.budget.usedCalls,0);assert.equal(after.budget.usedMicroUsd,0);
});
test('unknown/absent lookup never refunds or unlocks; failed truncated reply is not history',async t=>{
 const f=await fixture(t),s=await f.store.read(f.id);let calls=0;const p={mode:'fixture',async dispatch(){calls++;return null;},async lookup(){return null;}},host=new GroupHost(f.store,p);
 await host.send(f.id,{operationId:'send',expectedRevision:0,payload:{targetId:s.members[1].id,input:'hi'}});const before=await f.store.read(f.id);await host.recover(f.id,before.requests[0].id);const after=await f.store.read(f.id);assert.deepEqual(after.budget,before.budget);assert.equal(after.requests[0].status,'unknown');assert.equal(calls,1);
 p.lookup=async r=>({...done(r),status:'failed',content:'',partial:'截断的输出',costMicroUsd:null});await host.recover(f.id,before.requests[0].id);const failed=await f.store.read(f.id);assert.equal(failed.messages.length,2);assert.equal(failed.requests[0].evidence.partial,'截断的输出');assert.equal(failed.budget.usedMicroUsd,before.budget.usedMicroUsd);
});
test('proven undispatched failure releases calls and USD; foreign reply or overspend cannot settle',async t=>{
 const f=await fixture(t),s=await f.store.read(f.id),host=new GroupHost(f.store,{mode:'fixture',async dispatch(r){return {...done(r),status:'failed',dispatched:false,content:'',actualModel:null,costMicroUsd:0};}});
 await host.send(f.id,{operationId:'fail',expectedRevision:0,payload:{targetId:s.members[1].id,input:'hi'}});const a=await f.store.read(f.id);assert.equal(a.budget.usedCalls,0);assert.equal(a.budget.usedMicroUsd,0);
 const bad=new GroupHost(f.store,{mode:'fixture',async dispatch(r){return {...done(r),costMicroUsd:r.reservation.microUsd+1};}});await assert.rejects(bad.send(f.id,{operationId:'over',expectedRevision:a.revision,payload:{targetId:s.members[1].id,input:null}}),{code:'GROUP_COST_BOUND_EXCEEDED'});assert.equal((await f.store.read(f.id)).requests.at(-1).status,'unknown');
});
test('cross-group targets, stale revisions, forged sources, concurrent sends and immutable template',async t=>{
 const f=await fixture(t),s=await f.store.read(f.id),g=await f.store.create({...f.input,operationId:'g2'}),other=await f.store.read(g.id);
 await assert.rejects(f.op('prepare','foreign',{targetId:other.members[1].id,input:'x'}),{code:'CONVERSATION_MEMBER_NOT_FOUND'});
 await assert.rejects(f.op('prepare','extra',{targetId:s.members[1].id,input:'x',source:'model-result'}));
 const edit=structuredClone(f.doc);edit.character.card.data.description='CHANGED_DRAFT';await f.works.save(f.reference.workId,{operationId:'save',expectedRevision:(await f.works.read(f.reference.workId)).revision,document:edit});await f.works.publish(f.reference.workId,{operationId:'publish2',expectedRevision:(await f.works.read(f.reference.workId)).revision});assert.doesNotMatch(JSON.stringify(await f.store.preview(f.id,s.members[1].id)),/CHANGED_DRAFT/);
 const input={operationId:'race1',expectedRevision:0,payload:{targetId:s.members[1].id,input:'x'}};const results=await Promise.allSettled([f.store.prepare(f.id,input),f.store.prepare(f.id,{...input,operationId:'race2'})]);assert.equal(results.filter(x=>x.status==='fulfilled').length,1);assert.equal((await f.store.read(f.id)).messages.length,2);
});
