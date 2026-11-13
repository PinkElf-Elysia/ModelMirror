import test from 'node:test';
import {spawnSync} from 'node:child_process';
const check=process.argv.includes('--crash-child')?()=>{}:test;
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {SessionRecords} from './session-storage.mjs';
import {OfflineModelControl} from './model-control.mjs';
import {sha256} from './contracts.mjs';
import {ComparisonLedger,comparisonLedgerPort} from './comparison-ledger.mjs';
import {ComparisonCoordinator} from './comparison-coordinator.mjs';
import {clone,digest,same,batchId,stageId,comparisonPlan,freezeContext,candidateRequest,resultEnvelope} from './comparison-contract.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/comparison-c1-tests/',import.meta.url));await mkdir(root,{recursive:true});
const reject=(fn,code)=>assert.rejects(fn,e=>e.code===code);
const field=(minimum,maximum,defaultValue,required,wireName)=>({minimum,maximum,defaultValue,required,wireName});
function catalog(){return {catalogId:'comparison-offline',models:['alpha','beta','small'].map(id=>({selectionId:id,selectionRevision:sha256(id),modelId:'fixture/'+id,name:id,available:true,contextLength:id==='small'?600:200000,maxOutputTokens:8192,
 parameterPolicy:{kind:'editable',fields:{temperature:field(0,2,.7,false,'temperature'),topP:field(0,1,.8,false,'top_p'),maxOutputTokens:field(1,8192,128,true,'max_tokens')},exclusive:[]}}))};}
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
async function eventually(fn){for(let i=0;i<500;i++){if(await fn())return;await delay(10);}assert.fail('timed out waiting for fixture state');}
async function fixture(t,options={}){
 const directory=options.directory??await mkdtemp(join(root,'run-')),control=new OfflineModelControl(catalog());
 const f={directory,calls:[],lookups:[],active:0,maximumActive:0,freezeCalls:0,applyCalls:[],restoreCalls:0,options};
 let hostRecords,providerRecords,local,remote,coordinator,hostQueue=Promise.resolve(),guardQueue=Promise.resolve();
 const choose=(id='alpha',temperature=.7)=>{const c=control.catalog(),entry=c.models.find(e=>e.selectionId===id);return control.select({catalogRevision:c.revision,selectionId:id,selectionRevision:entry.selectionRevision,parameters:{temperature,topP:.8,maxOutputTokens:128}});};
 f.choice=choose;
 const transact=fn=>{const p=hostQueue.then(async()=>{const r=await hostRecords.get('session'),v=await fn(r.data);await hostRecords.put('session',r.hash,r.data);return v;});hostQueue=p.catch(()=>{});return p;};
 f.state=async()=>clone((await hostRecords.get('session')).data);
 f.edit=transact;
 const host={
  async inspect(){const s=await f.state();return {contextRevision:s.contextRevision,authorizationRevision:s.auth,sourceHash:digest({history:s.history,requirements:s.requirements,variables:s.variables,summary:s.summary,memory:s.memory}),summaryBefore:!!options.before,summaryAfter:!!options.after,memoryAfter:!!options.memory};},
  async validateChoice(choice){control.assertCurrent(choice);if(options.validateChoice)await options.validateChoice(choice);},
  guard(session,auth,fn){const p=guardQueue.then(async()=>{if((await f.state()).auth!==auth)throw Object.assign(Error('revoked'),{code:'COMPARISON_AUTHORIZATION_CHANGED'});return fn();});guardQueue=p.catch(()=>{});return p;},
  freeze({plan,operationId}){return transact(s=>{if(s.freezes[operationId])return clone(s.freezes[operationId]);f.freezeCalls++;
   const source={input:plan.intent.input,history:clone(s.history),requirements:s.requirements,variables:clone(s.variables),summary:clone(s.summary),memory:clone(s.memory),worldbook:{draw:.37,selected:'唯一世界书正文'}};
   const value={contextRevision:s.contextRevision,source,messages:[{role:'system',content:'作者 system\n'+source.worldbook.selected+'\n'+s.summary.text+'\n'+s.memory.text},{role:'user',content:plan.intent.input+'\n'+s.requirements}]};
   s.freezes[operationId]=clone(value);return value;});},
  async prepareTask({plan,stage,context}){if(stage.purpose==='compression'&&!options.compress)return null;const s=await f.state();const messages=[{role:'system',content:stage.purpose+' instruction'},{role:'user',content:JSON.stringify(s.history)}],choice=choose('alpha',stage.purpose==='memory'?.3:.2);
   return {id:stage.id,operationId:stage.id,sessionId:plan.intent.sessionId,kind:stage.purpose,batchId:plan.id,contextHash:context?.hash??digest(s.history),messages,messagesHash:digest(messages),modelChoice:choice,parameters:clone(choice.wireParameters)};},
  async lookupCommit(p){const r=(await f.state()).operations[p.operationId];if(r&&r.payloadHash!==digest(p))throw Object.assign(Error(),{code:'HOST_OPERATION_COLLISION'});return clone(r??null);},
  apply(p){return transact(s=>{const prior=s.operations[p.operationId];if(prior){if(prior.payloadHash!==digest(p))throw Object.assign(Error(),{code:'HOST_OPERATION_COLLISION'});return clone(prior);}
   f.applyCalls.push(p.step);if(p.step==='formal'){
    if(s.contextRevision!==p.expectedContextRevision)throw Object.assign(Error(),{code:'HOST_CONTEXT_CHANGED'});
    s.history.push({user:p.context.source.input,assistant:p.result.raw});s.formal.push({input:p.context.source.input,raw:p.result.raw,model:p.request.modelChoice.entry.modelId,contextHash:p.context.hash});s.contextRevision++;
   }else if(p.step==='variables'){s.variables.updates++;s.variables.value=p.result.raw.match(/energy=(\d+)/)?.[1]??null;}
   else if(p.step==='cg'){s.cgUpdates++;if(p.result.raw.includes('CG:pier'))s.cg.push('pier');}
   else if(p.step.startsWith('summary')||p.step.startsWith('compression')){s.summary.updates++;s.summary.text=p.result.raw;}
   else if(p.step.startsWith('memory')){s.memory.updates++;s.memory.text=p.result.raw;}
   else assert.fail(p.step);
   const body={operationId:p.operationId,payloadHash:digest(p),contextRevision:s.contextRevision};const receipt={...body,hash:digest(body)};s.operations[p.operationId]=receipt;return clone(receipt);
  });},
  restoreDraft(p){return transact(s=>{const prior=s.operations[p.operationId];if(prior)return clone(prior);f.restoreCalls++;if(!s.draft||s.draft===p.input)s.draft=p.input;else s.recoverableDraft=p.input;
   const body={operationId:p.operationId,payloadHash:digest(p),contextRevision:s.contextRevision};return s.operations[p.operationId]={...body,hash:digest(body)};
  });},
 };
 const result=(request,status='completed',raw='完整回复 energy=7 CG:pier')=>resultEnvelope(request,{status,raw:status==='completed'?raw:'',receipt:{dispatched:status!=='not_dispatched',retries:0,requestId:request.id,requestHash:digest(request),modelId:request.modelChoice.entry.modelId,parameters:clone(request.parameters),messagesHash:request.messagesHash}});
 const provider={async dispatch(request){assert.equal(await providerRecords.get(request.id),null,'provider operation was re-dispatched');let r=await providerRecords.put(request.id,null,{request:clone(request),result:null});f.calls.push(clone(request));f.active++;f.maximumActive=Math.max(f.maximumActive,f.active);
   try{const body=options.dispatch?await options.dispatch(request,f):undefined;const res=body??result(request,'completed',request.kind==='story'?'完整回复 energy='+Math.round(request.parameters.temperature*10)+' CG:pier':request.kind+' 原文整理');
    if(res!==null){r=await providerRecords.put(request.id,r.hash,{request:clone(request),result:clone(res)});}if(options.loseReply?.(request))throw Error('response lost');return res;
   }finally{f.active--;}
  },async lookup(request){f.lookups.push(request.id);const prior=await providerRecords.get(request.id);if(!prior)return options.proveAbsent?result(request,'not_dispatched'):null;assert.equal(digest(prior.data.request),digest(request));return clone(prior.data.result);}};
 f.resolve=async(request,status='completed',raw='恢复的原结果 energy=9 CG:pier')=>{const r=await providerRecords.get(request.id);await providerRecords.put(request.id,r?.hash??null,{request:clone(request),result:result(request,status,raw)});};
 async function open(){
  hostRecords=await SessionRecords.open(join(directory,'host'),{recoverDeadOwner:!!options.recoverDeadOwner});if(!await hostRecords.get('session'))await hostRecords.put('session',null,{contextRevision:3,auth:sha256('authorized'),history:[{user:'早期输入',assistant:'早期原始回复'}],requirements:'持续要求',variables:{updates:0,value:null},summary:{updates:0,text:'已有摘要'},memory:{updates:0,text:'已有记忆'},formal:[],cg:[],cgUpdates:0,freezes:{},operations:{},draft:'',recoverableDraft:null});
  providerRecords=await SessionRecords.open(join(directory,'provider'),{recoverDeadOwner:!!options.recoverDeadOwner});
  local=await ComparisonLedger.open({directory:join(directory,'local'),level:'local',allowanceId:'local-test',limit:options.localLimit??40,storageFault:options.localFault,recoverDeadOwner:!!options.recoverDeadOwner});
  remote=await ComparisonLedger.open({directory:join(directory,'control'),level:'control',allowanceId:'control-test',limit:options.controlLimit??40,storageFault:options.controlFault,recoverDeadOwner:!!options.recoverDeadOwner});
  coordinator=await ComparisonCoordinator.open({directory:join(directory,'coordinator'),host,provider,localBudget:local,controlBudget:remote,fault:options.fault,storageFault:options.storageFault,timeoutMs:options.timeoutMs??10000,recoverDeadOwner:!!options.recoverDeadOwner});
 }
 await open();t.after(async()=>{await coordinator.close();await local.close();await remote.close();await hostRecords.close();await providerRecords.close();});
 Object.defineProperties(f,{coordinator:{get:()=>coordinator},local:{get:()=>local},remote:{get:()=>remote}});
 f.intent=async(op='batch',candidates=2)=>{const s=await f.state();return {sessionId:'session',operationId:op,expectedContextRevision:s.contextRevision,expectedAuthorizationRevision:s.auth,input:'原始输入，不包含任何候选',candidates:Array.from({length:candidates},(_,i)=>({id:'c'+(i+1),choice:choose(i%2?'beta':'alpha',.3+i*.1)}))};};
 f.create=async(op='batch',n=2)=>{const i=await f.intent(op,n);f.lastIntent=i;return coordinator.create(i);};
 f.wait=()=>coordinator.wait('session',batchId('session',f.lastIntent.operationId));
 f.read=()=>coordinator.read('session',batchId('session',f.lastIntent.operationId));
 f.adopt=async(candidateId='c1',operationId='adopt')=>{const b=await f.read();return coordinator.adopt('session',b.plan.id,{operationId,candidateId,expectedContextRevision:b.context.contextRevision});};
 f.discard=async()=>{const b=await f.read();return coordinator.discard('session',b.plan.id,{operationId:'discard',expectedContextRevision:b.context?.contextRevision??b.plan.state.contextRevision});};
 f.reopen=async()=>{await coordinator.close();await local.close();await remote.close();await hostRecords.close();await providerRecords.close();await open();};
 f.reconcile=async()=>coordinator.reconcile('session',(await f.read()).plan.id);
 f.result=result;return f;
}

