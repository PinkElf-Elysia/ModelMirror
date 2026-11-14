import {test} from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {ActionStore} from './store.mjs';import {resourceRule} from './rules.mjs';
const a={binding:{id:'test'},validateState:()=>{},validateConfig:async()=>{}};
a.rule=resourceRule({id:'use',label:'使用',entries:s=>s.items,accept:()=>true,path:id=>'items.'+id,validateState:a.validateState});
async function setup(t,fault){const dir=await mkdtemp(join(tmpdir(),'r5-store-'));let s=await ActionStore.open({directory:dir,adapter:a,fault});t.after(async()=>{await s.close();await rm(dir,{recursive:true,force:true});});
 const r=await s.create({operationId:'initial',initial:{items:[{id:'a',name:'丹药',quantity:5}]},confirmed:true});
 return {r,get s(){return s;},async reopen(){await s.close();s=await ActionStore.open({directory:dir,adapter:a,fault});await s.reconcile(r.id);}};}
const action={actionId:'use',itemId:'a',quantity:2};
test('proposal/cancel do not change world; execute+receipt+narrative intent atomically saved',async t=>{
 const f=await setup(t);let r=await f.s.propose(f.r.id,{operationId:'p-one',revision:0,action});
 assert.equal(r.state.items[0].quantity,5);assert.equal(r.events.length,0);
 const req={operationId:'exec-one',revision:r.revision,proposalId:'p-one',proposalHash:r.proposals['p-one'].proposal.hash};
 r=await f.s.execute(r.id,req);assert.equal(r.state.items[0].quantity,3);assert.equal(r.events.length,1);assert.ok(r.pending);assert.equal(r.turns.length,0);
 assert.deepEqual(await f.s.execute(r.id,req),r);
 await assert.rejects(f.s.execute(r.id,{...req,action}),{code:'STATE_OPERATION_COLLISION'});
 const done=await f.s.finishNarrative(r.id,r.pending,{status:'completed',finishReason:'stop',text:'你取用了丹药。'});
 assert.equal(done.state.items[0].quantity,3);assert.equal(done.events.length,1);assert.equal(done.turns.length,1);
 await assert.rejects(f.s.execute(done.id,{...req,operationId:'exec-two',revision:done.revision}),{code:'ACTION_PROPOSAL'});
});
for(const phase of ['beforeCommit','afterCommit'])test('execution interrupted '+phase+' resolves without repeating',async t=>{
 let armed=true;const f=await setup(t,async(p,{data})=>{if(armed&&p===phase&&data?.events?.length===1){armed=false;throw Error('interrupt');}});
 const req={operationId:'exec-one',revision:0,action};await assert.rejects(f.s.execute(f.r.id,req));await f.reopen();
 let r=await f.s.read(f.r.id);if(!r.operations['exec-one'])r=await f.s.execute(r.id,req);
 assert.equal(r.state.items[0].quantity,3);assert.equal(r.events.length,1);
});
test('stale, pending and insufficient do not partially commit; explicit override recorded',async t=>{
 const f=await setup(t);await assert.rejects(f.s.execute(f.r.id,{operationId:'bad',revision:0,action:{...action,quantity:6}}));
 let r=await f.s.read(f.r.id);assert.equal(r.revision,0);
 r=await f.s.propose(r.id,{operationId:'p-one',revision:r.revision,action});
 const state=structuredClone(r.state);state.items[0].quantity=8;r=await f.s.override(r.id,{operationId:'rewrite',revision:r.revision,state,reason:'测试明确改写'});
 await assert.rejects(f.s.execute(r.id,{operationId:'exec-one',revision:r.revision,proposalId:'p-one',proposalHash:r.proposals['p-one'].proposal.hash}),{code:'ACTION_STALE'});
 assert.equal((await f.s.read(r.id)).events[0].kind,'override');
});
