import test from 'node:test';import assert from 'node:assert/strict';import {RevisionCoordinator} from './coordinator.mjs';import {id} from './contracts.mjs';
test('legacy and revision gate share serialized allowance and pending source rejects',async()=>{
 let calls=1,used=0,unknown=false,dispatched=0;const fid=id('family','single','s'),f={id:fid,source:{},usedCalls:0,usedMicroUsd:0,requests:{}};
 const store={list:async()=>[{id:fid}],read:async()=>({...f,usedCalls:used})},sources={verify:async()=>({pending:unknown,budget:{calls,microUsd:10000}})};const g=new RevisionCoordinator(store,sources);
 const results=await Promise.allSettled([g.legacy('single','s',async()=>{dispatched++;calls--;}),g.run(async()=>{assert.equal(calls,0);})]);assert.equal(results.every(r=>r.status==='fulfilled'),true);await assert.rejects(g.legacy('single','s',async()=>{dispatched++;}),/SHARED_BUDGET_EXHAUSTED/);assert.equal(dispatched,1);
 calls=2;used=2;await assert.rejects(g.legacy('single','s',async()=>{}),/SHARED_BUDGET_EXHAUSTED/);used=0;unknown=true;await assert.rejects(g.legacy('single','s',async()=>{}),/REQUEST_PENDING/);
});