check('common context is frozen once; candidates retain independent model/parameter requests without formal effects',async t=>{
 const f=await fixture(t);await f.create();const b=await f.wait();assert.equal(b.status,'awaiting-choice');assert.equal(f.calls.length,2);assert.equal(f.freezeCalls,1);
 assert.equal(f.calls[0].messagesHash,f.calls[1].messagesHash);assert.notEqual(digest(f.calls[0]),digest(f.calls[1]));assert.equal(b.context.source.worldbook.draw,.37);
 const s=await f.state();assert.equal(s.formal.length,0);assert.equal(s.variables.updates,0);assert.equal(s.cgUpdates,0);assert.equal(s.summary.updates,0);assert.equal(s.memory.updates,0);
 assert.deepEqual((await f.local.status()).used,2);assert.equal((await f.remote.status()).used,2);await reject(()=>f.coordinator.assertIdle('session'),'COMPARISON_SESSION_BUSY');
});

check('preflight, duplicate create and reads do not consume extra slots or regenerate',async t=>{
 const f=await fixture(t),i=await f.intent();assert.equal((await f.coordinator.preflight(i)).dispatched,false);assert.equal((await f.local.status()).reserved,0);
 await f.coordinator.create(i);f.lastIntent=i;await f.wait();await f.coordinator.create(i);await f.read();assert.equal(f.calls.length,2);assert.equal(f.freezeCalls,1);
 await reject(()=>f.coordinator.create({...i,input:'changed'}),'COMPARISON_OPERATION_COLLISION');
});

