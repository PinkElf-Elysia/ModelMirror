import {test} from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';import {openRuntime} from './composition.mjs';
async function setup(t){const dir=await mkdtemp(join(tmpdir(),'r5-lifecycle-'));const runtime=await openRuntime(dir,{offlinePreview:true});t.after(async()=>{await runtime.close();await rm(dir,{recursive:true,force:true});});const h=runtime.get('lighthouse'),r=await h.create({operationId:'open',initial:h.adapter.initial(),confirmed:true});return {h,r};}
const action={actionId:'fuel_lamp',itemId:'lamp-oil',quantity:2};
test('cancel, concurrent actions, pause and checkpoint keep one valid world',async t=>{
 const {h,r:initial}=await setup(t);let r=await h.propose(initial.id,{operationId:'p',revision:0,action});
 r=await h.cancel(r.id,{operationId:'cancel',revision:r.revision,proposalId:'p',proposalHash:r.proposals.p.proposal.hash});assert.deepEqual(r.state,initial.state);assert.equal(r.events.length,0);
 const results=await Promise.allSettled(['a','b'].map(operationId=>h.execute(r.id,{operationId,revision:r.revision,action})));
 assert.equal(results.filter(x=>x.status==='fulfilled').length,1);r=await h.narrate(r.id);assert.equal(r.events.length,1);assert.equal(r.state.supplies[0].quantity,3);
 const parent=structuredClone(r),child=await h.action(r.id,'fork',{operationId:'fork',revision:r.revision,eventId:r.events[0].id});
 assert.deepEqual(child.state,parent.state);assert.notEqual(child.id,parent.id);assert.deepEqual(await h.store.read(parent.id),parent);
 r=await h.action(child.id,'enabled',{operationId:'off',revision:child.revision,enabled:false});
 await assert.rejects(h.execute(r.id,{operationId:'no',revision:r.revision,action}));assert.equal((await h.view(r.id)).resources[0].quantity,3);
 r=await h.action(r.id,'enabled',{operationId:'on',revision:r.revision,enabled:true});
 r=await h.execute(r.id,{operationId:'child-use',revision:r.revision,action});assert.equal(r.state.supplies[0].quantity,1);assert.equal((await h.store.read(parent.id)).state.supplies[0].quantity,3);
});
test('durable proposal result resumes without a second model call or executing it',async t=>{
 const {h,r}=await setup(t),accept=h.store.acceptProposal.bind(h.store);let once=true;
 h.store.acceptProposal=async(...args)=>{if(once){once=false;throw Error('simulated stop after tool result');}return accept(...args);};
 await assert.rejects(h.turn(r.id,{operationId:'plan',revision:0,input:'拟定使用两份的方案'}));
 const count=h.provider.calls,recovered=await h.recover(r.id);
 assert.equal(h.provider.calls,count);assert.equal(recovered.pending,null);assert.equal(recovered.proposals.plan.status,'review');assert.deepEqual(recovered.state,r.state);
 const raw=await h.evidence(r.id);assert.ok(raw.record.operations.plan.stages[0].result.wire.includes('[DONE]'));
});
test('bad arguments, extra tools and truncated outputs are terminal without retry',async t=>{
 const {h,r:initial}=await setup(t);let r=initial;
 for(const fault of ['bad-json','double','truncated']){
  const calls=h.provider.calls;r=await h.turn(r.id,{operationId:'bad-'+fault,revision:r.revision,input:'拟定使用两份的方案'},{fault});
  assert.equal(h.provider.calls,calls+1);assert.equal(r.operations[r.pending].status,'rejected');assert.deepEqual(r.state,initial.state);
  r=await h.action(r.id,'discard',{operationId:'discard-'+fault,revision:r.revision,pendingId:r.pending,reason:'结束模拟错误'});
 }
});
