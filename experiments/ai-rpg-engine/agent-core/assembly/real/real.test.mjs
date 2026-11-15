import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {openAssembly} from '../runtime.mjs';import {SLOTS,policyFor,validateGrant,liveCatalog,PLAN_INPUT,OBS_INPUT} from './contract.mjs';import {hash,clone} from '../../contracts.mjs';import {reserve} from '../../actions/real/provider.mjs';
const quote={inputUSD:.00000075,outputUSD:.00000375,quoteId:'offline-test'},manifestHash='b'.repeat(64);
async function setup(t,kind='proposal'){
 const d=await mkdtemp(join(tmpdir(),'r8-real-unit-'));let calls=0;
 const tr={mode:'mock',async generate(p){calls++;const grant={taskId:p.taskId,session:p.session,purpose:p.purpose,inputHash:hash(p.meta.input),slot:Object.values(SLOTS).find(s=>s.purpose===p.purpose).id};validateGrant(grant,p);
  const message=p.purpose==='world-dialogue'?{role:'assistant',content:null,tool_calls:[{id:'call-test',type:'function',function:{name:kind==='query'?'query_local_state':'prepare_action',arguments:JSON.stringify(kind==='query'?{queries:[{domain:'items'}]}:{actionId:'use_medicine',itemId:'medicine',quantity:2})}}]}:{role:'assistant',content:'根据公开记录回应；未提供的账户与信任值未知。'};
  return {status:'completed',finishReason:message.tool_calls?'tool_calls':'stop',message,provider:'offline-r8-unit',billing:{kind:'simulated',microUSD:100}};},close(){}};
 const rt=await openAssembly(d,{controlledReal:true,catalog:await liveCatalog(quote),manifestHash,limits:{calls:3,microUSD:500000},transportFactory:async()=>tr});
 t.after(async()=>{await rt.close();await rm(d,{recursive:true,force:true});});return {rt,tr,count:()=>calls};
}
test('controlled real is opt-in; grant forbids continuation, foreign purpose and excess cost',async()=>{
 await assert.rejects(openAssembly('unused',{catalog:await liveCatalog(quote),manifestHash,transportFactory:async()=>({mode:'mock'})}),/ASSEMBLY_OFFLINE_ONLY/);
 const p={taskId:'a',session:'s',purpose:'observation-note',attempt:0,meta:{input:OBS_INPUT},request:{messages:[{role:'user',content:OBS_INPUT}],tools:[],tool_choice:'none'}};
 const g={taskId:'a',session:'s',purpose:'observation-note',slot:3,inputHash:hash(OBS_INPUT)};validateGrant(g,p);
 assert.throws(()=>validateGrant(g,{...p,attempt:1}),/GRANT/);assert.throws(()=>validateGrant(g,{...p,request:{...p.request,messages:[{role:'tool',content:'x'}]}}),/NO_CONTINUATION/);
 assert.throws(()=>reserve({text:'x'.repeat(200000)},quote,SLOTS[3]),/BUDGET/);assert.equal(policyFor(SLOTS[1],quote).maxCalls,1);
});
test('common assembly performs proposal then single execution, narration and explicit observation adoption',async t=>{
 const {rt,count}=await setup(t);let v=await rt.create({operationId:'start',adapterId:'cultivation'});const original=clone(v.world.state),expected=clone(original);expected.items.find(i=>i.id==='medicine').quantity=3;
 v=await rt.submit(v.id,{operationId:'plan',ownerId:'world-dialogue',input:PLAN_INPUT,strategy:'slot-1'});assert.equal(v.tasks.at(-1).adoption,'proposed');assert.deepEqual(v.world.state,original);
 const [proposalId,row]=Object.entries(v.world.proposals)[0],confirmation={operationId:'once',revision:v.world.revision,proposalId,proposalHash:row.proposal.hash};v=await rt.confirm(v.id,confirmation);v=await rt.confirm(v.id,confirmation);assert.deepEqual(v.world.state,expected);assert.equal(v.world.events.filter(e=>e.kind==='action').length,1);
 v=await rt.narrate(v.id,{strategy:'slot-2'});assert.equal(v.tasks.at(-1).adoption,'completed',v.tasks.at(-1).error);
 v=await rt.submit(v.id,{operationId:'note',ownerId:'observation-note',input:OBS_INPUT,strategy:'slot-3'});const note=v.tasks.at(-1);assert.equal(note.adoption,'awaiting-adoption');assert.equal(note.observation.state,undefined);assert.equal(note.observation.accounts,undefined);
 v=await rt.adopt(v.id,note.id);assert.equal(v.tasks.at(-1).adoption,'completed');assert.deepEqual(v.world.state,expected);assert.equal(count(),3);
});
test('unexpected read request cannot consume a second provider attempt',async t=>{
 const {rt,count}=await setup(t,'query');let v=await rt.create({operationId:'start',adapterId:'cultivation'});v=await rt.submit(v.id,{operationId:'plan',ownerId:'world-dialogue',input:PLAN_INPUT,strategy:'slot-1'});
 assert.equal(count(),1);assert.notEqual(v.tasks.at(-1).adoption,'proposed');assert.equal(v.world.resources[0].quantity,5);assert.equal(v.world.events.filter(e=>e.kind==='action').length,0);
});
