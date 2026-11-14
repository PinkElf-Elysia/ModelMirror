import {test} from 'node:test';import assert from 'node:assert/strict';import {fixture,operationId} from './test-support.mjs';import {clone} from './contracts.mjs';
for(const phase of ['beforeCommit','afterCommit']){
 test('atomic final commit recovery '+phase,async t=>{
  let armed=true;
  const f=await fixture(t,'lighthouse',{fault:async(p,{data})=>{if(armed&&p===phase&&data?.turns?.length===1){armed=false;throw Error('injected interruption');}}});
  await assert.rejects(f.turn('给灯具添油两份'));
  await f.reopen();const done=await f.host.recover(f.record.id);
  assert.equal(done.turns.length,1);assert.equal(done.events.length,1);assert.equal(done.state.supplies[0].quantity,3);assert.equal(f.host.provider.calls,0);
 });
}
test('binding drift keeps original data and rejects reads; projection failure never settles again',async t=>{
 const f=await fixture(t),h=f.host;const done=await f.turn('给灯具添油两份');
 const binding=clone(h.store.binding);h.store.binding.rulesVersion='changed';await assert.rejects(h.store.read(done.id),{code:'STATE_BINDING_CHANGED'});h.store.binding=binding;
 const render=h.adapter.render;h.adapter.render=async()=>{throw Object.assign(Error('fixture'),{code:'TEST_PROJECTION'});};
 const view=await h.view(done.id);assert.equal(view.projectionError,'TEST_PROJECTION');assert.equal(view.state.supplies[0].quantity,3);assert.equal(h.provider.calls,1);
 h.adapter.render=render;assert.ok((await h.view(done.id)).panels);assert.equal(h.provider.calls,1);
});
test('record CAS rejects competing stale writes',async t=>{
 const f=await fixture(t),s=f.host.store,row=await s.envelope(f.record.id),one=clone(row.data),two=clone(row.data);one.revision++;two.revision++;
 const results=await Promise.allSettled([s.records.put(one.id,row.hash,one),s.records.put(two.id,row.hash,two)]);
 assert.equal(results.filter(x=>x.status==='fulfilled').length,1);assert.equal((await s.read(one.id)).revision,1);
});
test('initial confirmation, unsupported adapter and no implicit imports',async t=>{
 const f=await fixture(t);assert.throws(()=>f.runtime.get('../file'));
 await assert.rejects(f.host.create({operationId:operationId(),initial:f.host.adapter.initial(),confirmed:false}));
 await assert.rejects(f.host.create({operationId:operationId(),initial:f.host.adapter.initial(),confirmed:true,source:{kind:'confirmed-import',id:'old',history:[]}}));
});

test('candidate selection survives interruption after start without generating a replacement',async t=>{
 let armed=true;
 const f=await fixture(t,'lighthouse',{fault:async(phase,{data})=>{
  if(armed&&phase==='afterCommit'&&data?.pending&&data.operations[data.pending]?.options?.previewHash&&data.operations[data.pending]?.receipt){armed=false;throw Error('candidate start interruption');}
 }});
 const r=f.record,p=await f.host.preview(r.id,'给灯具添油两份');
 await assert.rejects(f.host.turn(r.id,{operationId:'op-candidate-crash',revision:r.revision,input:'给灯具添油两份'},{previewHash:p.previewHash}));
 await f.reopen();const done=await f.host.recover(r.id);
 assert.equal(done.state.supplies[0].quantity,3);assert.equal(done.turns[0].body,p.body);assert.equal(done.turns.length,1);
 assert.equal(done.operations['op-candidate-crash'].stages.length,0);assert.equal(f.host.provider.calls,0);
});
