import {emptyRequirements,requirementsIntent,REQUIREMENTS_CHANGES} from './requirements.mjs';
import {controlledProviderPort} from './controlled-provider.mjs';
import {CONTROLLED_MODE,CONTROLLED_SESSION_FORMAT,isControlledSession} from './controlled-receipt.mjs';
import {MODEL_PLUGIN,commitPluginModel,checkPluginCatalog} from './model-plugin.mjs';
import {BranchCheckpoints} from './branch-storage.mjs';
import {branchIntent,branchCheckpointId,createBranchCheckpoint,createInheritedBranchCheckpoint} from './branch-checkpoint.mjs';
import {branchOrigin} from './branch-origin.mjs';
import {createBranchSession,branchChildId,branchCreateHash} from './branch-session.mjs';
import {readFile} from 'node:fs/promises';
import {randomBytes} from 'node:crypto';
import {canonical,keys,isId,isHash,sha256,fail} from './contracts.mjs';
import {SessionRecords,SESSION_RECORD_LIMIT} from './session-storage.mjs';
import {OfflineFixtureProvider} from './offline-session.mjs';
import {jointFixturePort} from './joint-fixture.mjs';
import {JointBudget,requireJointBudget} from './joint-budget.mjs';
import {requirePublishedRuntime,runtimeReference} from './runtime-binding.mjs';
import {dialoguePreview} from './dialogue.mjs';
import {buildWorldbookPlan} from './worldbook.mjs';
import {checkPlayerInput} from './input-assistance.mjs';
import {requireOfflineModelControl,emptyModelState,validateModelIntent,modelCapacity,MODEL_CHANGE_LIMIT} from './model-control.mjs';
import {requireHistoryControl,emptyHistoryState,validateHistoryIntent,contextPolicyRevision,HISTORY_CHANGE_LIMIT} from './history-control.mjs';
import {memoryContextPolicy,assembleMemoryContext} from './memory-context.mjs';
import {createSummaryCompression,commitSummaryUpdate,inspectSummaryResult} from './summary-state.mjs';
import {settleSummaryJob} from './summary-jobs.mjs';
import {initialJointSummary,freezeJointSummary,pendingJointSummary,jointCompressionId,summaryJobCandidates,SUMMARY_PENDING,SUMMARY_LOCAL_FAILURES,SUMMARY_RECEIPT_RESERVE} from './joint-summary.mjs';
import {initialMemoryJobs,freezeMemoryJob,autoMemoryIntent,pendingMemory,settleMemoryJob,MEMORY_LOCAL_FAILURES} from './memory-jobs.mjs';
import {commitMemoryUpdate,inspectMemoryResult,reviseMemory} from './memory-state.mjs';
import {JOINT_MODE,JOINT_SESSION_FORMAT,JOINT_LIMITS,digest,same,clone,integer,raw,jointSessionId,jointRequestId,jointGroupId,jointIntent,checkSession,
 validateJointSession,jointPlan,assertJointIdFree,pauseReason,runPending,STORY_PENDING,outcomeState,storyReceipt,turnValue} from './joint-session-state.mjs';
export {JOINT_MODE,JOINT_SESSION_FORMAT};
const timestamp=()=>new Date().toISOString(),sample=()=>randomBytes(6).readUIntBE(0,6)/281474976710656;
const paths=['requirements.mjs','controlled-provider.mjs','controlled-receipt.mjs','model-plugin.mjs','branch-origin.mjs','branch-checkpoint.mjs','branch-storage.mjs','branch-session.mjs','joint-fixture.mjs','joint-session.mjs','joint-session-state.mjs','joint-summary.mjs','joint-plan.mjs','joint-budget.mjs','memory-state.mjs','memory-jobs.mjs','memory-context.mjs',
 'summary-state.mjs','summary-context.mjs','summary-jobs.mjs','history-control.mjs','model-control.mjs','session-storage.mjs','runtime-binding.mjs','runtime-plugins.mjs',
 'contracts.mjs','offline-session.mjs','worldbook.mjs','worldbook-regex.mjs','input-assistance.mjs','dialogue.mjs','../plugins/host.mjs','../plugins/memory-palace.mjs','../plugins/history-window.mjs','../plugins/rolling-summary.mjs'];
const artifacts=async()=>Object.fromEntries(await Promise.all(paths.map(async p=>[p,sha256(await readFile(new URL(p,import.meta.url)))])));
const loaded=await artifacts(),executionHash=digest(loaded);
const validateRecord = data => validateJointSession(data, executionHash);
const count=Object.getOwnPropertyDescriptor(OfflineFixtureProvider.prototype,'dispatchCount').get;
const ports=Object.fromEntries(['preflight','dispatch','lookup'].map(k=>[k,OfflineFixtureProvider.prototype[k]]));
function providerPort(provider){
 const controlled=controlledProviderPort(provider);if(controlled)return controlled;
 const fixed=jointFixturePort(provider);if(fixed)return fixed;
 try{count.call(provider);}catch{fail('OFFLINE_HOST_REQUIRED');}
 return Object.freeze(Object.fromEntries(Object.entries(ports).map(([k,v])=>[k,v.bind(provider)])));
}
const inputKeys=(v,k)=>{canonical(v);keys(v,k);return clone(v);};
const localFailures=new Set([...SUMMARY_LOCAL_FAILURES,...MEMORY_LOCAL_FAILURES,'HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH',
 'MODEL_CONTROL_REFRESH_REQUIRED','MODEL_CONTROL_UNAVAILABLE','MODEL_CONTROL_TIMEOUT','MODEL_CONTROL_CHANGED','MODEL_CONTROL_UNAUTHORIZED','MODEL_CONTROL_UNSUPPORTED','MODEL_PARAMETERS_REJECTED','MODEL_CONTROL_RESPONSE_INVALID']);
