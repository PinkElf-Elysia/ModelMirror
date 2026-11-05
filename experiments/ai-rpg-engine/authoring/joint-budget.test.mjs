import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {canonical,sha256} from './contracts.mjs';
import {planJointCalls,validateJointPlan} from './joint-plan.mjs';
import {JointBudget,requireJointBudget} from './joint-budget.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6n-tests/',import.meta.url));await mkdir(root,{recursive:true});
const digest=v=>sha256(canonical(v)),clone=v=>structuredClone(v);
const reject=(f,code)=>assert.rejects(f,e=>{assert.equal(e.code,code,e.stack);return true;});
function plan({id='send-1',n=2,summary=true,memory=true,covered=Math.max(0,n-1),processed=n,kind='send',session='base'}={}){
 return planJointCalls({sessionId:'session-'+sha256(session),operationId:id,kind,completedTurns:n,summaryCoveredThrough:covered,memoryProcessedThrough:processed,
 authorization:{history:{enabled:true,revision:sha256('m1')},summary:{enabled:summary,revision:sha256('m2')},memory:{enabled:memory,revision:sha256('m3')}},sourceHash:sha256('source '+n),policyRevision:sha256('policy')});
}
async function fixture(t,{limit=20,storageFault}={}){const directory=await mkdtemp(join(root,'case-'));let budget=await JointBudget.open({directory,allowanceId:'local-fixture-allowance',limit,storageFault});t.after(()=>budget.close());return {directory,get budget(){return budget;},async reopen(){await budget.close();budget=await JointBudget.open({directory,allowanceId:'local-fixture-allowance',limit});}};}
const request=(g,stageId)=>({groupId:g.id,stageId,requestId:g.plan.stages.find(t=>t.stageId===stageId).operationId,requestHash:sha256(g.id+stageId)});
async function complete(b,g,stageId,status='completed'){const r=request(g,stageId);assert.equal((await b.claim(r)).dispatch,true);await b.settle({...r,status,resultHash:sha256('result '+stageId)});return r;}

