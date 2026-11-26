import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {startRoundWorkspace} from './controlled-workspace.mjs';
import {mockControl,syntheticToken} from '../revisions/mock-control.mjs';
import {mockPolicy,mockPrices,roundModels,roundFixtures} from './controlled-round-preview.mjs';

async function setup(t,{mockOptions={},calls=8,taskMoney=100000,roundMoney=20000,enabled=true,price}={}){
  const cleanup=[],mock=await mockControl({after:f=>cleanup.push(f)},mockOptions);
  const models=await roundModels(mock),directory=await mkdtemp(join(process.env.CHARACTER_ROUND_TEST_ROOT??tmpdir(),'controlled-round-'));
  const options={directory,port:0,upstream:mock.origin,serviceToken:syntheticToken,models,
    authorization:{id:'rounds-local',calls,microUsd:taskMoney,initial:{calls:1,microUsd:700}},
    ...(enabled?{roundPolicy:{...mockPolicy(models),maxMicroUsd:roundMoney},priceEvidence:price?model=>price(mockPrices(models)(model)):mockPrices(models)}:{})};
  let app=await startRoundWorkspace(options);
  t.after(async()=>{await app.close();for(const f of cleanup)await f();});
  const {id,payload}=await roundFixtures(app,models);
  const read=()=>app.groupStore.read(id);
  async function rules(){
    const group=await read();
    return {expectedRevision:group.revision,mode:'fixed',order:group.members.slice(1).map(m=>m.id),muted:[],text:''};
  }
  async function start(){
    const r=await rules(),preview=await app.rounds.preview(id,r);
    const body={operationId:'round-one',previousRoundId:null,rules:r,message:'一起记录水位。',quoteHash:preview.quote.hash};
    return {preview,body,...await app.rounds.start(id,body)};
  }
  const step=r=>app.rounds.step(id,{roundId:r.id,index:r.next,expectedRevision:r.revision});
  return {get app(){return app;},id,payload,mock,models,read,rules,start,step,
    async restart(){await app.close();app=await startRoundWorkspace(options);}};
}

test('two controlled speakers, one player message, one shared source and task authorization',async t=>{
  const f=await setup(t,{calls:4}),{round,preview,body}=await f.start();
  const {app,id}=f;
  assert.equal(f.mock.calls.length,0);assert.equal((await app.context.guard.status()).usedCalls,1);
  assert.deepEqual((await app.rounds.history(id)).items[0].estimate.steps,preview.quote.steps);
  assert.equal(preview.quote.calls,2);assert.equal(preview.quote.microUsd,preview.quote.steps.reduce((n,s)=>n+s.microUsd,0));
  assert.equal((await app.rounds.start(id,body)).duplicate,true);
  const one=await f.step(round);assert.equal(one.status,'ready');assert.equal(one.next,1);
  await assert.rejects(f.step(round),/STEP_CHANGED/);
  const done=await f.step(one);assert.equal(done.status,'completed');
  const group=await f.read();assert.deepEqual(f.mock.calls.map(p=>p.selectionId),f.models.map(m=>m.choice.entry.selectionId));
  assert.ok(f.mock.calls.every(p=>p.sessionId===id&&p.messageContract==='modelmirror.rpg.character-group-messages/1'));
  assert.equal(group.messages.filter(m=>m.source.kind==='player-input').length,1);
  assert.equal(group.messages.filter(m=>m.source.kind==='model-result').length,2);
  assert.deepEqual(group.requests.map(r=>r.binding),group.members.slice(1).map(m=>m.binding));
  assert.ok(group.requests.every((r,i)=>r.evidence.dispatched&&r.evidence.costMicroUsd===null&&r.reservation.microUsd<=preview.quote.steps[i].microUsd));
  const budget=await app.context.guard.status();assert.equal(budget.usedCalls,3);
  assert.equal(budget.usedMicroUsd,700+group.budget.usedMicroUsd);
  await app.groupHost.send(id,{operationId:'manual',expectedRevision:group.revision,payload:{targetId:group.members[1].id,input:'手动接续'}});
  assert.equal(f.mock.calls.length,3);assert.equal((await app.context.guard.status()).usedCalls,4);
  await assert.rejects(app.rounds.preview(id,await f.rules()),/TASK_BUDGET_EXHAUSTED/);
  assert.equal(f.mock.calls.length,3);
});

test('stop during upstream response prevents later dispatch and does not cancel provider billing',async t=>{
  const entered=Promise.withResolvers(),release=Promise.withResolvers();
  const f=await setup(t,{mockOptions:{onDispatch:async()=>{entered.resolve();await release.promise;}}});
  const {round}=await f.start(),running=f.step(round);
  try{
    await entered.promise;
    const stopped=await f.app.rounds.stop(f.id,{roundId:round.id});
    assert.equal(stopped.status,'stopped');assert.equal(stopped.steps[0].status,'dispatching');
    assert.equal((await f.app.context.guard.status()).usedCalls,2);
    await assert.rejects(f.step(stopped),/STEP_CHANGED/);
  }finally{release.resolve();}
  assert.equal((await running).status,'stopped');
  const g=await f.read();assert.equal(g.budget.usedCalls,1);assert.ok(g.budget.usedMicroUsd>0);
  assert.equal(g.requests[0].evidence.costMicroUsd,null);assert.equal(f.mock.calls.length,1);
});