const controlledError=e=>typeof e?.code==='string'&&/^[A-Z0-9_]{1,100}$/.test(e.code)?e.code:'JOINT_EXECUTION_INTERRUPTED';
const latestPolicy=(s,a)=>memoryContextPolicy(s,s.summary.snapshot,s.memory.snapshot,{authorization:a,historyConfig:s.history.config});
const readRun=(record,id)=>record.data.runs.find(r=>r.operationId===id);
const summaryFor=(s,id,stage)=>s.summary.jobs.find(j=>j.runId===id&&j.stageId===(stage.startsWith('compression')?stage.replace('compression','summary'):stage));
const memoryFor=(s,run)=>s.memory.jobs.find(j=>j.operationId===run.plan.stages.find(t=>t.purpose==='memory')?.operationId);
function itemFor(s,run,stage){return stage==='story'?s.operations.find(o=>o.operationId===run.operationId):stage==='memory-after'?memoryFor(s,run):summaryFor(s,run.operationId,stage);}
function requestFor(item,stage){return stage.startsWith('compression')?item.compressionRequest:item.request;}
function pendingItem(item,stage){return stage==='story'?STORY_PENDING.includes(item.state):stage==='memory-after'?['prepared','dispatching','unknown'].includes(item.state):SUMMARY_PENDING.includes(item.state);}