check('two concurrent creates with same operation share one batch and one set of calls',async t=>{
 const f=await fixture(t),i=await f.intent();f.lastIntent=i;const results=await Promise.allSettled([f.coordinator.create(i),f.coordinator.create(i)]);await f.wait();
 assert.equal(results.filter(r=>r.status==='fulfilled').length,2);assert.equal((await f.coordinator.list('session')).length,1);assert.equal(f.calls.length,2);
});

for(const level of ['local','control'])check(level+' insufficient whole allowance blocks every provider call',async t=>{
 const f=await fixture(t,{[level+'Limit']:1});await reject(()=>f.create(),'COMPARISON_BUDGET_EXHAUSTED');assert.equal(f.calls.length,0);assert.equal((await f.local.status()).used,0);assert.equal((await f.remote.status()).reserved,0);
});

check('full nine-slot plan reserves before pre-summary and releases unused compression slots',async t=>{
 const gate=deferred();let started=false;const f=await fixture(t,{before:true,after:true,memory:true,dispatch:async(r)=>{if(!started){started=true;await gate.promise;}return undefined;}});
 await f.create('nine',4);await eventually(()=>started);assert.equal((await f.local.status()).used+(await f.local.status()).reserved,9);assert.equal((await f.remote.status()).used+(await f.remote.status()).reserved,9);
 gate.resolve();await f.wait();assert.equal(f.calls.filter(r=>r.kind==='summary').length,1);assert.equal(f.freezeCalls,1);assert.match((await f.read()).context.source.summary.text,/summary 原文整理/);
 await f.adopt();await f.wait();assert.deepEqual(f.calls.map(r=>r.kind),['summary','story','story','story','story','summary','memory']);assert.equal((await f.local.status()).used,7);assert.equal((await f.local.status()).reserved,0);assert.equal((await f.remote.status()).used,7);
});

