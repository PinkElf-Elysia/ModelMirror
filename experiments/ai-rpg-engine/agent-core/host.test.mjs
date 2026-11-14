import {test} from 'node:test';import assert from 'node:assert/strict';
import {fixture,operationId} from './test-support.mjs';import {clone,hash} from './contracts.mjs';
for(const adapter of ['lighthouse','cultivation']){
 const use=adapter==='lighthouse'?'给灯具添油两份':'服用两份养息丹',query=adapter==='lighthouse'?'先查询灯油，再给灯具添油两份':'先查询丹药，再服用两份养息丹',rest=adapter==='lighthouse'?'用完剩余三份灯油':'服用剩余三份养息丹';
 const quantity=s=>adapter==='lighthouse'?s.supplies[0].quantity:s.items[0].quantity;
 test(adapter+': same host direct/query/continuation, 5→3→0, persisted metadata and no panel history',async t=>{
  const f=await fixture(t,adapter),initial=clone(f.record.state);
  let done=await f.turn(query);assert.equal(done.pending,null);assert.equal(quantity(done.state),3);assert.equal(done.turns.length,1);
  const op=Object.values(done.operations)[0];assert.equal(op.stages.length,2);assert.equal(op.query.stateRevision,0);
  const continuation=op.stages[1].request;assert.equal(continuation.tool_choice,'none');assert.equal(continuation.messages.at(-1).tool_call_id,'query-one');
  assert.equal(continuation.messages.at(-2).tool_calls[0].extra_content.google.thought_signature,'offline-opaque-signature');
  assert.equal(f.host.provider.calls,2);
  done=await f.turn(rest);assert.equal(quantity(done.state),0);assert.equal(f.host.provider.calls,3);
  assert.equal(adapter==='lighthouse'?done.state.supplies[0].source:done.state.items[0].source,adapter==='lighthouse'?initial.supplies[0].source:initial.items[0].source);
  const prior=hash(done.state);done=await f.turn('聊聊下一步安排');assert.equal(hash(done.state),prior);assert.equal(done.stateRevision,3);
  const view=await f.host.view(done.id);assert.ok(view.panels);assert.deepEqual(view.state,done.state);assert.equal(view.messages.length,3);
  assert.doesNotMatch(JSON.stringify(view.operations),/offline-opaque-signature/);
  await f.reopen();assert.equal(hash((await f.host.store.read(done.id)).state),prior);
 });
 test(adapter+': duplicate, concurrent different request and stale version cannot settle twice',async t=>{
  const f=await fixture(t,adapter),r=f.record,request={operationId:operationId(),revision:r.revision,input:use};
  const results=await Promise.all([f.host.turn(r.id,request),f.host.turn(r.id,request)]);assert.deepEqual(results[0],results[1]);assert.equal(f.host.provider.calls,1);
  await assert.rejects(f.host.turn(r.id,{...request,input:'换内容'}),{code:'STATE_OPERATION_COLLISION'});
  await assert.rejects(f.host.turn(r.id,{...request,operationId:operationId()}),{code:'STATE_STALE'});assert.equal(quantity((await f.host.store.read(r.id)).state),3);
 });
 test(adapter+': persisted unknown first or final recovered by lookup without redispatch',async t=>{
  const f=await fixture(t,adapter);let r=await f.turn(query,{fault:'unknown-first'});assert.equal(r.operations[r.pending].status,'unknown');assert.equal(quantity(r.state),5);
  await f.reopen();r=await f.host.recover(r.id);assert.equal(quantity(r.state),3);assert.equal(f.host.provider.calls,1);
  const f2=await fixture(t,adapter);r=await f2.turn(query,{fault:'unknown'});assert.equal(quantity(r.state),5);assert.equal(f2.host.provider.calls,2);
  await assert.rejects(f2.host.action(r.id,'discard',{operationId:operationId(),revision:r.revision,pendingId:r.pending,reason:'尝试跳过'}),{code:'STATE_UNKNOWN_CANNOT_DISCARD'});
  await f2.reopen();r=await f2.host.recover(r.id);assert.equal(quantity(r.state),3);assert.equal(f2.host.provider.calls,0);
 });
 test(adapter+': malformed native or final failure commits neither prose nor state; no retry',async t=>{
  for(const fault of ['invalid','bad-arguments','repeat','truncated','failed']){
   const f=await fixture(t,adapter);const r=await f.turn(query,{fault});
   assert.equal(quantity(r.state),5);assert.equal(r.turns.length,0);assert.ok(r.pending);assert.ok(['failed','rejected'].includes(r.operations[r.pending].status));
   const count=f.host.provider.calls;await f.host.recover(r.id);assert.equal(f.host.provider.calls,count);
  }
 });
}
test('unknown initial inventory and invalid increments reject whole batch',async t=>{
 const f=await fixture(t);let r=await f.turn('消耗一卷绷带');assert.equal(r.operations[r.pending].error,'STATE_INITIAL_VALUE_REQUIRED');assert.equal(r.turns.length,0);
 const a=f.host.adapter,receipt={status:'completed',finishReason:'stop',text:'你整理值守记录。<state_delta>'+JSON.stringify({operations:[{type:'setDuty',value:'已变',reason:'a'},{type:'consumeSupply',id:'lamp-oil',amount:99,reason:'b'}]})+'</state_delta>'};
 assert.throws(()=>a.candidate(f.record.state,receipt));assert.deepEqual((await f.host.store.read(r.id)).state,f.record.state);
});
test('candidate previews are pure; unique adopt, explicit override, fork and disable',async t=>{
 const f=await fixture(t),h=f.host,r=f.record;
 const candidate=await h.preview(r.id,'给灯具添油两份');assert.equal(h.provider.calls,0);assert.deepEqual(await h.store.read(r.id),r);
 const req={operationId:operationId(),revision:0,input:'给灯具添油两份'},opts={previewHash:candidate.previewHash};
 let done=await h.turn(r.id,req,opts);assert.equal(done.state.supplies[0].quantity,3);assert.equal(h.provider.calls,0);assert.equal(done.turns.length,1);
 assert.deepEqual(await h.turn(r.id,req,opts),done);await assert.rejects(h.turn(r.id,{...req,operationId:operationId(),revision:done.revision},opts),{code:'STATE_STALE_CANDIDATE'});
 const changed=clone(done.state);changed.supplies[0].quantity=8;
 done=await h.action(r.id,'override',{operationId:operationId(),revision:done.revision,state:changed,reason:'用户确认补录'});
 assert.equal(done.events.at(-1).before.supplies[0].quantity,3);assert.equal(done.events.at(-1).after.supplies[0].quantity,8);
 const child=await h.action(r.id,'fork',{operationId:operationId(),revision:done.revision,eventId:done.events[0].id});assert.equal(child.state.supplies[0].quantity,3);assert.equal(child.turns.length,1);
 done=await h.action(r.id,'enabled',{operationId:operationId(),revision:done.revision,enabled:false});
 await assert.rejects(h.turn(r.id,{operationId:operationId(),revision:done.revision,input:'给灯具添油两份'}),{code:'STATE_DISABLED'});
 assert.equal((await h.view(r.id)).state.supplies[0].quantity,8);assert.equal((await h.recover(r.id)).state.supplies[0].quantity,8);
});
test('pending operation blocks explicit rewrite; stale final state cannot overwrite',async t=>{
 const f=await fixture(t);let r=await f.turn('给灯具添油两份',{fault:'unknown'});
 await assert.rejects(f.host.action(r.id,'override',{operationId:operationId(),revision:r.revision,state:r.state,reason:'修改'}),{code:'STATE_TURN_PENDING'});
 await f.host.store.update(r.id,row=>{row.stateRevision++;});await assert.rejects(f.host.recover(r.id),{code:'STATE_STALE_RESULT'});
 assert.equal((await f.host.store.read(r.id)).state.supplies[0].quantity,5);
});