// Isolated /5 host: same pure storage, registry and assembly contracts; the /1-/4
// execution artifacts and acceptance services are not rewritten or migrated.
export class JointSessionHost {
 #records;#branches;#resolve;#provider;#budget;#allowance;#model;#history;#draw;#fault;#queue=Promise.resolve();#active=new Set();#closed=false;
 constructor(records,options,allowance){
  this.#records=records;this.#branches=options.branchStore;this.#resolve=options.resolveRuntime;this.#provider=providerPort(options.provider);this.#budget=requireJointBudget(options.budget);this.#allowance=allowance;
  this.#model=requireOfflineModelControl(options.modelControl);this.#history=requireHistoryControl(options.historyControl);this.#draw=options.draw;this.#fault=options.fault;
 }
 static async open({directory,resolveRuntime,provider,budget,modelControl,historyControl,branchStore=null,recoverDeadOwner=false,draw=sample,fault=async()=>{},storageFault}){
  const execution=providerPort(provider);requireOfflineModelControl(modelControl);requireHistoryControl(historyControl);
  if(execution.mode===CONTROLLED_MODE&&modelControl.catalog().source!=='control-plane-cache')fail('CONTROL_MODEL_REQUIRED');
  if(branchStore!==null&&!(branchStore instanceof BranchCheckpoints))fail('BRANCH_STORE_REQUIRED');
  if(!(budget instanceof JointBudget)||typeof resolveRuntime!=='function'||typeof draw!=='function'||typeof fault!=='function')fail('JOINT_HOST_REQUIRED');
  const allowance=await requireJointBudget(budget).status(),records=await SessionRecords.open(directory,{recoverDeadOwner,fault:storageFault,scope:'authored-joint-sessions'});
  try{await records.reconcile();return Object.freeze(new JointSessionHost(records,{resolveRuntime,provider,budget,modelControl,historyControl,branchStore,draw,fault},allowance.allowanceId));}
  catch(e){await records.close();throw e;}
 }
 get readiness(){return {mode:this.#mode,sessionPersistence:true,offlineDispatch:this.#mode===JOINT_MODE,controlledModels:true,modelSelectorExecution:true,pluginExecution:true,historyWindow:true,summaryExecution:true,memoryExecution:true,
  jointBudget:true,branchExecution:!!this.#branches,realProvider:this.#mode===CONTROLLED_MODE,runtimeReady:false,executionHash,artifactHashes:{...loaded}};}
 get #mode(){return this.#provider.mode??JOINT_MODE;}
 async #refreshControl(){if(this.#mode===CONTROLLED_MODE)await this.#model.refresh();}
 #run(fn){if(this.#closed)fail('OFFLINE_HOST_CLOSED');const r=this.#queue.then(fn);this.#queue=r.catch(()=>{});return r;}
 async #runtime(binding){
  if(digest(await artifacts())!==executionHash)fail('OFFLINE_FILES_CHANGED_RESTART_REQUIRED');
  const runtime=requirePublishedRuntime(await this.#resolve(runtimeReference({workId:binding.workId,versionId:binding.versionId})));
  runtime.requireBinding(binding);await runtime.verify();return runtime;
 }
 async #load(id){
  checkSession(id);const r=await this.#records.get(id,validateRecord);if(!r)fail('OFFLINE_SESSION_NOT_FOUND');
  if(r.data.id!==id)fail('JOINT_SESSION_CORRUPT');
  if(isControlledSession(r.data)!==(this.#mode===CONTROLLED_MODE))fail('JOINT_EXECUTION_MODE_MISMATCH');
  if(r.data.allowanceId!==this.#allowance)fail('JOINT_ALLOWANCE_CHANGED');
  if(r.data.origin){
   if(!this.#branches)fail('BRANCH_STORE_REQUIRED');
   const checkpoint=await this.#branches.read(r.data.origin.checkpointId);
   if(!checkpoint||!same(branchOrigin(checkpoint),r.data.origin)||r.data.id!==branchChildId(checkpoint)||r.data.create.requestHash!==branchCreateHash(checkpoint))fail('BRANCH_ORIGIN_UNVERIFIED');
  }
  const runtime=await this.#runtime(r.data.binding),p=dialoguePreview(runtime.configuration().document.enhancements,r.data.presetId);
  if(p.presetId!==r.data.presetId||!same(p.messages,r.data.examples))fail('JOINT_SESSION_CORRUPT');
  return {...r,runtime};
 }
 async #save(r){r.data.revision++;r.data.updatedAt=timestamp();validateJointSession(r.data,executionHash);return {...await this.#records.put(r.data.id,r.hash,r.data),runtime:r.runtime};}
 #idle(s){if(s.operations.some(o=>STORY_PENDING.includes(o.state)))fail('OFFLINE_RESULT_PENDING');if(pendingJointSummary(s))fail('SUMMARY_RESULT_PENDING');if(pendingMemory(s))fail('MEMORY_RESULT_PENDING');if(runPending(s))fail('JOINT_RUN_PENDING');}
 #reply(r,id,replayed=false){const run=readRun(r,id);return {sessionId:r.data.id,revision:r.data.revision,run:clone(run),operation:clone(r.data.operations.find(o=>o.operationId===id)??null),mode:this.#mode,replayed};}
 create(input){
  input=inputKeys(input,['operationId','expectedRevision','reference','openingText','presetId']);
  if(!isId(input.operationId)||input.expectedRevision!==null)fail('OFFLINE_OPERATION_INVALID');
  const reference=runtimeReference(input.reference);
  return this.#run(async()=>{
   const id=jointSessionId(input.operationId),prior=await this.#records.get(id),hash=digest(input);
   if(prior){const r=await this.#load(id);if(r.data.create.requestHash!==hash)fail('OFFLINE_OPERATION_COLLISION');return {session:clone(r.data),replayed:true};}
   if(digest(await artifacts())!==executionHash)fail('OFFLINE_FILES_CHANGED_RESTART_REQUIRED');
   const runtime=requirePublishedRuntime(await this.#resolve(reference));await runtime.verify();
   const binding=runtime.bindSession({sessionId:id,openingText:input.openingText}),p=dialoguePreview(runtime.configuration().document.enhancements,input.presetId);
   if(p.presetId!==input.presetId)fail('OFFLINE_PRESET_INVALID');
   const s={format:this.#mode===CONTROLLED_MODE?CONTROLLED_SESSION_FORMAT:JOINT_SESSION_FORMAT,id,revision:0,createdAt:timestamp(),updatedAt:timestamp(),create:{operationId:input.operationId,requestHash:hash},binding,executionHash,
    allowanceId:this.#allowance,presetId:p.presetId,examples:p.messages,draft:input.openingText,requirements:emptyRequirements(),runs:[],operations:[],turns:[],models:emptyModelState(),history:emptyHistoryState()};
   s.summary=initialJointSummary(s);s.memory=initialMemoryJobs(s);s.memoryEdits=[];validateJointSession(s,executionHash);await this.#records.put(id,null,s);return {session:clone(s),replayed:false};
  });
 }
 async #branchUnused(s,id){
  if(this.#branches&&await this.#branches.read(branchCheckpointId(s.id,id)))fail('OFFLINE_OPERATION_COLLISION');
 }
 fork(input){
  input=branchIntent(input);if(!this.#branches)fail('BRANCH_STORE_REQUIRED');
  if(this.#active.has(input.sessionId))fail('OFFLINE_SESSION_BUSY');
  return this.#run(async()=>{
   let checkpoint=await this.#branches.read(branchCheckpointId(input.sessionId,input.operationId));
   const materialize=async(sealed,replayed)=>{
    const id=branchChildId(sealed),prior=await this.#records.get(id);
    if(prior){const child=await this.#load(id);return {session:clone(child.data),checkpointId:sealed.id,name:sealed.intent.name,intent:clone(sealed.intent),replayed:true};}
    const runtime=await this.#runtime(sealed.archive.binding);
    const child=createBranchSession(sealed,runtime,executionHash);
    await this.#fault('branchBeforeChildSave',clone(child));await this.#records.put(id,null,child);
    await this.#fault('branchAfterChildSave',clone(child));
    return {session:clone(child),checkpointId:sealed.id,name:sealed.intent.name,intent:clone(sealed.intent),replayed};
   };
   // A durable checkpoint is a committed, non-executable result. Explicit
   // read-back can finish its child even after revocation, never mint grants.
   if(checkpoint){if(!same(checkpoint.intent,input))fail('BRANCH_OPERATION_COLLISION');return materialize(checkpoint,true);}
   const r=await this.#load(input.sessionId);this.#idle(r.data);
   const a=await this.#history.authorization(input.sessionId);
   if(r.data.origin&&input.turn<=r.data.origin.turn){
    const sources=[];let origin=r.data.origin;
    while(origin){
     if(sources.length>=32||sources.some(c=>c.id===origin.checkpointId))fail('BRANCH_LINEAGE_INVALID');
     const source=await this.#branches.read(origin.checkpointId);
     if(!source||!same(branchOrigin(source),origin))fail('BRANCH_ORIGIN_UNVERIFIED');sources.push(source);
     origin=source.archive.origin&&input.turn<=source.archive.origin.turn?source.archive.origin:null;
    }
    checkpoint=createInheritedBranchCheckpoint(r.data,input,a,sources);
   }else checkpoint=createBranchCheckpoint(r.data,input,a);
   const service=this.#history.pluginService,proposal=await service.invoke({pluginId:'rpg.branch-save',sessionId:input.sessionId,capability:'session.branch.prepare',input:{turn:input.turn,name:input.name}});
   if(!same(proposal.proposal,{kind:'branch-request',sessionId:input.sessionId,turn:input.turn,name:input.name}))fail('BRANCH_PROPOSAL_INVALID');
   await this.#fault('branchPrepared',clone(proposal));
   return service.commitResult(proposal,async()=>{
    // Persist the authorization-checked decision before materializing. No
    // parent mutation, quota allocation or model dispatch occurs here.
    await this.#branches.save(checkpoint);await this.#fault('afterBranchCheckpoint',clone(checkpoint));
    return materialize(checkpoint,false);
   });
  });
 }
 async assertOperationAvailable(id,operationId){const {data:s}=await this.#load(id);assertJointIdFree(s,operationId);await this.#branchUnused(s,operationId);}
 reconcileBranch(value){
  const input=branchIntent(value);if(!this.#branches)fail('BRANCH_STORE_REQUIRED');
  return this.#run(async()=>{
   await this.#records.reconcile();await this.#branches.reconcileStorage();
   const checkpoint=await this.#branches.read(branchCheckpointId(input.sessionId,input.operationId));
   if(!checkpoint)return {state:'not_submitted',intent:input,checkpointId:null,session:null};
   if(!same(checkpoint.intent,input))fail('BRANCH_OPERATION_COLLISION');
   const id=branchChildId(checkpoint),child=await this.#records.get(id);
   if(!child)return {state:'checkpoint_saved',intent:input,checkpointId:checkpoint.id,session:null};
   const loaded=await this.#load(id);return {state:'completed',intent:input,checkpointId:checkpoint.id,session:clone(loaded.data)};
  });
 }
 async branchDisplaySource(id,turn){
  const {data:s}=await this.#load(id);
  if(!s.origin||!Number.isSafeInteger(turn)||turn<1||turn>s.origin.turn)fail('BRANCH_DISPLAY_ORIGIN_INVALID');
  let source=s;const seen=new Set();
  while(source.origin&&turn<=source.origin.turn){
   const key=source.origin.checkpointId;if(seen.has(key)||seen.size>=32)fail('BRANCH_LINEAGE_INVALID');seen.add(key);
   const checkpoint=await this.#branches.read(key);
   if(!checkpoint||!same(branchOrigin(checkpoint),source.origin))fail('BRANCH_ORIGIN_UNVERIFIED');source=checkpoint.archive;
  }
  return {checkpointId:s.origin.checkpointId,session:clone(source)};
 }
 async lookupSession(id){const wasActive=this.#active.has(id),{data:s}=await this.#load(id);return {id:s.id,revision:s.revision,binding:s.binding,completedTurns:s.turns.length,busy:wasActive||this.#active.has(id),pending:!!runPending(s)};}
 async read(id){
  // Reading verifies artifacts asynchronously. A task may finish while that
  // older persisted snapshot is being verified; keep polling rather than call
  // this transition an unknown outcome. The next read observes its settlement.
  const wasActive=this.#active.has(id),{data:s}=await this.#load(id),a=await this.#history.authorization(id),busy=wasActive||this.#active.has(id);
  return {session:clone(s),busy,pending:!!runPending(s),status:runPending(s)&&!busy?'unknown':s.runs.at(-1)?.state??'idle',
   effectivePolicy:latestPolicy(s,a),pauseReason:pauseReason(s,a),historyConfigurationPolicyRevision:contextPolicyRevision(s.history,a),budget:await this.#budget.status()};
 }
 async waitForIdle(id){await this.#queue;return this.read(id);}
 async modelCatalog(id){await this.#refreshControl();const r=await this.#load(id);if(!this.#active.has(id)&&!runPending(r.data)&&(await this.#history.pluginService.sessionStatus(id,MODEL_PLUGIN)).enabled)await checkPluginCatalog(this.#history.pluginService,id);return {catalog:this.#model.list(),authoredSuggestion:clone(r.runtime.configuration().document.model),sessionRevision:r.data.revision,models:clone(r.data.models),realProvider:this.#mode===CONTROLLED_MODE};}
 configureModel(input){
  input=validateModelIntent(input);
  if(this.#active.has(input.sessionId))fail('OFFLINE_SESSION_BUSY');
  return this.#run(async()=>{
   let r=await this.#load(input.sessionId);const s=r.data,prior=s.models.changes.find(c=>c.intent.operationId===input.operationId);
   if(prior){if(prior.intentHash!==digest(input))fail('OFFLINE_OPERATION_COLLISION');return {revision:s.revision,change:clone(prior),replayed:true};}
   this.#idle(s);assertJointIdFree(s,input.operationId);await this.#branchUnused(s,input.operationId);
   if(s.revision!==input.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');if(s.models.revision!==input.expectedModelRevision)fail('MODEL_REVISION_CONFLICT');
   if(s.models.changes.length>=MODEL_CHANGE_LIMIT)fail('MODEL_SETTING_CAPACITY');
   const choice=input.choice===null?null:this.#model.select(input.choice),change={revision:s.models.revision+1,intent:input,intentHash:digest(input),choice};
   if(choice&&this.#mode===CONTROLLED_MODE)await this.#model.validate(choice);
   const commit=async saved=>{if(choice)this.#model.assertCurrent(choice);s.models.revision=saved.revision;s.models.current[input.purpose]=choice;s.models.changes.push(saved);r=await this.#save(r);return {revision:r.data.revision,change:clone(saved),replayed:false};};
   if(input.purpose==='story'&&choice&&(await this.#history.pluginService.sessionStatus(s.id,MODEL_PLUGIN)).enabled)
    return commitPluginModel({service:this.#history.pluginService,change,resourceHash:s.binding.resourceHash,commit,beforeCommit:receipt=>this.#fault('modelPluginPrepared',receipt)});
   return commit(change);
  });
 }
 configureRequirements(value){
  const input=requirementsIntent(value);if(this.#active.has(input.sessionId))fail('OFFLINE_SESSION_BUSY');
  return this.#run(async()=>{
   let r=await this.#load(input.sessionId);const s=r.data,state=s.requirements??emptyRequirements(),prior=state.changes.find(c=>c.intent.operationId===input.operationId);
   if(prior){if(prior.intentHash!==digest(input))fail('OFFLINE_OPERATION_COLLISION');return {sessionId:s.id,revision:s.revision,change:clone(prior),replayed:true};}
   this.#idle(s);assertJointIdFree(s,input.operationId);await this.#branchUnused(s,input.operationId);
   if(s.revision!==input.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');
   if(state.revision!==input.expectedRequirementsRevision)fail('REQUIREMENTS_REVISION_CONFLICT');
   const a=await this.#history.authorization(s.id);if(latestPolicy(s,a).revision!==input.expectedContextPolicyRevision)fail('REQUIREMENTS_POLICY_CONFLICT');
   if(state.changes.length>=REQUIREMENTS_CHANGES)fail('REQUIREMENTS_CAPACITY');
   const change={revision:state.revision+1,intent:input,intentHash:digest(input)};
   s.requirements={...state,revision:change.revision,text:input.text,changes:[...state.changes,change]};
   r=await this.#save(r);return {sessionId:s.id,revision:r.data.revision,change:clone(change),replayed:false};
  });
 }
 configureHistory(input){
  input=validateHistoryIntent(input);if(this.#active.has(input.sessionId))fail('OFFLINE_SESSION_BUSY');
  return this.#run(async()=>{
   let r=await this.#load(input.sessionId);const s=r.data,prior=s.history.changes.find(c=>c.intent.operationId===input.operationId);
   if(prior){if(prior.intentHash!==digest(input))fail('OFFLINE_OPERATION_COLLISION');return {revision:s.revision,change:clone(prior),replayed:true};}
   this.#idle(s);assertJointIdFree(s,input.operationId);await this.#branchUnused(s,input.operationId);
   if(s.revision!==input.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');if(s.history.revision!==input.expectedHistoryRevision)fail('HISTORY_REVISION_CONFLICT');
   if(s.history.changes.length>=HISTORY_CHANGE_LIMIT)fail('HISTORY_SETTING_CAPACITY');
   return this.#history.commitConfiguration(input,s.history,async change=>{s.history.revision=change.revision;s.history.config=clone(input.config);s.history.changes.push(change);r=await this.#save(r);return {revision:r.data.revision,change:clone(change),replayed:false};});
  });
 }
 editMemory(input){
  input=inputKeys(input,['sessionId','operationId','expectedRevision','expectedMemoryRevision','expectedContextPolicyRevision','change']);checkSession(input.sessionId);
  return this.#run(async()=>{
   let r=await this.#load(input.sessionId);const s=r.data,a=await this.#history.authorization(s.id);
   const prior=s.memory.snapshot.versions.find(v=>v.operationId===input.operationId);
   const edit={operationId:input.operationId,expectedRevision:input.expectedRevision,expectedMemoryRevision:input.expectedMemoryRevision,change:input.change};
   if(prior){if(!same(s.memoryEdits.find(i=>i.operationId===input.operationId),input))fail('MEMORY_OPERATION_COLLISION');const result=reviseMemory(s,s.memory.snapshot,edit);return {revision:s.revision,...result};}
   this.#idle(s);assertJointIdFree(s,input.operationId);await this.#branchUnused(s,input.operationId);
   if(latestPolicy(s,a).revision!==input.expectedContextPolicyRevision)fail('MEMORY_POLICY_CONFLICT');
   return this.#history.pluginService.guardMemoryTask(s.id,a,async()=>{const result=reviseMemory(s,s.memory.snapshot,edit);s.memory.snapshot=result.state;s.memoryEdits.push(clone(input));r=await this.#save(r);return {revision:r.data.revision,...result};});
  });
 }
 estimate(input){input=jointIntent(input);return this.#run(async()=>{await this.#refreshControl();const r=await this.#load(input.sessionId),a=await this.#history.authorization(input.sessionId);await this.#branchUnused(r.data,input.operationId);this.#checkIntent(r.data,input,a);return {plan:jointPlan(r.data,input.operationId,input.kind,a),budget:await this.#budget.status()};});}
 #checkIntent(s,i,a){
  this.#idle(s);assertJointIdFree(s,i.operationId);
  if(s.revision!==i.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');if(s.models.revision!==i.expectedModelRevision)fail('MODEL_REVISION_CONFLICT');
  if(s.summary.snapshot.revision!==i.expectedSummaryRevision)fail('SUMMARY_REVISION_CONFLICT');if(s.memory.snapshot.revision!==i.expectedMemoryRevision)fail('MEMORY_REVISION_CONFLICT');
  const p=latestPolicy(s,a);if(p.revision!==i.expectedContextPolicyRevision)fail('MEMORY_POLICY_CONFLICT');
  if(p.reason&&p.reason!=='SUMMARY_UPDATE_REQUIRED')fail(p.reason);
  if(i.kind==='send'&&pauseReason(s,a))fail(pauseReason(s,a));
  if(s.runs.length>=JOINT_LIMITS.runs)fail('OFFLINE_OPERATION_CAPACITY');
  if(i.kind==='send'){
   if(!s.models.current.story)fail('MODEL_SELECTION_REQUIRED');this.#model.assertCurrent(s.models.current.story);
   if(!s.turns.length&&i.input!==s.binding.openingText)fail('OFFLINE_OPENING_MISMATCH');
   if(s.turns.reduce((n,t)=>n+[...t.user].length+[...t.assistant].length,0)+[...i.input].length+JOINT_LIMITS.replyCharacters>2000000)fail('SUMMARY_SOURCE_CAPACITY');
   this.#provider.preflight(i.operationId);
  }
 }
 send(input){return this.#execute(jointIntent({...input,kind:'send'}));}
 update(input){return this.#execute(jointIntent({...input,kind:'update',input:null}));}
 #execute(input){
  let deliver,deny;const delivered=new Promise((a,b)=>{deliver=a;deny=b;});
  const work=this.#run(async()=>{
   this.#active.add(input.sessionId);
   try{
    let r=await this.#load(input.sessionId),s=r.data;const prior=s.runs.find(x=>x.operationId===input.operationId);
    if(prior){if(prior.intentHash!==digest(input))fail('OFFLINE_OPERATION_COLLISION');return this.#reply(r,prior.operationId,true);}
    await this.#refreshControl();const a=await this.#history.authorization(s.id);await this.#branchUnused(s,input.operationId);this.#checkIntent(s,input,a);
    if(input.kind==='send'){const c=checkPlayerInput(input.input,r.runtime.configuration().document.enhancements.blockedWords);if(!c.allowed)fail(c.issue?'OFFLINE_INPUT_CAPACITY':'OFFLINE_INPUT_BLOCKED');}
    const plan=jointPlan(s,input.operationId,input.kind,a);if(!plan.stages.length)fail('JOINT_NO_TASKS');
    const run={operationId:input.operationId,intent:input,intentHash:digest(input),plan,groupId:jointGroupId(this.#allowance,plan),historyRevision:s.history.revision,
     summaryRevision:s.summary.snapshot.revision,memoryRevision:s.memory.snapshot.revision,state:'prepared',diagnostic:null,settledRevision:null};
    s.runs.push(run);if(input.kind==='send')s.draft=input.input;r=await this.#save(r);
    await this.#fault('afterRunPrepared',clone(run));
    try{await this.#budget.reserve(plan);}catch(e){
     if(e.code!=='JOINT_BUDGET_EXHAUSTED')throw e;
     const item=readRun(r,input.operationId);item.state='not_dispatched';item.diagnostic=e.code;item.settledRevision=r.data.revision+1;r=await this.#save(r);return this.#reply(r,input.operationId);
    }
    await this.#fault('afterReserved',clone(run));readRun(r,input.operationId).state='running';r=await this.#save(r);
    for(const stage of plan.stages){
     if(stage.purpose==='compression')continue;
     if(stage.purpose==='story'){
      r=await this.#prepareStory(r,input.operationId);r=await this.#invoke(r,input.operationId,stage.stageId);
      const op=r.data.operations.at(-1);
      if(op.state!=='completed')return this.#finish(r,input.operationId,op.diagnostic??'STORY_'+op.state.toUpperCase());
      deliver(this.#reply(r,input.operationId));
      await this.#fault('afterStorySaved',clone(r.data));
     }else if(stage.purpose==='summary'){
      if(!summaryFor(r.data,input.operationId,stage.stageId)){const j=freezeJointSummary(r.data,readRun(r,input.operationId),stage.stageId);if(j)r.data.summary.jobs.push(j);r=await this.#save(r);}
      const j=summaryFor(r.data,input.operationId,stage.stageId);
      if(!j){await this.#budget.release(run.groupId,stage.stageId);await this.#budget.release(run.groupId,stage.stageId.replace('summary','compression'));continue;}
      if(j.state==='prepared')r=await this.#invoke(r,input.operationId,stage.stageId);
      let settled=summaryFor(r.data,input.operationId,stage.stageId);
      if(settled.state==='awaiting_compression')r=await this.#invoke(r,input.operationId,stage.stageId.replace('summary','compression'));
      settled=summaryFor(r.data,input.operationId,stage.stageId);
      if(settled.state!=='applied')return this.#finish(r,input.operationId,settled.diagnostic??'SUMMARY_INTERRUPTED');
      if(!settled.compressionRequest)await this.#budget.release(run.groupId,stage.stageId.replace('summary','compression'));
     }else{
      let j=memoryFor(r.data,readRun(r,input.operationId));
      if(!j){j=this.#freezeMemory(r.data,readRun(r,input.operationId));if(j)r.data.memory.jobs.push(j);r=await this.#save(r);}
      j=memoryFor(r.data,readRun(r,input.operationId));
      if(!j){await this.#budget.release(run.groupId,stage.stageId);continue;}
      if(j.state==='prepared')r=await this.#invoke(r,input.operationId,stage.stageId);
      j=memoryFor(r.data,readRun(r,input.operationId));
      if(j.state!=='applied')return this.#finish(r,input.operationId,j.diagnostic??'MEMORY_INTERRUPTED');
     }
    }
    return this.#finish(r,input.operationId,null);
   }finally{this.#active.delete(input.sessionId);}
  });
  work.then(deliver,deny);return delivered;
 }
 async #prepareStory(r,id){
  const s=r.data,run=readRun(r,id),a=run.plan.input.authorization,doc=r.runtime.configuration().document,choice=s.models.current.story;
  const input={sessionId:s.id,operationId:id,expectedRevision:s.revision,expectedModelRevision:s.models.revision,expectedContextPolicyRevision:latestPolicy(s,a).revision,input:run.intent.input};
  const draws=doc.worldbook.entries.filter(e=>e.enabled).map(e=>({id:e.id,trigger:e.chance>0&&e.chance<100?this.#draw():null,content:e.mode==='weighted'?this.#draw():null}));
  const worldbook=await buildWorldbookPlan({operationId:id,revision:s.revision,document:doc,currentInput:input.input,turns:s.turns.map(t=>({id:t.id,complete:true,user:t.user,assistant:t.assistant})),draws,capacity:{...JOINT_LIMITS.worldbook}});
  const assembled=assembleMemoryContext({session:s,summary:s.summary.snapshot,memory:s.memory.snapshot,authorization:a,historyConfig:s.history.config,base:{system:worldbook.assembly.system,user:worldbook.assembly.user},input:input.input,expectedPolicyRevision:input.expectedContextPolicyRevision});
  const request={id:jointRequestId(s.id,id),operationId:id,sessionId:s.id,bindingHash:digest(s.binding),executionHash,mode:this.#mode,model:choice.entry.modelId,policy:assembled.evidence.policy,createdAt:timestamp(),
   rawInput:input.input,worldbook,draws,messages:assembled.messages,messagesHash:digest(assembled.messages),memoryContext:assembled.evidence,modelConfiguration:{revision:s.models.revision,choice:clone(choice)},parameters:clone(choice.wireParameters),capacity:modelCapacity(choice,assembled.messages)};
  s.operations.push({operationId:id,intent:input,intentHash:digest(input),request,requestHash:digest(request),state:'prepared',receipt:null,diagnostic:null});
  if(Buffer.byteLength(canonical(s))+JOINT_LIMITS.replyCharacters*18+65536>SESSION_RECORD_LIMIT)fail('OFFLINE_RECORD_CAPACITY');
  r=await this.#save(r);await this.#fault('afterStoryPrepared',clone(request));return r;
 }
 #freezeMemory(s,run){
  const a=run.plan.input.authorization,stage=run.plan.stages.find(t=>t.purpose==='memory');if(!stage)return null;
  const story=s.operations.find(o=>o.operationId===run.operationId);
  const intent=story?autoMemoryIntent(s,story,a):{sessionId:s.id,operationId:stage.operationId,expectedRevision:s.revision,expectedMemoryRevision:s.memory.snapshot.revision,
   expectedModelRevision:s.models.revision,expectedContextPolicyRevision:latestPolicy(s,a).revision};
  return freezeMemoryJob(s,intent,a,{kind:story?'after-story':'manual',operationId:story?story.operationId:stage.operationId});
 }
 async #guard(s,run,stage,action){
  if(stage.startsWith('summary')||stage.startsWith('compression'))return this.#history.pluginService.guardSummaryTask(s.id,run.plan.input.authorization.summary.revision,a=>{if(!a.enabled)fail('SUMMARY_AUTHORIZATION_CHANGED');return action(a);});
  return this.#history.pluginService.guardMemoryTask(s.id,run.plan.input.authorization,()=>action(run.plan.input.authorization.memory));
 }
 #notDispatched(s,item,stage,code){
  if(stage==='story'){item.state='not_dispatched';item.diagnostic=code;}
  else if(stage==='memory-after')settleMemoryJob(s,item,code==='MEMORY_AUTHORIZATION_CHANGED'?'revoked':'not_dispatched',code);
  else settleSummaryJob(s,item,code==='SUMMARY_AUTHORIZATION_CHANGED'?'revoked':'not_dispatched',code);
 }
 async #invoke(r,id,stage){
  let run=readRun(r,id),item=itemFor(r.data,run,stage);const request=requestFor(item,stage),compressed=stage.startsWith('compression');
  item.state=compressed?'compression_dispatching':'dispatching';item.diagnostic=null;r=await this.#save(r);
  await this.#fault('beforeClaim',clone(request));
  let response;
  try{
   response=(await this.#guard(r.data,run,stage,async()=>{
    const purpose=stage==='story'?'story':stage==='memory-after'?'memory':'summary',choice=stage==='story'?request.modelConfiguration.choice:request.modelChoice;
    if(!same(r.data.models.current[purpose],choice))fail(purpose==='memory'?'MEMORY_MODEL_CHANGED':purpose==='summary'?'SUMMARY_MODEL_CHANGED':'MODEL_SELECTION_CHANGED');
    await this.#refreshControl();if(this.#mode===CONTROLLED_MODE)await this.#model.validate(choice);
    this.#model.assertCurrent(choice);this.#provider.preflight(request.operationId);
    const claim=await this.#budget.claim({groupId:run.groupId,stageId:stage,requestId:request.id,requestHash:digest(request)});
    if(!claim.dispatch)fail('JOINT_REQUEST_ALREADY_CLAIMED');
    await this.#fault('afterClaim',clone(request));
    return {response:this.#provider.dispatch(request)};
   })).response;
  }catch(e){
   if(!localFailures.has(e.code))throw e;
   // No callback has started transport on these local guard failures.
   item=itemFor(r.data,readRun(r,id),stage);this.#notDispatched(r.data,item,stage,stage==='story'&&e.code==='MEMORY_AUTHORIZATION_CHANGED'?'HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH':e.code);
   return this.#save(r);
  }
  let receipt;try{receipt=await response;}catch{
   item=itemFor(r.data,readRun(r,id),stage);item.state=compressed?'compression_unknown':'unknown';item.diagnostic=stage==='story'?'OFFLINE_RESULT_UNKNOWN':stage==='memory-after'?'MEMORY_RESULT_UNKNOWN':'SUMMARY_RESULT_UNKNOWN';
   await this.#budget.uncertain(run.groupId,stage);return this.#save(r);
  }
  await this.#fault('afterProvider',clone(request));
  r=await this.#accept(r,id,stage,receipt);await this.#fault('afterStageSaved',{stage,session:clone(r.data)});return r;
 }
 async #accept(r,id,stage,receipt){
  const s=r.data,run=readRun(r,id),item=itemFor(s,run,stage),request=requestFor(item,stage),compressed=stage.startsWith('compression');
  if(receipt?.kind!==(this.#mode===CONTROLLED_MODE?'control-plane':'offline-fixture'))fail('JOINT_RECEIPT_ORIGIN_MISMATCH');
  storyReceipt(receipt,request);
  await this.#budget.settle({groupId:run.groupId,stageId:stage,requestId:request.id,requestHash:digest(request),status:receipt.result.status==='failed'?'failed':'completed',resultHash:receipt.sha256});
  item[compressed?'compressionReceipt':'receipt']=clone(receipt);
  if(stage==='story'){
   item.state=outcomeState(receipt.result);item.diagnostic=null;
   if(item.state==='completed'){
    s.turns.push(turnValue(item,s.turns.length+1));s.draft='';
    if(run.plan.stages.some(t=>t.stageId==='summary-after')){const j=freezeJointSummary(s,run,'summary-after');if(j)s.summary.jobs.push(j);}
    const memory=this.#freezeMemory(s,run);if(memory)s.memory.jobs.push(memory);
   }
   return this.#save(r);
  }
  try{
   return await this.#guard(s,run,stage,async authorization=>{
    if(stage==='memory-after'){
     const candidate=inspectMemoryResult(request,receipt);
     try{const result=commitMemoryUpdate(s,s.memory.snapshot,{candidate,authorization});s.memory.snapshot=result.state;item.appliedVersionId=result.version.id;settleMemoryJob(s,item,'applied');}
     catch(e){if(MEMORY_LOCAL_FAILURES.includes(e.code))throw e;settleMemoryJob(s,item,'rejected',controlledError(e));}
    }else{
     const {candidate,compression}=summaryJobCandidates(item),effective=compression??candidate;
     if(effective.status==='rejected')settleSummaryJob(s,item,'rejected',effective.reason);
     else if(candidate.status==='requires-compression'&&!compression){
      try{
       item.compressionRequest=createSummaryCompression(candidate,jointCompressionId(item));
       if(Buffer.byteLength(canonical(s))+Buffer.byteLength(canonical(item.compressionRequest))+SUMMARY_RECEIPT_RESERVE>SESSION_RECORD_LIMIT)fail('SUMMARY_RECORD_CAPACITY');
       item.state='awaiting_compression';item.diagnostic=null;
      }catch(e){if(!['MODEL_CONTEXT_CAPACITY','SUMMARY_RECORD_CAPACITY'].includes(e.code))throw e;item.compressionRequest=null;settleSummaryJob(s,item,'blocked',e.code);}
     }else{
      const result=commitSummaryUpdate(s,s.summary.snapshot,{candidate,compression,authorization});s.summary.snapshot=result.state;item.appliedVersionId=result.version.id;settleSummaryJob(s,item,'applied');
     }
    }
    return this.#save(r);
   });
  }catch(e){
   const allowed=stage==='memory-after'?MEMORY_LOCAL_FAILURES:SUMMARY_LOCAL_FAILURES;
   if(!allowed.includes(e.code))throw e;
   if(stage==='memory-after')settleMemoryJob(s,item,e.code==='MEMORY_AUTHORIZATION_CHANGED'?'revoked':'stale',e.code);
   else settleSummaryJob(s,item,e.code==='SUMMARY_AUTHORIZATION_CHANGED'?'revoked':'stale',e.code);
   return this.#save(r);
  }
 }
 async #finish(r,id,diagnostic){
  const run=readRun(r,id);
  // Only unstarted stages are released. Pending claimed requests stay counted.
  for(const stage of run.plan.stages){
   const item=itemFor(r.data,run,stage.stageId);
   if(!item)continue;
   if(stage.purpose==='compression'&&item.state!=='awaiting_compression')continue;
   if(item.state==='prepared'||item.state==='awaiting_compression')this.#notDispatched(r.data,item,stage.stageId,stage.purpose==='memory'?'MEMORY_NOT_DISPATCHED':stage.purpose==='story'?'OFFLINE_NOT_DISPATCHED':'SUMMARY_NOT_DISPATCHED');
  }
  await this.#budget.releaseUnused(run.groupId);
  const unresolved=run.plan.stages.some(t=>{const item=itemFor(r.data,run,t.stageId);return item&&pendingItem(item,t.stageId);});
  run.state=unresolved?'unknown':diagnostic?'paused':'completed';run.diagnostic=diagnostic;run.settledRevision=unresolved?null:r.data.revision+1;
  r=await this.#save(r);return this.#reply(r,id);
 }
 recover(input){
  input=inputKeys(input,['sessionId','operationId','expectedRevision','expectedSummaryRevision','expectedMemoryRevision']);checkSession(input.sessionId);
  if(!isId(input.operationId)||![input.expectedRevision,input.expectedSummaryRevision,input.expectedMemoryRevision].every(integer))fail('JOINT_RECOVERY_INVALID');
  return this.#run(async()=>{
   await this.#records.reconcile();await this.#budget.reconcile();
   let r=await this.#load(input.sessionId),run=readRun(r,input.operationId);if(!run)fail('OFFLINE_OPERATION_NOT_FOUND');
   if(!runPending(r.data)||!['prepared','running','unknown'].includes(run.state))return this.#reply(r,run.operationId,true);
   if(r.data.revision!==input.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');
   if(r.data.summary.snapshot.revision!==input.expectedSummaryRevision)fail('SUMMARY_REVISION_CONFLICT');
   if(r.data.memory.snapshot.revision!==input.expectedMemoryRevision)fail('MEMORY_REVISION_CONFLICT');
   const group=await this.#budget.read(run.groupId);
   if(!group){run.state='not_dispatched';run.diagnostic='JOINT_NOT_RESERVED';run.settledRevision=r.data.revision+1;r=await this.#save(r);return this.#reply(r,run.operationId);}
   for(const stage of run.plan.stages){
    const item=itemFor(r.data,run,stage.stageId);if(!item||!pendingItem(item,stage.stageId))continue;
    if(stage.purpose==='summary'&&item.state.startsWith('compression'))continue;
    if(stage.purpose==='compression'&&!item.state.startsWith('compression')&&item.state!=='awaiting_compression')continue;
    const slot=group.stages.find(t=>t.stageId===stage.stageId),request=requestFor(item,stage.stageId);
    if(['reserved','released'].includes(slot.state)||!request||item.state==='prepared'||item.state==='awaiting_compression'){
     this.#notDispatched(r.data,item,stage.stageId,stage.purpose==='story'?'OFFLINE_NOT_DISPATCHED':stage.purpose==='memory'?'MEMORY_NOT_DISPATCHED':'SUMMARY_NOT_DISPATCHED');r=await this.#save(r);continue;
    }
    const receipt=await this.#provider.lookup(request);
    if(receipt)r=await this.#accept(r,run.operationId,stage.stageId,receipt);
    else {item.state=stage.purpose==='compression'?'compression_unknown':'unknown';item.diagnostic=stage.purpose==='story'?'OFFLINE_RESULT_UNKNOWN':stage.purpose==='memory'?'MEMORY_RESULT_UNKNOWN':'SUMMARY_RESULT_UNKNOWN';await this.#budget.uncertain(run.groupId,stage.stageId);r=await this.#save(r);}
    run=readRun(r,input.operationId);
   }
   return this.#finish(r,run.operationId,'JOINT_RECOVERY_STOPPED');
  });
 }
 async reconcileStorage(){return this.#run(async()=>({session:await this.#records.reconcile(),budget:await this.#budget.reconcile()}));}
 async close(){if(this.#closed)return;this.#closed=true;await this.#queue;await this.#records.close();}
}