check('at most two candidate calls run concurrently and a completed candidate may be adopted early',async t=>{
 const a=deferred(),b=deferred(),c=deferred(),d=deferred();const gates=[a,b,c,d];let storyIndex=0;
 const f=await fixture(t,{after:true,memory:true,dispatch:async(r)=>{if(r.kind==='story')await gates[storyIndex++].promise;}});
 await f.create('parallel',4);await eventually(()=>f.calls.length===2);assert.equal(f.maximumActive,2);a.resolve();await eventually(async()=>f.calls.length===3&&(await f.read()).tasks.some(t=>t.state==='completed'));
 await f.adopt('c1');assert.equal((await f.state()).formal.length,1);assert.equal((await f.state()).memory.updates,0);assert.equal(f.calls.length,3);
 await reject(()=>f.coordinator.assertIdle('session'),'COMPARISON_SESSION_BUSY');b.resolve();c.resolve();d.resolve();await f.wait();assert.equal(f.maximumActive,2);assert.equal((await f.state()).memory.updates,1);assert.equal((await f.read()).status,'settled');await f.coordinator.assertIdle('session');
});

check('candidate progress never invalidates legal adoption; multitab different picks conflict',async t=>{
 const f=await fixture(t,{after:true,memory:true});await f.create();const first=await f.wait();const revision=first.context.contextRevision;
 const outcomes=await Promise.allSettled(['c1','c2'].map((candidateId,i)=>f.coordinator.adopt('session',first.plan.id,{operationId:'adopt-'+i,expectedContextRevision:revision,candidateId})));
 assert.equal(outcomes.filter(x=>x.status==='fulfilled').length,1);assert.equal(outcomes.find(x=>x.status==='rejected').reason.code,'COMPARISON_DECISION_CONFLICT');await f.wait();assert.equal((await f.state()).formal.length,1);assert.equal((await f.state()).variables.updates,1);assert.equal((await f.state()).cgUpdates,1);assert.equal(f.calls.length,4);
 const decision=(await f.read()).decision;await f.coordinator.adopt('session',first.plan.id,decision.intent);assert.equal((await f.state()).formal.length,1);assert.equal(f.calls.length,4);
});

check('unselected raw never reaches formal history, variable/CG projections or M2/M3 sources',async t=>{
 const f=await fixture(t,{after:true,memory:true,dispatch:async(r,f)=>f.result(r,'completed',r.kind==='story'?(r.modelChoice.entry.modelId==='fixture/alpha'?'SELECTED energy=11 CG:pier':'UNSELECTED energy=999 CG:secret'):'task done')});
 await f.create();await f.wait();await f.adopt();const s=await f.state();assert.equal(s.formal[0].raw,'SELECTED energy=11 CG:pier');assert.equal(s.variables.value,'11');assert.deepEqual(s.cg,['pier']);
 for(const r of f.calls.filter(r=>r.kind!=='story'))assert.ok(!JSON.stringify(r.messages).includes('UNSELECTED'));assert.equal(s.memory.updates,1);assert.equal(s.summary.updates,1);
});

check('discard preserves comparison and newer draft, releases unstarted post tasks, and is idempotent',async t=>{
 const f=await fixture(t,{after:true,memory:true});await f.create();await f.wait();await f.edit(s=>{s.draft='new draft';});await f.discard();await f.discard();assert.equal((await f.read()).status,'settled');
 const s=await f.state();assert.equal(s.draft,'new draft');assert.equal(s.recoverableDraft,f.lastIntent.input);assert.equal(s.formal.length,0);assert.equal(f.restoreCalls,1);assert.equal((await f.local.status()).used,2);assert.equal((await f.local.status()).reserved,0);
 await reject(()=>f.adopt(),'COMPARISON_DECISION_CONFLICT');
});

check('stop only releases queued candidates; active calls remain charged and readable',async t=>{
 const gate=deferred();const f=await fixture(t,{dispatch:()=>gate.promise});await f.create('stop',4);await eventually(()=>f.calls.length===2);let b=await f.read();const op={operationId:'stop-queued',expectedRevision:b.revision};await f.coordinator.stop('session',b.plan.id,op);await f.coordinator.stop('session',b.plan.id,op);
 assert.equal((await f.local.status()).used,2);assert.equal((await f.local.status()).reserved,0);gate.resolve();await f.wait();assert.equal(f.calls.length,2);b=await f.read();assert.equal(b.tasks.filter(t=>t.state==='released').length,2);await f.adopt();assert.equal((await f.state()).formal.length,1);
});

