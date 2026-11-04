import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {AuthoringStore} from './store.mjs';
import {blankDocument,canonical,sha256} from './contracts.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {OfflineFixtureProvider} from './offline-session.mjs';
import {JointSessionHost} from './joint-session.mjs';
import {JointBudget} from './joint-budget.mjs';
import {stageOperationId} from './joint-plan.mjs';
import {jointSessionId,validateJointSession} from './joint-session-state.mjs';
import {OfflineModelControl,modelCapacity} from './model-control.mjs';
import {memoryAt} from './memory-state.mjs';
import {memoryContextPolicy,assembleMemoryContext} from './memory-context.mjs';
import {createAuthoringHistoryControl} from './history-control.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6t-tests/',import.meta.url));await mkdir(root,{recursive:true});
const digest=v=>sha256(canonical(v)),clone=v=>structuredClone(v),reply=(text,finishReason='stop',status='completed')=>({status,text,finishReason});
const reject=(fn,code)=>assert.rejects(async()=>fn(),e=>{assert.equal(e.code,code,e.stack);return true;});
const id=jointSessionId('session'),opening='原始开局：雾港的一封信。',H='rpg.history-window',M2='rpg.rolling-summary',M3='rpg.memory-palace';
const deferred=()=>{let resolve;return {promise:new Promise(r=>{resolve=r;}),resolve};};
const catalog={catalogId:'joint-fixture',models:['story','summary','memory'].map(id=>({
 selectionId:id,selectionRevision:sha256(id),modelId:'fixture/'+id,name:'离线 '+id,available:true,contextLength:256000,maxOutputTokens:16000,
 parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]},
}))};
const proposal={operations:[{type:'create',name:'港口承诺',keywords:['雾港','信'],match:'any',roles:['user','assistant'],content:'双方计划次日见面，尚未兑现。',sourceTurns:[1]}]};
function replies(options={}){
 const result={};
 for(const name of ['one','two','three','four','five','manual','retry']){
  result[name]=reply(name==='one'?'古老灯塔。<details>卡内总结：约定明天见面</details>':'完整原文 '+name);
  for(const phase of ['summary-before','summary-after'])result[stageOperationId(id,name,phase)]=reply(options.summaryText??'两人有一个未兑现的港口承诺。');
  for(const phase of ['compression-before','compression-after'])result[stageOperationId(id,name,phase)]=reply('压缩后：承诺尚未兑现。');
  result[stageOperationId(id,name,'memory-after')]=reply(options.memoryText??JSON.stringify(name==='one'?proposal:{operations:[]}));
 }
 return {...result,...options.replies};
}
async function fixture(t,options={}){
 const directory=await mkdtemp(join(root,'run-'));let store=await AuthoringStore.open(join(directory,'works'));
 const doc=blankDocument();doc.metadata.name='独立作品 '+randomUUID();doc.metadata.audience='male';doc.sources.system=' FROZEN SYSTEM\r\n';doc.sources.prefix='PRE';doc.sources.suffix='POST';
 const work=await store.create({operationId:'work',expectedRevision:null,document:doc}),pub=await store.publish(work.workId,{operationId:'publish',expectedRevision:0}),reference={workId:work.workId,versionId:pub.result.versionId};
 const resolveRuntime=async ref=>{await options.beforeResolveRuntime?.();return openPublishedRuntime(store,ref);},modelControl=new OfflineModelControl(catalog);
 let host,control,provider,budget;
 async function open(faults=true){
  control=await createAuthoringHistoryControl({directory:join(directory,'plugins'),lookupSession:x=>host.lookupSession(x),resolveRuntime});
  provider=await OfflineFixtureProvider.open(join(directory,'provider'),{replies:replies(options),fault:faults?options.providerFault:undefined});
  budget=await JointBudget.open({directory:join(directory,'budget'),allowanceId:'shared',limit:options.limit??30,storageFault:faults?options.budgetFault:undefined});
  host=await JointSessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider,budget,modelControl,historyControl:control,fault:faults?options.fault:undefined,storageFault:faults?options.storageFault:undefined});
 }
 await open();await host.create({operationId:'session',expectedRevision:null,reference,openingText:opening,presetId:null});
 async function close(){await host.close();await control.close();await provider.close();await budget.close();}
 t.after(async()=>{await close();await store.close();});
 const f={directory,id,reference,get host(){return host;},get provider(){return provider;},get budget(){return budget;},get service(){return control.pluginService;},
  read:()=>host.read(id),idle:()=>host.waitForIdle(id),
  async select(purpose){const s=(await f.read()).session,c=modelControl.catalog();return host.configureModel({sessionId:id,operationId:randomUUID(),expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,
   choice:{catalogRevision:c.revision,selectionId:purpose,selectionRevision:sha256(purpose),parameters:{maxOutputTokens:4096}}});},
  async change(action,pluginId){const c=await f.service.catalog(),p=c.plugins.find(p=>p.id===pluginId);return f.service.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,
   ...(['enable','disable'].includes(action)?{sessionId:id,expectedSessionRevision:(await host.lookupSession(id)).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},
  async enable(plugin){await f.change('install',plugin);await f.change('enable',plugin);},
  async input(operationId='one',input=opening,kind='send'){const v=await f.read();return {sessionId:id,operationId,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,kind,input};},
  async send(...args){return host.send(await f.input(...args));},
  async sent(...args){const r=await f.send(...args);await f.idle();return r;},
  async update(operationId='manual'){return host.update(await f.input(operationId,null,'update'));},
  async recover(operationId='one'){const s=(await f.read()).session;return host.recover({sessionId:id,operationId,expectedRevision:s.revision,expectedSummaryRevision:s.summary.snapshot.revision,expectedMemoryRevision:s.memory.snapshot.revision});},
  async reopen(){await close();await store.close();store=await AuthoringStore.open(join(directory,'works'));await open(false);},
 };
 for(const p of ['story','summary','memory'])if(options[p+'Model']!==false)await f.select(p);
 if(options.summary!==false)await f.enable(M2);if(options.memory!==false)await f.enable(M3);
 return f;
}

import {createBranchCheckpoint,validateBranchCheckpoint,branchInheritance} from './branch-checkpoint.mjs';
import {BranchCheckpoints} from './branch-storage.mjs';
function intent(v,turn=1,operationId='branch-one') {return {sessionId:v.session.id,operationId,expectedRevision:v.session.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,turn,name:'新路线'};}
function freeze(v,turn=1,operationId='branch-one'){return createBranchCheckpoint(v.session,intent(v,turn,operationId),v.effectivePolicy.authorization);}
const checkError=(fn,code)=>assert.throws(fn,e=>{assert.equal(e.code,code,e.stack);return true;});
function reseal(c){const {sha256:ignored,...body}=c;c.sha256=digest(body);return c;}

test('settled branch snapshots and immutable storage',async t=>{
 const f=await fixture(t);await f.enable(H);let v=await f.read();
 await f.host.configureHistory({sessionId:id,operationId:'history-before',expectedRevision:v.session.revision,expectedHistoryRevision:v.session.history.revision,expectedContextPolicyRevision:v.historyConfigurationPolicyRevision,config:{turns:1,includeInitialCharacter:false}});
 await f.sent();v=await f.read();let entry=v.session.memory.snapshot.entries[0];
 await f.host.editMemory({sessionId:id,operationId:'before-branch-edit',expectedRevision:v.session.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,change:{action:'update',id:entry.id,baseVersion:entry.version,value:{...entry.value,content:'分支点前的人工约定，尚未兑现。'}}});
 await f.sent('two','带着信继续');const atTwo=await f.read(),early=freeze(atTwo,2);
 v=atTwo;entry=v.session.memory.snapshot.entries[0];
 await f.host.editMemory({sessionId:id,operationId:'future-edit',expectedRevision:v.session.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,change:{action:'update',id:entry.id,baseVersion:entry.version,value:{...entry.value,content:'FUTURE_ONLY 后来的情节，不能进入旧分支。'}}});
 v=await f.read();await f.host.configureHistory({sessionId:id,operationId:'future-history',expectedRevision:v.session.revision,expectedHistoryRevision:v.session.history.revision,expectedContextPolicyRevision:v.historyConfigurationPolicyRevision,config:{turns:5,includeInitialCharacter:true}});
 v=await f.read();const catalog=await f.host.modelCatalog(id),m=catalog.catalog;
 await f.host.configureModel({sessionId:id,operationId:'future-model',expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,purpose:'story',choice:{catalogRevision:m.revision,selectionId:'story',selectionRevision:sha256('story'),parameters:{maxOutputTokens:8192}}});
 await f.sent('three','父路线继续第三回合');const now=await f.read(),later=freeze(now,2,'branch-two'),count=f.provider.dispatchCount,budget=await f.budget.status();
 await t.test('dispatch and post-background states differ and both remain exact',()=>{
  assert.equal(early.dispatch.memory.revision,2);assert.equal(early.archive.memory.snapshot.revision,3);
  assert.equal(early.dispatch.summary.revision,0);assert.equal(early.archive.summary.snapshot.revision,1);
  assert.deepEqual(early.dispatch.memory,atTwo.session.operations[1].request.memoryContext.memorySnapshot);
  assert.equal(early.inheritance.memory.processedThrough,2);assert.equal(early.inheritance.summary.coveredThrough,1);
  assert.equal(early.inheritance.memory.entries[0].protected,true);assert.equal(early.inheritance.memory.entries[0].revisions.length,2);
 });
 await t.test('later edits, model and history changes, and future turns do not leak',()=>{
  assert.deepEqual(later.archive,early.archive);assert.deepEqual(later.inheritance,early.inheritance);assert.deepEqual(later.dispatch,early.dispatch);
  assert.equal(later.inheritance.turns.length,2);assert.ok(!canonical(later.archive).includes('FUTURE_ONLY'));
  assert.equal(later.inheritance.settings.history.turns,1);assert.equal(later.inheritance.settings.modelSuggestions.story.parameters.maxOutputTokens,4096);
 });
 await t.test('child projection has no grants, jobs, operations or request bodies',()=>{
  const child=branchInheritance(later);
  assert.deepEqual(Object.keys(child).sort(),['allowanceId','binding','examples','memory','presetId','settings','summary','turns']);
  assert.equal(child.allowanceId,now.session.allowanceId);assert.ok(!('sessionId' in child.binding));
  const forbidden=new Set(['authorization','jobs','operations','request','receipt','runId','operationId','expectedRevision']);
  const walk=x=>{if(x&&typeof x==='object')for(const [k,v]of Object.entries(x)){assert.ok(!forbidden.has(k),k);walk(v);}};walk(child);
  child.memory.entries[0].value.content='local mutation';assert.notEqual(later.inheritance.memory.entries[0].value.content,'local mutation');
 });
 await t.test('stale UI revisions, policy changes and invalid points reject',()=>{
  const a=now.effectivePolicy.authorization,i=intent(now,2);
  checkError(()=>createBranchCheckpoint(now.session,{...i,expectedRevision:i.expectedRevision-1},a),'BRANCH_SESSION_CONFLICT');
  checkError(()=>createBranchCheckpoint(now.session,{...i,expectedMemoryRevision:0},a),'BRANCH_MEMORY_CONFLICT');
  checkError(()=>createBranchCheckpoint(now.session,{...i,expectedContextPolicyRevision:'f'.repeat(64)},a),'BRANCH_POLICY_CONFLICT');
  checkError(()=>createBranchCheckpoint(now.session,{...i,turn:4},a),'BRANCH_POINT_UNSETTLED');
  for(const name of ['', ' '.repeat(4), 'a'.repeat(81), ' bad'])checkError(()=>createBranchCheckpoint(now.session,{...i,name},a),'BRANCH_INTENT_INVALID');
  checkError(()=>createBranchCheckpoint(now.session,{...i,operationId:'future-model'},a),'OFFLINE_OPERATION_COLLISION');
 });
 await t.test('resealing an altered inheritance or future archive cannot bypass validation',()=>{
  const a=clone(later);a.inheritance.memory.entries[0].value.content='tampered';checkError(()=>validateBranchCheckpoint(reseal(a)),'BRANCH_CHECKPOINT_INVALID');
  const b=clone(later);b.archive=clone(now.session);checkError(()=>validateBranchCheckpoint(reseal(b)),'BRANCH_CHECKPOINT_INVALID');
  const c=clone(later);c.dispatch.memory=clone(now.session.memory.snapshot);checkError(()=>validateBranchCheckpoint(reseal(c)),'BRANCH_CHECKPOINT_INVALID');
 });
 await t.test('atomic persistence, same operation replay, collision and restart',async()=>{
  const directory=join(f.directory,'branches');let store=await BranchCheckpoints.open({directory});
  try{
   const results=await Promise.all([store.save(early),store.save(early)]);assert.deepEqual(results.map(r=>r.replayed),[false,true]);
   assert.deepEqual(await store.read(early.id),early);
   const changed=freeze(atTwo,2);changed.intent.name='another';reseal(changed);await reject(()=>store.save(changed),'BRANCH_OPERATION_COLLISION');
   await store.close();store=await BranchCheckpoints.open({directory});assert.deepEqual(await store.read(early.id),early);assert.equal((await store.save(early)).replayed,true);
  }finally{await store.close();}
 });
 for(const phaseName of ['beforeCommit','afterCommit'])await t.test('storage failure '+phaseName+' recovers original ID without reconstruction',async()=>{
  const directory=join(f.directory,'fault-'+phaseName);let armed=true,store=await BranchCheckpoints.open({directory,storageFault:phase=>{if(armed&&phase===phaseName){armed=false;throw Error('disk');}}});
  try{
   await reject(()=>store.save(early),phaseName==='beforeCommit'?'OFFLINE_WRITE_FAILED':'OFFLINE_WRITE_UNKNOWN');
   await store.close();store=await BranchCheckpoints.open({directory});
   const recovery=await store.reconcileStorage();
   if(phaseName==='afterCommit'){assert.deepEqual(recovery,{id:early.id,status:'committed'});assert.deepEqual(await store.read(early.id),early);assert.equal((await store.save(early)).replayed,true);}
   else{assert.equal(recovery,null);assert.equal(await store.read(early.id),null);}
  }finally{await store.close();}
 });
 assert.equal(f.provider.dispatchCount,count);assert.deepEqual(await f.budget.status(),budget);assert.equal(count,8);
});

test('unknown background outcome blocks branch creation without refund or redispatch',async t=>{
 const f=await fixture(t,{providerFault:(phase,r)=>{if(phase==='afterReceipt'&&r.kind==='memory')throw Error('lost');}});await f.sent();const v=await f.read(),budget=await f.budget.status();
 checkError(()=>freeze(v),'BRANCH_RESULT_PENDING');assert.equal(f.provider.dispatchCount,2);assert.deepEqual(await f.budget.status(),budget);
});
test('a terminal rejected memory batch can be archived without pretending it succeeded',async t=>{
 const f=await fixture(t,{memoryText:'invalid proposal'});await f.sent();const v=await f.read(),c=freeze(v);
 assert.equal(c.archive.runs[0].state,'paused');assert.equal(c.archive.memory.jobs[0].state,'rejected');assert.equal(c.inheritance.memory.processedThrough,0);
 assert.equal(c.inheritance.turns.length,1);validateBranchCheckpoint(c);assert.equal(f.provider.dispatchCount,2);
});

test('zero-plugin branch remains full raw history and inherits no memory authority',async t=>{
 const f=await fixture(t,{summary:false,memory:false});await f.sent();const v=await f.read(),c=freeze(v);
 assert.equal(c.dispatch.memory.revision,0);assert.equal(c.inheritance.memory.processedThrough,0);assert.deepEqual(c.inheritance.memory.entries,[]);
 assert.equal(c.inheritance.summary.text,null);assert.equal(c.inheritance.turns[0].user,opening);assert.equal(c.inheritance.turns[0].assistant,v.session.turns[0].assistant);
 assert.equal(f.provider.dispatchCount,1);assert.equal((await f.budget.status()).used,1);
});
