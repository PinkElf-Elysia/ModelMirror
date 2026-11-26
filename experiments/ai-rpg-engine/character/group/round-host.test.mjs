import test from 'node:test';
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {fixture} from './test-support.mjs';
import {RoundHost} from './round-host.mjs';
import {GroupHost} from './host.mjs';
import {digest,requestBody} from './contracts.mjs';

function receipt(r) {
  return {format:'modelmirror.rpg.group-receipt/1',requestId:r.id,requestHash:digest(requestBody(r)),targetId:r.targetId,
    modelHash:digest(r.model),status:'completed',dispatched:true,actualModel:'fixture',content:'中性检验回复 @[3]',
    partial:'',costMicroUsd:0,evidenceHash:digest(r.id)};
}
async function setup(t,{budget,authorization,dispatch=receipt,lookup=()=>null,fault}={}) {
  const f=await fixture(t,{authorization,fault});
  const input=structuredClone(f.input);
  input.operationId='three'; input.payload.members.push(input.payload.members[0]);
  if(budget)input.payload.budget=budget;
  const {id}=await f.store.create(input), calls=[];
  const provider={mode:'fixture',dispatch:r=>{calls.push(r);return dispatch(r);},lookup};
  const options=()=>({directory:join(f.directory,'round'),store:f.store,provider});
  let host=await RoundHost.open(options());
  t.after(()=>host.close());
  const current=await f.store.view(id);
  const rules={expectedRevision:current.revision, mode:'fixed', order:current.members.slice(1).map(m=>m.id), muted:[],text:''};
  const body={operationId:'one',previousRoundId:null,rules,message:'玩家的一条公开消息'};
  const step=(r,index=r.next)=>host.step(id,{roundId:r.id,index,expectedRevision:r.revision});
  return {id,f,body,step,calls,provider,get host(){return host;},start:()=>host.start(id,body),
    restart:async()=>{await host.close();await f.reopen();host=await RoundHost.open(options());}};
}
test('one player message, frozen same-name instances, serial verified replies, duplicate actions',async t=>{
  const x=await setup(t);
  x.body.rules.mode='mentions'; x.body.rules.text=x.body.message='@[2] @[1] @[2]';
  const {round}=await x.start();
  assert.deepEqual(round.steps.map(s=>s.memberId),[x.body.rules.order[1],x.body.rules.order[0]]);
  assert.equal((await x.start()).duplicate,true);
  await assert.rejects(x.host.start(x.id,{...x.body,message:'另一个输入'}),{code:'GROUP_OPERATION_COLLISION'});
  const attempts=await Promise.allSettled([x.step(round),x.step(round)]);
  assert.equal(attempts.filter(v=>v.status==='fulfilled').length,1);
  const next=await x.host.view(x.id);assert.equal(next.next,1);
  const final=await x.step(next);assert.equal(final.status,'completed');
  await assert.rejects(x.step(round));
  const group=await x.f.store.read(x.id);
  assert.equal(group.messages.filter(m=>m.source.kind==='player-input').length,1);
  assert.equal(group.messages.filter(m=>m.source.kind==='model-result').length,2);
  assert.equal(group.budget.usedCalls,2);assert.equal(new Set(x.calls.map(r=>r.targetId)).size,2);
  assert.ok(x.calls[1].messages.some(m=>m.content.includes('中性检验回复')));
});

test('stop before first step, stale revision and empty selection leave the group ledger untouched',async t=>{
  const x=await setup(t),before=await x.f.store.read(x.id);
  await assert.rejects(x.host.start(x.id,{...x.body,rules:{...x.body.rules,muted:x.body.rules.order}}),{code:'GROUP_ROUND_EMPTY'});
  await assert.rejects(x.host.start(x.id,{...x.body,rules:{...x.body.rules,expectedRevision:78}}),{code:'GROUP_REVISION_CONFLICT'});
  const {round}=await x.start();
  await x.host.stop(x.id,{roundId:round.id});await assert.rejects(x.step(round));
  assert.deepEqual(await x.f.store.read(x.id),before);assert.equal(x.calls.length,0);
});