check('unknown occupies concurrency slot; original ID lookup restores without dispatch',async t=>{
 const f=await fixture(t,{dispatch:async()=>{throw Error('network unknown');}});await f.create('unknown',4);await f.wait();assert.equal(f.calls.length,2);assert.equal((await f.read()).tasks.filter(t=>t.state==='unknown').length,2);
 const ids=f.calls.map(r=>r.id);await f.reopen();assert.equal(f.calls.length,2);for(const r of f.calls)await f.resolve(r);await f.reconcile();assert.deepEqual(f.lookups,ids);assert.equal(f.calls.length,2);assert.equal(f.freezeCalls,1);assert.equal((await f.read()).tasks.filter(t=>t.state==='released').length,2);
 await f.adopt();assert.equal((await f.state()).formal.length,1);assert.equal((await f.local.status()).used,2);
});

check('lost provider reply uses durable original receipt and retains failed diagnostic',async t=>{
 const f=await fixture(t,{loseReply:()=>true});await f.create();await f.wait();assert.equal((await f.read()).tasks[0].state,'unknown');await f.reopen();await f.reconcile();assert.equal(f.calls.length,2);assert.equal((await f.read()).tasks[0].state,'completed');assert.ok((await f.read()).diagnostic);await f.adopt();assert.equal((await f.state()).formal.length,1);
});

check('host failure before transport proves non-dispatch and releases both holds',async t=>{
 let armed=true;const f=await fixture(t,{proveAbsent:true,fault:(phase)=>{if(armed&&phase==='beforeDispatch')throw Error('before network');}});await f.create();await f.wait();assert.equal(f.calls.length,0);assert.equal((await f.local.status()).used,0);assert.equal((await f.local.status()).reserved,0);armed=false;
 await f.reopen();await f.reconcile();assert.equal((await f.local.status()).used,0);assert.equal((await f.remote.status()).used,0);assert.equal((await f.read()).tasks[0].state,'not_dispatched');
});

for(const point of ['afterAdoptionDecision','formal','variables','cg'])check('adoption resumes after '+point+' with one formal turn and one projection per step',async t=>{
 let armed=true;const f=await fixture(t,{after:true,memory:true,fault:(phase,v)=>{if(armed&&(phase===point||phase==='afterCommitStep'&&v.step===point)){armed=false;throw Error('crash');}}});
 await f.create();await f.wait();await f.adopt().catch(()=>{});await f.reopen();await f.reconcile();await f.wait();const s=await f.state();assert.equal(s.formal.length,1);assert.equal(s.variables.updates,1);assert.equal(s.cgUpdates,1);assert.equal(s.summary.updates,1);assert.equal(s.memory.updates,1);assert.equal(f.calls.length,4);assert.equal((await f.read()).status,'settled');
});

check('revoked candidate cannot be adopted, but original late result is still retained',async t=>{
 const gate=deferred();const f=await fixture(t,{dispatch:()=>gate.promise});await f.create();await eventually(()=>f.calls.length===2);await f.edit(s=>{s.auth=sha256('revoked');});gate.resolve();await f.wait();await reject(()=>f.adopt(),'COMPARISON_AUTHORIZATION_CHANGED');assert.equal((await f.read()).tasks.filter(t=>t.state==='completed').length,2);assert.equal((await f.state()).formal.length,0);assert.equal((await f.local.status()).used,2);
});

check('formal commit ack lost then revoked still recovers its existing local projections',async t=>{
 let armed=true;const f=await fixture(t,{fault:(phase,v)=>{if(armed&&phase==='afterCommitStep'&&v.step==='formal'){armed=false;throw Error('ack lost');}}});await f.create();await f.wait();await f.adopt();await f.edit(s=>{s.auth=sha256('revoked');});await f.reopen();await f.reconcile();assert.equal((await f.state()).formal.length,1);assert.equal((await f.state()).variables.updates,1);assert.equal((await f.read()).status,'settled');
});

check('small model blocks every candidate, no silent common-context trimming',async t=>{
 const f=await fixture(t);await f.edit(s=>{s.requirements='完整要求'.repeat(1000);});const i=await f.intent();i.candidates[1].choice=f.choice('small');f.lastIntent=i;await f.coordinator.create(i);const b=await f.wait();assert.equal(b.status,'blocked');assert.equal(b.diagnostic,'MODEL_CONTEXT_CAPACITY');assert.equal(f.calls.length,0);assert.equal(f.freezeCalls,1);await f.discard();assert.equal((await f.local.status()).remaining,40);
});