test('unknown retains money and count on restart; only the original receipt can resolve it',async t=>{
  const f=await setup(t,{mockOptions:{dropAfterStored:true}});
  const {round}=await f.start(),unknown=await f.step(round);
  assert.equal(unknown.status,'unknown');const before=await f.app.context.guard.status();
  assert.equal(before.pending.length,1);assert.equal(before.usedCalls,2);assert.ok(before.usedMicroUsd>700);
  await f.restart();
  assert.deepEqual(await f.app.rounds.view(f.id),unknown);
  assert.deepEqual(await f.app.context.guard.status(),before);assert.equal(f.mock.lookups.length,0);
  await assert.rejects(f.step(unknown),/STEP_CHANGED/);assert.equal(f.mock.calls.length,1);
  await f.app.groupHost.recover(f.id,unknown.steps[0].requestId);
  await f.app.groupHost.recover(f.id,unknown.steps[0].requestId);
  assert.equal(f.mock.calls.length,1);assert.equal(f.mock.lookups.length,1);
  assert.ok(f.mock.lookups[0].includes(unknown.steps[0].requestId));
  const settled=await f.app.context.guard.status();
  assert.equal(settled.pending.length,0);assert.equal(settled.usedMicroUsd,before.usedMicroUsd);
  const paused=await f.app.rounds.view(f.id);assert.equal(paused.status,'ready');assert.equal(paused.next,1);
  assert.equal((await f.app.rounds.history(f.id)).items[0].steps[1].status,'waiting');
});

test('round and task money limits deny before creating a round or a request',async t=>{
  for(const [config,expected] of [[{roundMoney:1},/ROUND_LIMIT/],[{taskMoney:701},/TASK_BUDGET_EXHAUSTED/],
    [{price:p=>({...p,includesReasoning:false})},/PRICE_UNKNOWN/]]){
    const f=await setup(t,config);
    await assert.rejects(f.start(),expected);
    assert.equal(await f.app.rounds.view(f.id),null);
    assert.equal(f.mock.calls.length,0);assert.equal((await f.read()).requests.length,0);
    assert.equal((await f.app.context.guard.status()).usedMicroUsd,700);
  }
});

test('fresh price evidence and unchanged quoted model required on each step',async t=>{
  let expires=false;
  const f=await setup(t,{price:p=>({...p,validUntil:expires?0:p.validUntil})});
  const rules=await f.rules();
  await assert.rejects(f.app.rounds.start(f.id,{rules,message:'测试',operationId:'forged',previousRoundId:null,quoteHash:'0'.repeat(64)}),/PREVIEW_CHANGED/);
  const {round}=await f.start(),one=await f.step(round);expires=true;
  await assert.rejects(f.step(one),/PRICE_UNKNOWN/);
  const v=await f.app.rounds.view(f.id);assert.equal(v.reason,'GROUP_PRICE_UNKNOWN');assert.equal(v.status,'blocked');
  assert.equal(f.mock.calls.length,1);assert.equal((await f.read()).requests.length,1);
});

test('shared headroom spent through a different group between two steps is rechecked',async t=>{
  const f=await setup(t,{calls:3}),{round}=await f.start(),one=await f.step(round);
  const other=await f.app.groupStore.create({operationId:'other-group',expectedRevision:null,payload:f.payload});
  const g=await f.app.groupStore.read(other.id);
  await f.app.groupHost.send(g.id,{operationId:'manual-other',expectedRevision:g.revision,payload:{targetId:g.members[1].id,input:'另一群发言'}});
  await assert.rejects(f.step(one),/TASK_BUDGET_EXHAUSTED/);
  assert.equal((await f.app.rounds.view(f.id)).status,'blocked');
  assert.equal((await f.read()).requests.length,1);assert.equal(f.mock.calls.length,2);
});

test('stop while the price check is pending prevents this step from claiming or dispatching',async t=>{
  let hold=false;const entered=Promise.withResolvers(),release=Promise.withResolvers();
  const f=await setup(t,{price:async p=>{if(hold){entered.resolve();await release.promise;}return p;}});
  const {round}=await f.start();hold=true;
  const attempted=assert.rejects(f.step(round),/ROUND_STOPPING/);
  await entered.promise;const stopping=f.app.rounds.stop(f.id,{roundId:round.id});release.resolve();
  await attempted;assert.equal((await stopping).status,'stopped');
  assert.equal(f.mock.calls.length,0);assert.equal((await f.read()).requests.length,0);
  assert.equal((await f.app.context.guard.status()).usedCalls,1);
});

test('disabled by default; changed readiness between steps stops before preparing next request',async t=>{
  const off=await setup(t,{enabled:false});assert.equal(off.app.rounds,undefined);assert.equal(off.app.workspace.groupRoundsEnabled,false);
  let ready=true;const f=await setup(t,{mockOptions:{ready:()=>ready}});
  const {round}=await f.start(),one=await f.step(round);ready=false;
  await assert.rejects(f.step(one),/ROUND_ROUTE_CHANGED/);
  assert.equal(f.mock.calls.length,1);assert.equal((await f.read()).requests.length,1);
});