for(const limit of ['group calls','task calls','group money'])test('per-step gate: '+limit,async t=>{
  const x=await setup(t,{budget:{calls:limit==='group calls'?1:8,microUsd:limit==='group money'?1:100000},
    authorization:{id:'grant',mode:'fixture',calls:limit==='task calls'?1:8,microUsd:100000},
    dispatch:r=>({...receipt(r),costMicroUsd:r.reservation.microUsd})});
  const initial=(await x.start()).round;
  const next=await x.step(initial);assert.equal(next.next,1);
  await assert.rejects(x.step(next),{code:limit==='task calls'?'GROUP_TASK_BUDGET_EXHAUSTED':'GROUP_BUDGET_EXHAUSTED'});
  assert.equal((await x.host.view(x.id)).status,'blocked');assert.equal(x.calls.length,1);
});
test('stop while provider is pending locks remaining members, current receipt still settles',async t=>{
  const entered=Promise.withResolvers(),finish=Promise.withResolvers();
  const x=await setup(t,{dispatch:async r=>{entered.resolve();await finish.promise;return receipt(r);}});
  const {round}=await x.start(), result=x.step(round);await entered.promise;
  const live=await x.host.view(x.id);assert.equal(live.status,'dispatching');
  assert.equal((await x.host.stop(x.id,{roundId:round.id})).status,'stopped');
  await assert.rejects(x.step(round));finish.resolve();assert.equal((await result).status,'stopped');
  await x.restart();assert.equal((await x.host.view(x.id)).steps[0].status,'completed');assert.equal(x.calls.length,1);
});
test('reload and reopened ledgers never dispatch; explicit next step resumes once',async t=>{
  const x=await setup(t);const first=(await x.start()).round;
  await x.restart();assert.equal(x.calls.length,0);
  const next=await x.step(first);await x.restart();
  assert.deepEqual(await x.host.view(x.id),next);assert.equal(x.calls.length,1);
  await x.step(next);assert.equal(x.calls.length,2);
  await x.host.stop(x.id,{roundId:first.id});await x.restart();
  assert.equal((await x.host.view(x.id)).status,'stopped');assert.equal(x.calls.length,2);
});
test('unknown survives stop/restart with call and fee held; lookup alone adopts once',async t=>{
  let found=null,lookups=0;
  const x=await setup(t,{dispatch:()=>null,lookup:()=>{lookups++;return found;}});
  const unknown=await x.step((await x.start()).round);
  assert.equal(unknown.status,'unknown');
  const reserved=(await x.f.store.read(x.id)).budget;
  assert.equal(reserved.usedCalls,1);assert.ok(reserved.usedMicroUsd>0);
  await x.restart();await assert.rejects(x.step(unknown));await x.host.stop(x.id,{roundId:unknown.id});
  const existing=unknown.steps[0].requestId, recovery=new GroupHost(x.f.store,x.provider);
  await recovery.recover(x.id,existing);assert.deepEqual((await x.f.store.read(x.id)).budget,reserved);
  found=receipt(x.calls[0]);await recovery.recover(x.id,existing);await recovery.recover(x.id,existing);
  const final=await x.host.view(x.id);assert.equal(final.status,'stopped');assert.equal(final.steps[0].status,'completed');
  assert.equal(x.calls.length,1);assert.equal(lookups,2);
});
test('verified failure and roster drift prevent later dispatch',async t=>{
  const x=await setup(t,{dispatch:r=>({...receipt(r),status:'failed',content:'',partial:'片段'})});
  const first=(await x.start()).round;
  assert.equal((await x.step(first)).status,'failed');await assert.rejects(x.step(first));
  const y=await setup(t);const next=await y.step((await y.start()).round);
  await y.f.store.change(y.id,'leave',{operationId:'left',expectedRevision:next.revision,payload:{memberId:next.steps[1].memberId}});
  await assert.rejects(y.step(next));assert.equal((await y.host.view(y.id)).status,'changed');
  assert.equal(y.calls.length,1);
});