check('invalid receipts stay unknown, cannot be adopted or unlock formal state',async t=>{
 const f=await fixture(t,{dispatch:async(r,f)=>{const x=f.result(r);x.raw='tampered';return x;}});await f.create();await f.wait();await reject(()=>f.adopt(),'COMPARISON_CANDIDATE_NOT_READY');assert.equal((await f.read()).tasks[0].state,'unknown');assert.equal((await f.local.status()).used,2);assert.equal((await f.state()).cgUpdates,0);
});

check('host context CAS rejects stale adoption even when candidate is ready',async t=>{
 const f=await fixture(t);await f.create();await f.wait();await f.edit(s=>{s.contextRevision++;});const b=await f.adopt();assert.equal(b.status,'blocked');assert.equal(b.diagnostic,'HOST_CONTEXT_CHANGED');assert.equal((await f.state()).formal.length,0);
});

check('same model with distinct parameters is supported and does not change host defaults',async t=>{
 const f=await fixture(t);const i=await f.intent();i.candidates[1].choice=f.choice('alpha',1.2);f.lastIntent=i;await f.coordinator.create(i);await f.wait();assert.equal(f.calls[0].modelChoice.entry.modelId,f.calls[1].modelChoice.entry.modelId);assert.notEqual(f.calls[0].parameters.temperature,f.calls[1].parameters.temperature);await f.adopt('c2');assert.equal((await f.state()).formal[0].model,'fixture/alpha');assert.equal(f.choice().parameters.temperature,.7);
});

check('reservation acknowledgment loss across restart blocks dispatch until original reservation is checked',async t=>{
 let armed=true;const f=await fixture(t,{controlFault:(phase,v)=>{if(armed&&phase==='afterCommit'&&v.data.groups?.length){armed=false;throw Error('ack');}}});const b=await f.create();assert.equal(b.status,'blocked');assert.equal(f.calls.length,0);await f.reopen();await f.reconcile();assert.equal(f.calls.length,0);assert.equal((await f.local.status()).reserved,0);assert.equal((await f.remote.status()).reserved,0);await f.discard();
});

check('failed second reservation releases first without inventing a provider attempt',async t=>{
 let once=true;const f=await fixture(t,{controlFault:(phase,v)=>{if(once&&phase==='beforeCommit'&&v.data.groups?.length){once=false;throw Error('disk');}}});await f.create();assert.equal(f.calls.length,0);await f.reopen();await f.reconcile();assert.equal((await f.local.status()).reserved,0);assert.equal((await f.remote.status()).used,0);await f.discard();
});

check('result persistence interruption recovers from original provider ID without retry',async t=>{
 let armed=true;const f=await fixture(t,{storageFault:(phase,v)=>{if(armed&&phase==='afterCommit'&&v.data.batches?.some(b=>b.tasks.some(t=>t.result))){armed=false;throw Error('disk ack');}}});await f.create();await f.wait();const calls=f.calls.length;await f.reopen();await f.reconcile();assert.equal(f.calls.length,calls);assert.ok((await f.read()).tasks.some(t=>t.state==='completed'));await f.adopt();assert.equal((await f.state()).formal.length,1);
});

check('one candidate failure does not discard another success and both attempts count',async t=>{
 const f=await fixture(t,{dispatch:async(r,f)=>f.result(r,r.modelChoice.entry.modelId==='fixture/beta'?'failed':'completed')});await f.create();await f.wait();await f.adopt('c1');assert.equal((await f.read()).status,'settled');assert.equal((await f.state()).formal.length,1);assert.equal((await f.local.status()).used,2);
});

check('post-summary failure pauses memory and retains the already adopted formal turn',async t=>{
 const f=await fixture(t,{after:true,memory:true,dispatch:async(r,f)=>f.result(r,r.kind==='summary'?'failed':'completed')});await f.create();await f.wait();await f.adopt();assert.equal((await f.read()).status,'blocked');assert.equal((await f.state()).formal.length,1);assert.equal(f.calls.some(r=>r.kind==='memory'),false);await f.reconcile();assert.equal(f.calls.length,3);
});

check('ledger serializes full reservations across sessions, rejects identity reset and replay claim',async t=>{
 const f=await fixture(t,{localLimit:2});const i=await f.intent(),state=await f.coordinator.preflight(i);const plans=[state.plan,comparisonPlan({...i,sessionId:'other',operationId:'other'},state.plan.state)];
 const rs=await Promise.allSettled(plans.map(p=>f.local.reserve(p)));assert.equal(rs.filter(r=>r.status==='fulfilled').length,1);assert.equal(rs.find(r=>r.status==='rejected').reason.code,'COMPARISON_BUDGET_EXHAUSTED');
 const plan=rs[0].status==='fulfilled'?plans[0]:plans[1],stage=plan.stages[0],hash=sha256('request');assert.equal((await f.local.claim(plan.id,stage.id,hash)).dispatch,true);assert.equal((await f.local.claim(plan.id,stage.id,hash)).dispatch,false);
 await reject(()=>f.local.release(plan.id,stage.id),'COMPARISON_DISPATCH_RETAINED');await f.local.close();await reject(()=>ComparisonLedger.open({directory:join(f.directory,'local'),level:'local',allowanceId:'local-test',limit:100}),'COMPARISON_ALLOWANCE_CHANGED');
 // Restore the exact ledger identity before fixture cleanup.
 const replacement=await ComparisonLedger.open({directory:join(f.directory,'local'),level:'local',allowanceId:'local-test',limit:2});await replacement.close();
});