test('actual phase list gives M3 2, M2+M3 4, catch-up plus after-story at most 6',()=>{
 assert.equal(plan({summary:false}).maximumCalls,2);assert.equal(plan().maximumCalls,4);const p=plan({n:4,covered:0});assert.equal(p.maximumCalls,6);
 assert.deepEqual(p.stages.map(x=>x.stageId),['summary-before','compression-before','story','summary-after','compression-after','memory-after']);
 assert.equal(new Set(p.stages.map(x=>x.operationId)).size,6);assert.notEqual(p.stages[0].operationId,p.stages[3].operationId);
 assert.equal(plan({n:0}).maximumCalls,2);assert.equal(plan({summary:false,memory:false}).maximumCalls,1);
 assert.equal(plan({kind:'update'}).maximumCalls,0);assert.equal(plan({kind:'update',processed:0}).maximumCalls,1);
});
test('forged phases, probabilities, future coverage and undocumented fields rejected',()=>{
 const p=plan();const altered=clone(p);altered.maximumCalls=1;assert.throws(()=>validateJointPlan(altered));
 for(const change of [{completedTurns:-1},{summaryCoveredThrough:3},{memoryProcessedThrough:3},{mode:'automatic'}])assert.throws(()=>planJointCalls({...p.input,...change}));
 assert.deepEqual(validateJointPlan(p),p);
});
test('default allowance is zero and no task group is written when exhausted',async t=>{const directory=await mkdtemp(join(root,'zero-')),b=await JointBudget.open({directory,allowanceId:'zero'});t.after(()=>b.close());await reject(()=>b.reserve(plan()),'JOINT_BUDGET_EXHAUSTED');assert.deepEqual(await b.status(),{limit:0,used:0,reserved:0,remaining:0,revision:0,allowanceId:'zero'});});
test('reserves the entire upper bound atomically; duplicate operation does not allocate again',async t=>{const f=await fixture(t,{limit:4}),p=plan(),r=await f.budget.reserve(p);assert.equal(r.replayed,false);assert.equal((await f.budget.status()).reserved,4);assert.equal((await f.budget.reserve(p)).replayed,true);
 await reject(()=>f.budget.reserve(plan({id:'another'})),'JOINT_BUDGET_EXHAUSTED');const collision=plan({summary:false});await reject(()=>f.budget.reserve(collision),'JOINT_OPERATION_COLLISION');assert.equal((await f.budget.status()).reserved,4);
});
test('two sessions share the same allowance; concurrent full reservation admits only one',async t=>{const f=await fixture(t,{limit:2});const results=await Promise.allSettled([f.budget.reserve(plan({summary:false,session:'A'})),f.budget.reserve(plan({summary:false,session:'B'}))]);assert.equal(results.filter(r=>r.status==='fulfilled').length,1);assert.equal(results.find(r=>r.status==='rejected').reason.code,'JOINT_BUDGET_EXHAUSTED');assert.equal((await f.budget.status()).remaining,0);});
test('stage must claim once before dispatch, repeated claim never permits a second transport call',async t=>{const f=await fixture(t),{group:g}=await f.budget.reserve(plan({summary:false})),r=request(g,'story');let calls=0;
 if((await f.budget.claim(r)).dispatch)calls++;if((await f.budget.claim(r)).dispatch)calls++;assert.equal(calls,1);
 await f.budget.uncertain(g.id,'story');assert.equal((await f.budget.claim(r)).dispatch,false);await reject(()=>f.budget.claim({...r,requestHash:sha256('other')}),'JOINT_REQUEST_COLLISION');
 await reject(()=>f.budget.release(g.id,'story'),'JOINT_DISPATCH_COUNT_RETAINED');assert.equal((await f.budget.status()).used,1);
});
test('M2 -> optional compression -> M3 order remains fixed; optional slot is released explicitly',async t=>{const f=await fixture(t),{group:g}=await f.budget.reserve(plan());await reject(()=>f.budget.claim(request(g,'memory-after')),'JOINT_STAGE_BLOCKED');
 await complete(f.budget,g,'story');await complete(f.budget,g,'summary-after');await reject(()=>f.budget.claim(request(g,'memory-after')),'JOINT_STAGE_BLOCKED');await f.budget.release(g.id,'compression-after');await complete(f.budget,g,'memory-after');
 assert.deepEqual(await f.budget.status(),{limit:20,used:3,reserved:0,remaining:17,revision:8,allowanceId:'local-fixture-allowance'});
});
for(const status of ['failed','cancelled'])test(status+' consumes its dispatched slot and blocks downstream until unused slots are released',async t=>{const f=await fixture(t),{group:g}=await f.budget.reserve(plan());await complete(f.budget,g,'story',status);await reject(()=>f.budget.claim(request(g,'summary-after')),'JOINT_STAGE_BLOCKED');await f.budget.releaseUnused(g.id);const view=await f.budget.status();assert.equal(view.used,1);assert.equal(view.reserved,0);assert.equal(view.remaining,19);assert.equal((await f.budget.reserve(g.plan)).replayed,true);});
test('unknown is counted across restart; original result settles without another claim',async t=>{const f=await fixture(t),{group:g}=await f.budget.reserve(plan({summary:false})),r=request(g,'story');await f.budget.claim(r);await f.budget.uncertain(g.id,'story');await f.reopen();assert.equal((await f.budget.read(g.id)).stages[0].state,'unknown');assert.equal((await f.budget.claim(r)).dispatch,false);
 await f.budget.settle({...r,status:'completed',resultHash:sha256('original')});assert.equal((await f.budget.status()).used,1);await complete(f.budget,g,'memory-after');assert.equal((await f.budget.status()).used,2);
});
test('unstarted stages release once and never become executable by repeating reservation',async t=>{const f=await fixture(t,{limit:2}),{group:g}=await f.budget.reserve(plan({summary:false}));await f.budget.releaseUnused(g.id);await f.budget.releaseUnused(g.id);assert.equal((await f.budget.status()).remaining,2);assert.equal((await f.budget.reserve(g.plan)).replayed,true);await reject(()=>f.budget.claim(request(g,'story')),'JOINT_STAGE_RELEASED');});
test('request IDs and result hashes cannot be moved between groups or changed after settlement',async t=>{const f=await fixture(t),{group:a}=await f.budget.reserve(plan({id:'A',summary:false})),{group:b}=await f.budget.reserve(plan({id:'B',summary:false}));const ra=await complete(f.budget,a,'story');await reject(()=>f.budget.claim({...request(b,'story'),requestId:ra.requestId}),'JOINT_REQUEST_COLLISION');
 await reject(()=>f.budget.settle({...ra,status:'completed',resultHash:sha256('changed')}),'JOINT_RESULT_COLLISION');assert.equal((await f.budget.claim(ra)).dispatch,false);
});
test('opening a used directory with another identity or larger limit cannot mint a fresh allowance',async t=>{const f=await fixture(t);await f.budget.reserve(plan());await f.budget.close();await reject(()=>JointBudget.open({directory:f.directory,allowanceId:'another',limit:20}),'JOINT_ALLOWANCE_CHANGED');await reject(()=>JointBudget.open({directory:f.directory,allowanceId:'local-fixture-allowance',limit:100}),'JOINT_ALLOWANCE_CHANGED');await f.reopen();assert.equal((await f.budget.status()).reserved,4);});
test('CAS failure before reservation commit leaves no partial group',async t=>{let armed=false;const f=await fixture(t,{storageFault:(phase)=>{if(armed&&phase==='beforeCommit'){armed=false;throw Error('disk');}}});armed=true;await reject(()=>f.budget.reserve(plan()),'OFFLINE_WRITE_FAILED');assert.equal((await f.budget.status()).reserved,0);assert.equal((await f.budget.reserve(plan())).replayed,false);});
test('uncertain reservation write requires reconciliation; recovered original group stays singular',async t=>{let armed=false;const f=await fixture(t,{storageFault:(phase)=>{if(armed&&phase==='afterCommit'){armed=false;throw Error('ack lost');}}});armed=true;await reject(()=>f.budget.reserve(plan()),'OFFLINE_WRITE_UNKNOWN');await f.reopen();const prior=await f.budget.reserve(plan());assert.equal(prior.replayed,true);await reject(()=>f.budget.claim(request(prior.group,'story')),'OFFLINE_STORAGE_RECOVERY_REQUIRED');assert.equal((await f.budget.reconcile()).status,'committed');assert.equal((await f.budget.status()).reserved,4);assert.equal((await f.budget.claim(request(prior.group,'story'))).dispatch,true);});
test('uncertain dispatch marker is never returned as new dispatch after recovery',async t=>{let armed=false;const f=await fixture(t,{storageFault:(phase)=>{if(armed&&phase==='afterCommit'){armed=false;throw Error('ack');}}});const {group:g}=await f.budget.reserve(plan()),r=request(g,'story');armed=true;await reject(()=>f.budget.claim(r),'OFFLINE_WRITE_UNKNOWN');await f.reopen();await f.budget.reconcile();assert.equal((await f.budget.claim(r)).dispatch,false);assert.equal((await f.budget.status()).used,1);});
test('resealed ledger cannot mark later stages as dispatched before earlier ones',async t=>{const f=await fixture(t),{group:g}=await f.budget.reserve(plan());await f.budget.close();const file=join(f.directory,'records','allowance.json'),raw=JSON.parse(await readFile(file,'utf8')),stage=raw.data.groups[0].stages.at(-1);Object.assign(stage,{state:'unknown',requestId:'illegal',requestHash:sha256('illegal')});raw.sha256=digest(raw.data);await writeFile(file,JSON.stringify(raw));await reject(()=>JointBudget.open({directory:f.directory,allowanceId:'local-fixture-allowance',limit:20}),'JOINT_BUDGET_ORDER_CORRUPT');});
test('empty update plan cannot reserve invented calls',async t=>{const f=await fixture(t);await reject(()=>f.budget.reserve(plan({kind:'update'})),'JOINT_NO_TASKS');assert.equal((await f.budget.status()).revision,0);});


