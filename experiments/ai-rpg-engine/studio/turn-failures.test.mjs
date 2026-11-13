import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {failureFixture} from './failed-turn-fixture.mjs';

test('failed/unknown turns expose partial text, close durably, never replay late results, and allow a new turn',async t=>{
 const f=await failureFixture();t.after(()=>f.close());
 for(const [input,code] of [['模拟截断','PROVIDER_OUTPUT_TRUNCATED'],['模拟审查','PROVIDER_CONTENT_FILTERED'],['模拟网关中断','MEMORY_TASK_RESULT_UNKNOWN']]){
  const id=await f.create(),base='sessions/'+id,{session:s,body}=await f.send(id,input),key=body.operationId;
  assert.equal(s.turns.length,0);assert.equal(s.requests[key].error,code);assert.match(s.requests[key].partial,/山间驿站/);
  const calls=f.calls.length,op={action:'dismiss-request',requestId:key,operationId:randomUUID(),expectedRevision:s.revision};
  await f.call(base+'/lifecycle',op);assert.equal((await f.call(base+'/lifecycle',op)).replayed,true);
  let h=await f.call(base+'/memory-palace');assert.equal(h.busy,false);
  await f.call(base+'/memory-palace/recover',{operationId:key,expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision});
  assert.equal(f.calls.length,calls);assert.equal((await f.call(base)).turns.length,0);
  await assert.rejects(f.call(base+'/send',body),/REQUEST_DISMISSED/);
  const next=await f.send(id,input);assert.equal(next.session.turns.length,1);assert.equal(next.session.id,id);
  assert.equal(next.session.requests[key].dismissed,true);
 }
});

test('missing browser request is fenced; deleting a turn rolls back context and preserves a recoverable version',async t=>{
 const f=await failureFixture();t.after(()=>f.close());const id=await f.create(),base='sessions/'+id,key=randomUUID();
 let h=await f.call(base+'/memory-palace');const recovery=await f.call(base+'/memory-palace/recover',{operationId:key,expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision});assert.equal(recovery.operation.status,'not-found');
 const body={operationId:key,input:'遗留草稿',expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,expectedContextPolicyRevision:h.effectivePolicy.revision};
 await f.call(base+'/lifecycle',{action:'dismiss-request',requestId:key,operationId:randomUUID(),expectedRevision:h.sessionRevision});
 await assert.rejects(f.call(base+'/send',body),/REQUEST_DISMISSED/);assert.equal(f.calls.length,0);
 await f.send(id,'进入书院');const {session:before}=await f.send(id,'询问馆员');const op={action:'delete-turn',operationId:randomUUID(),expectedRevision:before.revision,turn:2};const count=f.calls.length;
 await f.call(base+'/lifecycle',op);let s=await f.call(base);assert.equal(s.id,id);assert.equal(s.turns.length,1);assert.equal(f.calls.length,count);
 assert.equal((await f.call(base+'/plugins/rpg.rolling-summary')).enabled,true);
 await f.call(base+'/lifecycle',{action:'restore-version',operationId:randomUUID(),expectedRevision:s.revision,versionId:op.operationId});s=await f.call(base);assert.deepEqual(s.turns,before.turns);assert.equal(s.requests[key].dismissed,true);assert.equal(f.calls.length,count);
});