check('input and protocol boundaries reject provider fields, too many candidates, and arbitrary ledger objects',async t=>{
 const f=await fixture(t),i=await f.intent();await reject(()=>f.coordinator.create({...i,providerUrl:'https://example.invalid'}),'AUTHORING_UNKNOWN_FIELD');await reject(()=>f.coordinator.create({...i,candidates:Array.from({length:5},(_,n)=>({...i.candidates[0],id:'n'+n}))}),'COMPARISON_INPUT_INVALID');
 assert.throws(()=>comparisonLedgerPort({reserve(){}}),e=>e.code==='COMPARISON_LEDGER_REQUIRED');assert.equal(f.calls.length,0);
});


check('timeout retains unknown slots; close does not cancel the original provider request',async t=>{
 const gate=deferred();const f=await fixture(t,{timeoutMs:25,dispatch:()=>gate.promise});await f.create('timeout',4);await f.wait();assert.equal(f.calls.length,2);assert.equal(f.active,2);assert.equal((await f.read()).tasks.filter(t=>t.state==='unknown').length,2);
 gate.resolve();await eventually(()=>f.active===0);await f.reopen();await f.reconcile();assert.equal(f.calls.length,2);await f.adopt();assert.equal((await f.state()).formal.length,1);
});

check('mutation operation IDs cannot be repurposed between creation, stop and adoption',async t=>{
 const f=await fixture(t);await f.create();const b=await f.wait();await reject(()=>f.coordinator.stop('session',b.plan.id,{operationId:'batch',expectedRevision:b.revision}),'COMPARISON_OPERATION_COLLISION');
 await reject(()=>f.adopt('c1','batch'),'COMPARISON_OPERATION_COLLISION');
});


for(const stage of ['summary-before','summary-after','memory-after'])check('background apply acknowledgment loss at '+stage+' never repeats a model or state update',async t=>{
 let armed=true;const before=stage==='summary-before';const f=await fixture(t,{before,after:!before,memory:!before,fault:(phase,v)=>{if(armed&&phase==='afterTaskApply'&&v.stage===stage){armed=false;throw Error('state ack lost');}}});
 await f.create();await f.wait();if(!before)await f.adopt();const calls=f.calls.length;await f.reopen();await f.reconcile();const s=await f.state();
 if(before){assert.equal(s.summary.updates,1);assert.equal(f.calls.length,calls);assert.equal(f.freezeCalls,0);assert.equal(s.formal.length,0);await f.discard();}
 else{assert.equal(s.summary.updates,1);assert.equal(s.memory.updates,1);assert.equal(s.formal.length,1);assert.equal(f.calls.length,4);assert.equal((await f.read()).status,'settled');}
 assert.ok((await f.read()).events.some(e=>e.type==='failure'));
});

check('optional compression has separate identities before and after adoption and runs in order',async t=>{
 const f=await fixture(t,{before:true,after:true,memory:true,compress:true});await f.create();await f.wait();await f.adopt();assert.deepEqual(f.calls.map(r=>r.kind),['summary','compression','story','story','summary','compression','memory']);
 assert.equal(new Set(f.calls.map(r=>r.operationId)).size,7);assert.equal((await f.local.status()).used,7);assert.equal((await f.local.status()).reserved,0);
});

check('catalog revalidation failure cannot start a request using changed model capabilities',async t=>{
 let checks=0;const f=await fixture(t,{validateChoice:()=>{if(++checks>2)throw Object.assign(Error(),{code:'MODEL_CAPABILITY_CHANGED'});}});await f.create();await f.wait();assert.equal(f.calls.length,0);assert.equal((await f.local.status()).used,0);assert.ok((await f.read()).events.some(e=>e.code==='MODEL_CAPABILITY_CHANGED'));assert.equal((await f.remote.status()).reserved,0);assert.equal((await f.read()).tasks.filter(t=>t.state==='not_dispatched').length,2);
});