test('host budget port rejects forged objects and ignores replaced instance methods',async t=>{
 for(const fake of [{},Object.create(JointBudget.prototype)])assert.throws(()=>requireJointBudget(fake),e=>e.code==='JOINT_BUDGET_REQUIRED');
 assert.throws(()=>new JointBudget(),e=>e.code==='JOINT_BUDGET_REQUIRED');
 const f=await fixture(t,{limit:2});let overrides=0;
 f.budget.reserve=async()=>{overrides++;return {group:{id:'fake'}};};
 f.budget.claim=async()=>{overrides++;return {dispatch:true};};
 const port=requireJointBudget(f.budget),{group}=await port.reserve(plan({summary:false})),r=request(group,'story');
 assert.equal(Object.isFrozen(port),true);assert.equal((await port.claim(r)).dispatch,true);assert.equal((await port.claim(r)).dispatch,false);
 assert.equal((await port.status()).used,1);assert.equal(overrides,0);
});


test('confirmed non-dispatch settles unknown without spending or erasing its original identity',async t=>{
 const f=await fixture(t,{limit:2}),{group:g}=await f.budget.reserve(plan({summary:false})),r=request(g,'story');await f.budget.claim(r);await f.budget.uncertain(g.id,'story');
 const result={...r,status:'not_dispatched',resultHash:sha256('durable control rejection')};await f.budget.settle(result);await f.budget.settle(result);
 await reject(()=>f.budget.claim(request(g,'memory-after')),'JOINT_STAGE_BLOCKED');await f.budget.releaseUnused(g.id);await f.reopen();
 assert.equal((await f.budget.status()).used,0);assert.equal((await f.budget.status()).remaining,2);assert.equal((await f.budget.claim(r)).dispatch,false);
 const saved=(await f.budget.read(g.id)).stages[0];assert.equal(saved.requestId,r.requestId);assert.equal(saved.resultHash,result.resultHash);
 await reject(()=>f.budget.settle({...result,status:'completed'}),'JOINT_RESULT_COLLISION');await reject(()=>f.budget.settle({...result,resultHash:sha256('changed')}),'JOINT_RESULT_COLLISION');
});