check('ledger terminal receipt cannot be rewritten, and released slots cannot be reclaimed',async t=>{
 const f=await fixture(t,{after:true});await f.create();await f.wait();const b=await f.read(),task=b.tasks.find(t=>t.state==='completed');await reject(()=>f.local.settle(b.plan.id,task.id,digest(task.request),{status:'completed',hash:sha256('changed')}),'COMPARISON_RESULT_CONFLICT');
 await f.discard();const released=(await f.local.read(b.plan.id)).tasks.find(t=>t.state==='released');await reject(()=>f.local.claim(b.plan.id,released.id,sha256('invented')),'COMPARISON_CLAIM_CONFLICT');
});

check('shared-record compare and swap survives a failed write without creating a second formal turn',async t=>{
 let armed=false;const f=await fixture(t,{storageFault:(phase,v)=>{if(armed&&phase==='beforeCommit'&&v.data.batches?.some(b=>b.decision)){armed=false;throw Error('disk full');}}});await f.create();await f.wait();armed=true;await assert.rejects(()=>f.adopt());assert.equal((await f.state()).formal.length,0);await f.adopt();assert.equal((await f.state()).formal.length,1);assert.equal(f.calls.length,2);
});


check('candidate completion racing the adoption checkpoint cannot strand post-adoption jobs',async t=>{
 const candidate=deferred(),checkpoint=deferred();let reached=false,once=true;
 const f=await fixture(t,{after:true,memory:true,dispatch:async r=>{if(r.kind==='story'&&r.modelChoice.entry.modelId==='fixture/beta')await candidate.promise;},fault:async phase=>{if(once&&phase==='beforePostCheck'){once=false;reached=true;await checkpoint.promise;}}});
 await f.create();await eventually(async()=>{const b=await f.read();return b.tasks.find(t=>t.id===b.plan.stages.find(s=>s.candidateId==='c1').id).state==='completed';});
 const adoption=f.adopt();await eventually(()=>reached);candidate.resolve();await eventually(async()=>{const b=await f.read();return b.tasks.filter(t=>t.request?.kind==='story').every(t=>t.state==='completed');});checkpoint.resolve();await adoption;await f.wait();
 assert.equal((await f.read()).status,'settled');assert.equal((await f.state()).formal.length,1);assert.equal((await f.state()).summary.updates,1);assert.equal((await f.state()).memory.updates,1);assert.equal(f.calls.length,4);
});


check('real process exit after formal write recovers without redrawing or dispatching again',async t=>{
 const directory=await mkdtemp(join(root,'process-'));
 const run=spawnSync(process.execPath,[fileURLToPath(import.meta.url),'--crash-child',directory],{encoding:'utf8',windowsHide:true,timeout:20000});assert.equal(run.status,86,run.stderr);
 const f=await fixture(t,{directory,recoverDeadOwner:true});f.lastIntent=(await f.coordinator.list('session'))[0].plan.intent;
 assert.equal((await f.state()).formal.length,1);assert.equal((await f.state()).variables.updates,0);await f.reconcile();const s=await f.state();assert.equal(s.formal.length,1);assert.equal(s.variables.updates,1);assert.equal(s.cgUpdates,1);assert.equal(f.calls.length,0);assert.equal(Object.keys(s.freezes).length,1);assert.equal((await f.local.status()).used,2);assert.equal((await f.remote.status()).used,2);assert.equal((await f.read()).status,'settled');
 await writeFile(join(directory,'crash-recovery-evidence.json'),JSON.stringify({exitCode:run.status,newProviderCalls:f.calls.length,formalTurns:s.formal.length,variableUpdates:s.variables.updates,cgUpdates:s.cgUpdates,freezeCount:Object.keys(s.freezes).length,batch:await f.read()},null,2));
});

check('swapped or reused budget layers cannot stand in for two independent reservations',async t=>{
 const f=await fixture(t);await reject(()=>ComparisonCoordinator.open({directory:join(f.directory,'invalid-coordinator'),localBudget:f.local,controlBudget:f.local}),'COMPARISON_LEDGER_LEVEL_INVALID');assert.equal(f.calls.length,0);
});

if(process.argv.includes('--crash-child')){
 const directory=process.argv[process.argv.indexOf('--crash-child')+1];
 const f=await fixture({after(){}},{directory,fault:(phase,value)=>{if(phase==='afterCommitStep'&&value.step==='formal')process.exit(86);}});
 await f.create('crash');await f.wait();await f.adopt();process.exit(99);
}


check('unconfirmed original lookup never refunds a dispatched unknown request',async t=>{
 const f=await fixture(t,{dispatch:async()=>{throw Error('upstream disconnected');}});await f.create();await f.wait();await f.reopen();await f.reconcile();assert.equal((await f.local.status()).used,2);assert.equal((await f.remote.status()).used,2);assert.equal((await f.read()).tasks.filter(t=>t.state==='unknown').length,2);assert.equal(f.calls.length,2);assert.equal(f.lookups.length,2);await reject(()=>f.adopt(),'COMPARISON_CANDIDATE_NOT_READY');
});
