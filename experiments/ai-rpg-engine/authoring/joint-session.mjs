import {legacyArtifacts} from '../character/legacy-compat.mjs';
import {futureIntent,futureIds,futureReceipt,priorFuture} from './comparison-future.mjs';
import {modelChoiceInput} from './model-control.mjs';
import {comparisonReceipt} from './controlled-receipt.mjs';
import {validatePlan as validateComparisonPlan,freezeContext,validateResult as validateComparisonResult} from './comparison-contract.mjs';
import {PARAMETER_PLUGIN,CHAT_PARAMETER_LIMIT,emptyChatParameters,chatParameterIntent,purposeParameterRevision,parameterConfigurationHash,requireParameterModel,requireChatDefault,freezeChatParameters,parameterChoices,taskModelChoice} from './chat-parameters.mjs';
import {emptyRequirements,requirementsIntent,REQUIREMENTS_CHANGES} from './requirements.mjs';
import {panelDefinition} from './variable-panel.mjs';
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
import {requireOfflineModelControl,emptyModelState,validateModelIntent,modelCapacity,MODEL_CHANGE_LIMIT,PARAMETER_CHOICE_FORMAT,PARAMETER_SELECTION_FORMAT} from './model-control.mjs';
import {requireHistoryControl,emptyHistoryState,validateHistoryIntent,contextPolicyRevision,HISTORY_CHANGE_LIMIT} from './history-control.mjs';
import {memoryContextPolicy,assembleMemoryContext} from './memory-context.mjs';
import {createSummaryCompression,commitSummaryUpdate,inspectSummaryResult} from './summary-state.mjs';
import {settleSummaryJob} from './summary-jobs.mjs';
import {initialJointSummary,freezeJointSummary,pendingJointSummary,jointCompressionId,summaryJobCandidates,SUMMARY_PENDING,SUMMARY_LOCAL_FAILURES,SUMMARY_RECEIPT_RESERVE} from './joint-summary.mjs';
import {initialMemoryJobs,freezeMemoryJob,autoMemoryIntent,pendingMemory,settleMemoryJob,MEMORY_LOCAL_FAILURES} from './memory-jobs.mjs';
import {commitMemoryUpdate,inspectMemoryResult,reviseMemory} from './memory-state.mjs';
import {JOINT_MODE,JOINT_SESSION_FORMAT,JOINT_LIMITS,digest,same,clone,integer,raw,jointSessionId,jointRequestId,jointGroupId,jointIntent,checkSession,
 validateJointSession,jointPlan,assertJointIdFree,pauseReason,runPending,RUN_PENDING,STORY_PENDING,outcomeState,storyReceipt,turnValue} from './joint-session-state.mjs';
export {JOINT_MODE,JOINT_SESSION_FORMAT};
const timestamp=()=>new Date().toISOString(),sample=()=>randomBytes(6).readUIntBE(0,6)/281474976710656;
const paths=['comparison-future.mjs','../plugins/owner-lock.mjs','comparison-contract.mjs','comparison-control.mjs','comparison-host.mjs','comparison-ledger.mjs','comparison-coordinator.mjs','../plugins/model-comparison.mjs','../plugins/model-comparison.manifest.json','chat-parameters.mjs','generation-settings.mjs','../plugins/catalog.mjs','../plugins/generation-parameters.mjs','../plugins/generation-parameters.manifest.json','../package-lock.json','variable-panel.mjs','variable-template-syntax.mjs','requirements.mjs','controlled-provider.mjs','controlled-receipt.mjs','model-plugin.mjs','branch-origin.mjs','branch-checkpoint.mjs','branch-storage.mjs','branch-session.mjs','joint-fixture.mjs','joint-session.mjs','joint-session-state.mjs','joint-summary.mjs','joint-plan.mjs','joint-budget.mjs','memory-state.mjs','memory-jobs.mjs','memory-context.mjs',
 'summary-state.mjs','summary-context.mjs','summary-jobs.mjs','history-control.mjs','model-control.mjs','session-storage.mjs','runtime-binding.mjs','runtime-plugins.mjs',
 'contracts.mjs','offline-session.mjs','worldbook.mjs','worldbook-regex.mjs','input-assistance.mjs','dialogue.mjs','../plugins/host.mjs','../plugins/memory-palace.mjs','../plugins/history-window.mjs','../plugins/rolling-summary.mjs'];
const artifacts=async()=>legacyArtifacts(Object.fromEntries(await Promise.all(paths.map(async p=>[p,sha256(await readFile(new URL(p,import.meta.url)))]))),import.meta.url);
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
 'MODEL_CONTROL_REFRESH_REQUIRED','MODEL_CONTROL_UNAVAILABLE','MODEL_CONTROL_TIMEOUT','MODEL_CONTROL_CHANGED','MODEL_CONTROL_UNAUTHORIZED','MODEL_CONTROL_UNSUPPORTED','MODEL_PARAMETERS_REJECTED','MODEL_CONTROL_RESPONSE_INVALID','MODEL_CATALOG_CHANGED','MODEL_CAPABILITY_CHANGED','MODEL_PARAMETER_RESOLUTION_CHANGED','PARAMETER_PREFLIGHT_REJECTED','PARAMETER_CONFIG_CAPACITY','MODEL_CONTROL_RESPONSE_LIMIT','MODEL_CONTROL_CREDENTIAL_ECHO']);
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
 #chat;#records;#branches;#resolve;#provider;#budget;#allowance;#model;#history;#draw;#fault;#queue=Promise.resolve();#active=new Set();#closed=false;
 constructor(records,options,allowance){
  this.#chat=options.chatParameters;this.#records=records;this.#branches=options.branchStore;this.#resolve=options.resolveRuntime;this.#provider=providerPort(options.provider);this.#budget=requireJointBudget(options.budget);this.#allowance=allowance;
  this.#model=requireOfflineModelControl(options.modelControl);this.#history=requireHistoryControl(options.historyControl);this.#draw=options.draw;this.#fault=options.fault;
 }
 static async open({directory,resolveRuntime,provider,budget,modelControl,historyControl,branchStore=null,chatParameters=false,recoverDeadOwner=false,draw=sample,fault=async()=>{},storageFault}){
  if(typeof chatParameters!=='boolean')fail('CHAT_PARAMETER_HOST_INVALID');
  const execution=providerPort(provider);if(chatParameters&&execution.mode!==CONTROLLED_MODE)fail('PARAMETER_CONTROL_V2_REQUIRED');requireOfflineModelControl(modelControl);requireHistoryControl(historyControl);
  if(execution.mode===CONTROLLED_MODE&&modelControl.catalog().source!=='control-plane-cache')fail('CONTROL_MODEL_REQUIRED');
  if(branchStore!==null&&!(branchStore instanceof BranchCheckpoints))fail('BRANCH_STORE_REQUIRED');
  if(!(budget instanceof JointBudget)||typeof resolveRuntime!=='function'||typeof draw!=='function'||typeof fault!=='function')fail('JOINT_HOST_REQUIRED');
  const allowance=await requireJointBudget(budget).status(),records=await SessionRecords.open(directory,{recoverDeadOwner,fault:storageFault,scope:'authored-joint-sessions'});
  try{await records.reconcile();return Object.freeze(new JointSessionHost(records,{resolveRuntime,provider,budget,modelControl,historyControl,branchStore,chatParameters,draw,fault},allowance.allowanceId));}
  catch(e){await records.close();throw e;}
 }
 get readiness(){return {mode:this.#mode,...(this.#chat?{chatParameters:true}:{}),sessionPersistence:true,offlineDispatch:this.#mode===JOINT_MODE,controlledModels:true,modelSelectorExecution:true,pluginExecution:true,historyWindow:true,summaryExecution:true,memoryExecution:true,
  jointBudget:true,branchExecution:!!this.#branches&&!this.#chat,realProvider:this.#mode===CONTROLLED_MODE,runtimeReady:false,executionHash,artifactHashes:{...loaded}};}
 get #mode(){return this.#provider.mode??JOINT_MODE;}
 async #refreshControl(choices=[]){if(this.#mode===CONTROLLED_MODE){await this.#model.refresh();if(choices.some(c=>c?.format===PARAMETER_CHOICE_FORMAT))await this.#model.parameterCatalog();}}
 #run(fn){if(this.#closed)fail('OFFLINE_HOST_CLOSED');const r=this.#queue.then(fn);this.#queue=r.catch(()=>{});return r;}
 async #runtime(binding){
  if(digest(await artifacts())!==executionHash)fail('OFFLINE_FILES_CHANGED_RESTART_REQUIRED');
  const runtime=requirePublishedRuntime(await this.#resolve(runtimeReference({workId:binding.workId,versionId:binding.versionId})));
  runtime.requireBinding(binding);await runtime.verify();return runtime;
 }
 async #load(id){
  checkSession(id);const r=await this.#records.get(id,validateRecord);if(!r)fail('OFFLINE_SESSION_NOT_FOUND');
  if(r.data.id!==id)fail('JOINT_SESSION_CORRUPT');
  if(!!r.data.generationParameters!==this.#chat)fail('CHAT_PARAMETER_SESSION_MODE_MISMATCH');
  if(isControlledSession(r.data)!==(this.#mode===CONTROLLED_MODE))fail('JOINT_EXECUTION_MODE_MISMATCH');
  if(r.data.allowanceId!==this.#allowance)fail('JOINT_ALLOWANCE_CHANGED');
  if(r.data.origin){
   if(!this.#branches)fail('BRANCH_STORE_REQUIRED');
   const checkpoint=await this.#branches.read(r.data.origin.checkpointId);
   if(!checkpoint||!same(branchOrigin(checkpoint),r.data.origin)||r.data.id!==branchChildId(checkpoint)||r.data.create.requestHash!==branchCreateHash(checkpoint))fail('BRANCH_ORIGIN_UNVERIFIED');
  }
  const runtime=await this.#runtime(r.data.binding);
  if(!same(r.data.variablePanel??null,panelDefinition(runtime.configuration().document.settings.variablePanel)))fail('PANEL_STATE_MISMATCH');
  const p=dialoguePreview(runtime.configuration().document.enhancements,r.data.presetId);
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
   if(this.#chat)s.generationParameters=emptyChatParameters();
   const panel=panelDefinition(runtime.configuration().document.settings.variablePanel);if(panel)s.variablePanel=panel;
   s.summary=initialJointSummary(s);s.memory=initialMemoryJobs(s);s.memoryEdits=[];validateJointSession(s,executionHash);await this.#records.put(id,null,s);return {session:clone(s),replayed:false};
  });
 }
 async #branchUnused(s,id){
  if(this.#branches&&await this.#branches.read(branchCheckpointId(s.id,id)))fail('OFFLINE_OPERATION_COLLISION');
 }
 fork(input){
  input=branchIntent(input);if(this.#chat)fail('CHAT_PARAMETER_BRANCH_UNAVAILABLE');if(!this.#branches)fail('BRANCH_STORE_REQUIRED');
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
 async lookupSession(id){const wasActive=this.#active.has(id),{data:s}=await this.#load(id);return {id:s.id,revision:s.revision,binding:s.binding,...(this.#chat?{parameterRevision:s.generationParameters.revision}:{}),completedTurns:s.turns.length,busy:wasActive||this.#active.has(id),pending:!!runPending(s)};}
 async read(id){
  // Reading verifies artifacts asynchronously. A task may finish while that
  // older persisted snapshot is being verified; keep polling rather than call
  // this transition an unknown outcome. The next read observes its settlement.
  const wasActive=this.#active.has(id),{data:s}=await this.#load(id),a=await this.#history.authorization(id),busy=wasActive||this.#active.has(id);
  return {session:clone(s),...(this.#chat?{parameterSettings:await this.#parameterView(s)}:{}),busy,pending:!!runPending(s),status:runPending(s)&&!busy?'unknown':s.runs.at(-1)?.state??'idle',
   effectivePolicy:latestPolicy(s,a),pauseReason:pauseReason(s,a),historyConfigurationPolicyRevision:contextPolicyRevision(s.history,a),budget:await this.#budget.status()};
 }
 async waitForIdle(id){await this.#queue;return this.read(id);}
 async modelCatalog(id,parameterVersion=1){if(![1,2].includes(parameterVersion)||parameterVersion===2&&this.#mode!==CONTROLLED_MODE)fail('PARAMETER_CONTROL_V2_REQUIRED');await this.#refreshControl();const r=await this.#load(id);if(!this.#active.has(id)&&!runPending(r.data)&&(await this.#history.pluginService.sessionStatus(id,MODEL_PLUGIN)).enabled)await checkPluginCatalog(this.#history.pluginService,id);return {catalog:parameterVersion===2?await this.#model.parameterCatalog():this.#model.list(),authoredSuggestion:clone(r.runtime.configuration().document.model),sessionRevision:r.data.revision,models:clone(r.data.models),realProvider:this.#mode===CONTROLLED_MODE};}
 async #parameterView(s){
  const authorization=await this.#history.pluginService.parameterAuthorization(s.id),state=s.generationParameters;
  let effective=null,diagnostic=null;try{effective=parameterChoices(s,{revision:state.revision,authorization});}catch(e){diagnostic=e.code??'PARAMETER_CONFIGURATION_INVALID';}
  return {revision:state.revision,purposeRevisions:Object.fromEntries(['story','summary','memory'].map(p=>[p,purposeParameterRevision(state,p)])),authorization,effective,diagnostic};
 }
 async #freezeParameters(s,intent){
  if(!this.#chat){if(Object.hasOwn(intent,'expectedParameterAuthorizationRevision'))fail('CHAT_PARAMETER_HOST_REQUIRED');return null;}
  const authorization=await this.#history.pluginService.parameterAuthorization(s.id);
  if(intent.expectedParameterAuthorizationRevision!==authorization.revision)fail('PARAMETER_AUTHORIZATION_CHANGED');
  const frozen=freezeChatParameters(s,authorization),choices=Object.values(parameterChoices(s,frozen));
  await this.#refreshControl(choices);for(const c of choices)if(c)this.#model.assertCurrent(c);return frozen;
 }
 async parameterCatalog(id,purpose){
  if(!this.#chat||!['story','summary','memory'].includes(purpose))fail('CHAT_PARAMETER_HOST_REQUIRED');
  const r=await this.#load(id);this.#idle(r.data);const result=await this.#history.pluginService.invoke({pluginId:PARAMETER_PLUGIN,sessionId:id,capability:'model.parameters.read',input:{purpose}});
  const base=r.data.models.current[purpose];if(!base)fail('PARAMETER_MODEL_REQUIRED');
  const catalog=await this.#model.parameterCatalog();await this.#history.pluginService.validateResult(result);
  return {sessionRevision:r.data.revision,modelRevision:r.data.models.revision,parameterRevision:r.data.generationParameters.revision,purposeRevision:purposeParameterRevision(r.data.generationParameters,purpose),catalog:{...catalog,models:catalog.models.filter(e=>e.modelId===base.entry.modelId)},purpose};
 }
 configureParameters(value){
  if(!this.#chat)fail('CHAT_PARAMETER_HOST_REQUIRED');const input=chatParameterIntent(value);
  if(this.#active.has(input.sessionId))fail('OFFLINE_SESSION_BUSY');
  return this.#run(async()=>{
   let r=await this.#load(input.sessionId);const s=r.data,state=s.generationParameters,prior=state.changes.find(c=>c.intent.operationId===input.operationId);
   if(prior){if(prior.intentHash!==digest(input))fail('OFFLINE_OPERATION_COLLISION');return {revision:s.revision,change:clone(prior),replayed:true};}
   this.#idle(s);assertJointIdFree(s,input.operationId);await this.#branchUnused(s,input.operationId);
   if(s.revision!==input.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');if(s.models.revision!==input.expectedModelRevision)fail('MODEL_REVISION_CONFLICT');
   if(state.revision!==input.expectedParameterRevision||purposeParameterRevision(state,input.purpose)!==input.expectedPurposeRevision)fail('PARAMETER_REVISION_CONFLICT');
   if(state.changes.length>=CHAT_PARAMETER_LIMIT)fail('PARAMETER_CONFIG_CAPACITY');
   if(!(await this.#history.pluginService.parameterAuthorization(s.id)).enabled)fail('PARAMETER_AUTHORIZATION_REQUIRED');
   const base=s.models.current[input.purpose];if(!base)fail('PARAMETER_MODEL_REQUIRED');
   // Bind to the currently selected purpose before asking the control plane.
   if(input.choice&&input.choice.profile.modelId!==base.entry.modelId)fail('PARAMETER_MODEL_MISMATCH');
   const choice=input.choice===null?null:await this.#model.selectParameters(input.choice);requireParameterModel(base,choice,input.purpose);
   const configurationHash=parameterConfigurationHash(input,base,choice),result=await this.#history.pluginService.invoke({pluginId:PARAMETER_PLUGIN,sessionId:s.id,capability:'session.parameters.configure',input:{purpose:input.purpose,parameterRevision:state.revision,configurationHash}});
   await this.#fault('parameterPrepared',clone(result));
   return this.#history.pluginService.commitResult(result,async()=>{
    if(choice)this.#model.assertCurrent(choice);const {ticket,...receipt}=result;
    const change={revision:state.revision+1,purposeRevision:input.expectedPurposeRevision+1,intent:input,intentHash:digest(input),choice,configurationHash,receipt};
    state.revision++;state.current[input.purpose]=choice;state.changes.push(change);r=await this.#save(r);
    await this.#fault('parameterSaved',clone(change));return {revision:r.data.revision,change:clone(change),replayed:false};
   });
  });
 }
 configureModel(input){
  input=validateModelIntent(input);if(this.#chat)requireChatDefault(input);
  if(this.#active.has(input.sessionId))fail('OFFLINE_SESSION_BUSY');
  return this.#run(async()=>{
   let r=await this.#load(input.sessionId);const s=r.data,prior=s.models.changes.find(c=>c.intent.operationId===input.operationId);
   if(prior){if(prior.intentHash!==digest(input))fail('OFFLINE_OPERATION_COLLISION');return {revision:s.revision,change:clone(prior),replayed:true};}
   this.#idle(s);assertJointIdFree(s,input.operationId);await this.#branchUnused(s,input.operationId);
   if(s.revision!==input.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');if(s.models.revision!==input.expectedModelRevision)fail('MODEL_REVISION_CONFLICT');
   if(s.models.changes.length>=MODEL_CHANGE_LIMIT)fail('MODEL_SETTING_CAPACITY');
   if(this.#chat&&!(await this.#history.pluginService.sessionStatus(s.id,input.purpose==='story'?MODEL_PLUGIN:input.purpose==='summary'?'rpg.rolling-summary':'rpg.memory-palace')).enabled)fail('CHAT_MODEL_AUTHORIZATION_REQUIRED');
   const typed=input.choice?.format===PARAMETER_SELECTION_FORMAT;
   if(typed&&this.#mode!==CONTROLLED_MODE)fail('PARAMETER_CONTROL_V2_REQUIRED');
   const choice=input.choice===null?null:typed?await this.#model.selectParameters(input.choice):this.#model.select(input.choice),change={revision:s.models.revision+1,intent:input,intentHash:digest(input),choice};
   if(choice&&!typed&&this.#mode===CONTROLLED_MODE)await this.#model.validate(choice);
   const commit=async saved=>{if(choice)this.#model.assertCurrent(choice);s.models.revision=saved.revision;s.models.current[input.purpose]=choice;s.models.changes.push(saved);r=await this.#save(r);return {revision:r.data.revision,change:clone(saved),replayed:false};};
   if(input.purpose==='story'&&choice&&(this.#chat||(await this.#history.pluginService.sessionStatus(s.id,MODEL_PLUGIN)).enabled))
    return commitPluginModel({service:this.#history.pluginService,change,resourceHash:s.binding.resourceHash,commit,beforeCommit:receipt=>this.#fault('modelPluginPrepared',receipt)});
   if(this.#chat){
    const a=await this.#history.authorization(s.id);
    if(input.purpose==='summary')return this.#history.pluginService.guardSummaryTask(s.id,a.summary.revision,auth=>{if(!auth.enabled)fail('CHAT_MODEL_AUTHORIZATION_REQUIRED');return commit(change);});
    return this.#history.pluginService.guardMemoryTask(s.id,a,()=>{if(!a.memory.enabled)fail('CHAT_MODEL_AUTHORIZATION_REQUIRED');return commit(change);});
   }
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
 estimate(input){input=jointIntent(input);return this.#run(async()=>{const r=await this.#load(input.sessionId),a=await this.#history.authorization(input.sessionId);await this.#refreshControl(Object.values(r.data.models.current));await this.#branchUnused(r.data,input.operationId);this.#checkIntent(r.data,input,a);await this.#freezeParameters(r.data,input);if(this.#mode===CONTROLLED_MODE)await this.#provider.ready();return {plan:jointPlan(r.data,input.operationId,input.kind,a),budget:await this.#budget.status()};});}
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
    await this.#refreshControl(Object.values(s.models.current));const a=await this.#history.authorization(s.id);await this.#branchUnused(s,input.operationId);this.#checkIntent(s,input,a);if(this.#mode===CONTROLLED_MODE)await this.#provider.ready();
    if(input.kind==='send'){const c=checkPlayerInput(input.input,r.runtime.configuration().document.enhancements.blockedWords);if(!c.allowed)fail(c.issue?'OFFLINE_INPUT_CAPACITY':'OFFLINE_INPUT_BLOCKED');}
    const parameters=await this.#freezeParameters(s,input);
    const plan=jointPlan(s,input.operationId,input.kind,a);if(!plan.stages.length)fail('JOINT_NO_TASKS');
    const run={...(parameters?{parameters}:{}),operationId:input.operationId,intent:input,intentHash:digest(input),plan,groupId:jointGroupId(this.#allowance,plan),historyRevision:s.history.revision,
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
  const s=r.data,run=readRun(r,id),a=run.plan.input.authorization,doc=r.runtime.configuration().document,choice=taskModelChoice(s,s.models.revision,'story',id);
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
 async #guard(s,run,stage,action,dispatch=false){
  const parameters=dispatch&&this.#chat?{authorizationRevision:run.parameters.authorization.revision,sessionRevision:s.revision,parameterRevision:run.parameters.revision}:null;
  if(stage.startsWith('summary')||stage.startsWith('compression'))return this.#history.pluginService.guardSummaryTask(s.id,run.plan.input.authorization.summary.revision,a=>{if(!a.enabled)fail('SUMMARY_AUTHORIZATION_CHANGED');return action(a);},parameters);
  return this.#history.pluginService.guardMemoryTask(s.id,run.plan.input.authorization,()=>action(run.plan.input.authorization.memory),parameters);
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
    if(!same(taskModelChoice(r.data,r.data.models.revision,purpose,request.operationId),choice))fail(purpose==='memory'?'MEMORY_MODEL_CHANGED':purpose==='summary'?'SUMMARY_MODEL_CHANGED':'MODEL_SELECTION_CHANGED');
    await this.#refreshControl([choice]);if(this.#mode===CONTROLLED_MODE)await this.#model.validate(choice);
    this.#model.assertCurrent(choice);this.#provider.preflight(request.operationId);
    const claim=await this.#budget.claim({groupId:run.groupId,stageId:stage,requestId:request.id,requestHash:digest(request)});
    if(!claim.dispatch)fail('JOINT_REQUEST_ALREADY_CLAIMED');
    await this.#fault('afterClaim',clone(request));
    return {response:this.#provider.dispatch(request)};
   },true)).response;
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
 async #accept(r,id,stage,receipt,comparison=false){
  const s=r.data,run=readRun(r,id),item=itemFor(s,run,stage),request=requestFor(item,stage),compressed=stage.startsWith('compression');
  if(receipt?.kind!==(this.#mode===CONTROLLED_MODE?'control-plane':'offline-fixture'))fail('JOINT_RECEIPT_ORIGIN_MISMATCH');
  storyReceipt(receipt,request);
  if(!comparison)await this.#budget.settle({groupId:run.groupId,stageId:stage,requestId:request.id,requestHash:digest(request),status:receipt.result.status==='failed'?(receipt.kind==='control-plane'&&receipt.control.dispatched===false?'not_dispatched':'failed'):'completed',resultHash:receipt.sha256});
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
   return await (comparison?async action=>action(stage==='memory-after'?run.plan.input.authorization.memory:run.plan.input.authorization.summary):action=>this.#guard(s,run,stage,action))(async authorization=>{
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
   let r=await this.#load(input.sessionId),run=readRun(r,input.operationId);if(run?.comparison)fail('COMPARISON_RECOVERY_REQUIRED');if(!run)fail('OFFLINE_OPERATION_NOT_FOUND');
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
 // Trusted coordinator port. These methods are never deserialized from HTTP.
 async #comparisonAuth(id){const service=this.#history.pluginService,context={history:await service.historyAuthorization(id),summary:await service.summaryAuthorization(id),memory:await service.memoryAuthorization(id)},grant=this.#chat?await this.#history.pluginService.comparisonAuthorization(id):null;if(grant&&!grant.enabled)fail('COMPARISON_AUTHORIZATION_REQUIRED');return {context,grant,revision:digest({context,grant})};}
 async comparisonInspect(id){const {data:s}=await this.#load(id);this.#idle(s);const a=await this.#comparisonAuth(id),p=jointPlan(s,'comparison-preflight','send',a.context);return {contextRevision:s.revision,authorizationRevision:a.revision,sourceHash:digest(s),summaryBefore:p.stages.some(t=>t.stageId==='summary-before'),summaryAfter:p.stages.some(t=>t.stageId==='summary-after'),memoryAfter:p.stages.some(t=>t.purpose==='memory')};}
 async comparisonValidate(choice){await this.#refreshControl([choice]);await this.#model.validate(choice);return this.#model.assertCurrent(choice);}
 async comparisonFutureStatus(id){
  const {data:s}=await this.#load(id),service=this.#history.pluginService;
  const permissions=this.#chat?{model:await service.contextAuthorization(id,MODEL_PLUGIN),parameters:await service.parameterAuthorization(id)}:{native:true};
  const allowed=!this.#chat||permissions.model.enabled&&permissions.parameters.enabled;
  return {sessionId:id,expectedRevision:s.revision,expectedModelRevision:s.models.revision,expectedParameterRevision:s.generationParameters?.revision??null,expectedAuthorizationRevision:digest(permissions),allowed,
   reason:allowed?null:'COMPARISON_FUTURE_AUTHORIZATION_REQUIRED',defaultChoice:(this.#chat?(await this.#parameterView(s)).effective?.story:null)??s.models.current.story};
 }
 async comparisonFutureOperation(id,operationId){const {data:s}=await this.#load(id);return clone((s.futureConfigurations??[]).find(p=>p.intent.operationId===operationId)??{status:'not_applied'});}
 configureFutureStory(value,source){
  const i=futureIntent(value);if(this.#active.has(i.sessionId))fail('OFFLINE_SESSION_BUSY');
  return this.#run(async()=>{
   let r=await this.#load(i.sessionId);const s=r.data,prior=priorFuture(s,i,source);if(prior)return prior;
   this.#idle(s);const status=await this.comparisonFutureStatus(s.id);
   for(const k of ['expectedRevision','expectedModelRevision','expectedParameterRevision','expectedAuthorizationRevision'])if(status[k]!==i[k])fail('COMPARISON_FUTURE_CONFIGURATION_CHANGED');
   if(!status.allowed)fail(status.reason);if(s.models.revision>=MODEL_CHANGE_LIMIT||(s.generationParameters?.revision??0)>=CHAT_PARAMETER_LIMIT)fail('PARAMETER_CONFIG_CAPACITY');
   const ids=futureIds(i);for(const id of [i.operationId,ids.model,ids.parameter]){assertJointIdFree(s,id);await this.#branchUnused(s,id);}
   await this.#refreshControl([source]);await this.#model.validate(source);
   const choice=await this.#model.selectParameters(modelChoiceInput(source));if(!same(choice,source))fail('MODEL_CAPABILITY_CHANGED');
   let base=choice;
   if(this.#chat){const input=modelChoiceInput(choice);input.profile.parameters.values={};base=await this.#model.selectParameters(input);}
   const mi={sessionId:s.id,operationId:ids.model,expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose:'story',choice:modelChoiceInput(base)};
   const mc={revision:s.models.revision+1,intent:mi,intentHash:digest(mi),choice:base},service=this.#history.pluginService,tickets=[];
   let pc=null;
   if(this.#chat){
    const pi={sessionId:s.id,operationId:ids.parameter,expectedRevision:s.revision,expectedModelRevision:mc.revision,expectedParameterRevision:s.generationParameters.revision,
     expectedPurposeRevision:purposeParameterRevision(s.generationParameters,'story'),purpose:'story',choice:modelChoiceInput(choice)};
    const ch=parameterConfigurationHash(pi,base,choice);
    tickets.push(await service.invoke({pluginId:MODEL_PLUGIN,sessionId:s.id,capability:'session.model.select',input:{selectionId:base.entry.selectionId,selectionRevision:s.models.revision}}));
    tickets.push(await service.invoke({pluginId:PARAMETER_PLUGIN,sessionId:s.id,capability:'session.parameters.configure',input:{purpose:'story',parameterRevision:s.generationParameters.revision,configurationHash:ch}}));
    const {ticket:mt,...mr}=tickets[0],{ticket:pt,...pr}=tickets[1];mc.pluginReceipt=mr;
    pc={revision:s.generationParameters.revision+1,purposeRevision:pi.expectedPurposeRevision+1,intent:pi,intentHash:digest(pi),choice,configurationHash:ch,receipt:pr};
   }
   await this.#fault('futurePrepared',clone(i));
   const commit=async()=>{
    if((await this.comparisonFutureStatus(s.id)).expectedAuthorizationRevision!==i.expectedAuthorizationRevision)fail('COMPARISON_FUTURE_CONFIGURATION_CHANGED');
    this.#model.assertCurrent(choice);s.models.revision=mc.revision;s.models.current.story=base;s.models.changes.push(mc);
    if(pc){s.generationParameters.revision=pc.revision;s.generationParameters.current.story=choice;s.generationParameters.changes.push(pc);}
    const receipt=futureReceipt(i,choice,s.revision+1,mc.revision,pc?.revision??null);s.futureConfigurations??=[];s.futureConfigurations.push(receipt);
    r=await this.#save(r);await this.#fault('futureSaved',clone(receipt));return clone(receipt);
   };
   return this.#chat?service.commitResults(tickets,commit):commit();
  });
 }
 async comparisonOptions(id){const {data:s}=await this.#load(id);await this.#comparisonAuth(id);const current=this.#chat?(await this.#parameterView(s)).effective?.story:null;return {catalog:await this.#model.parameterCatalog(),defaultChoice:current??s.models.current.story};}
 async comparisonResolve(selection){return this.#model.selectParameters(selection);}
 async comparisonGuard(id,revision,action){const a=await this.#comparisonAuth(id);if(a.revision!==revision)fail('COMPARISON_AUTHORIZATION_CHANGED');return this.#chat?this.#history.pluginService.guardComparison(id,a.grant.revision,a.context,action):this.#history.pluginService.guardMemoryTask(id,a.context,action);}
 async #comparisonStart(plan){
  validateComparisonPlan(plan);let r=await this.#load(plan.intent.sessionId),s=r.data,run=s.runs.find(x=>x.comparison?.plan.id===plan.id);
  if(run){if(!same(run.comparison.plan,plan))fail('COMPARISON_OPERATION_COLLISION');return r;}
  this.#idle(s);const a=await this.#comparisonAuth(s.id);if(s.revision!==plan.state.contextRevision||digest(s)!==plan.state.sourceHash||a.revision!==plan.state.authorizationRevision)fail('COMPARISON_CONTEXT_CHANGED');
  const i={sessionId:s.id,operationId:plan.intent.operationId,expectedRevision:s.revision,expectedModelRevision:s.models.revision,expectedContextPolicyRevision:latestPolicy(s,a.context).revision,expectedSummaryRevision:s.summary.snapshot.revision,expectedMemoryRevision:s.memory.snapshot.revision,kind:'send',input:plan.intent.input,...(this.#chat?{expectedParameterAuthorizationRevision:(await this.#history.pluginService.parameterAuthorization(s.id)).revision}:{})};
  // Reservation and pre-summary work can outlive the catalog cache. Refresh before checking the frozen defaults.
  await this.#refreshControl(Object.values(s.models.current));
  this.#checkIntent(s,i,a.context);const checked=checkPlayerInput(i.input,r.runtime.configuration().document.enhancements.blockedWords);if(!checked.allowed)fail('OFFLINE_INPUT_BLOCKED');
  const parameters=await this.#freezeParameters(s,i),native=jointPlan(s,i.operationId,'send',a.context);
  run={...(parameters?{parameters}:{}),operationId:i.operationId,intent:i,intentHash:digest(i),plan:native,groupId:jointGroupId(this.#allowance,native),historyRevision:s.history.revision,summaryRevision:s.summary.snapshot.revision,memoryRevision:s.memory.snapshot.revision,state:'running',diagnostic:null,settledRevision:null,
   comparison:{plan:clone(plan),choice:clone(plan.intent.candidates[0].choice),context:null,commits:[]}};
  s.runs.push(run);s.draft=i.input;return this.#save(r);
 }
 comparisonFreeze({plan}){return this.#run(async()=>{let r=await this.#comparisonStart(plan),run=readRun(r,plan.intent.operationId);if(run.comparison.context)return clone(run.comparison.context);
  r=await this.#prepareStory(r,run.operationId);run=readRun(r,run.operationId);const request=r.data.operations.at(-1).request,s=r.data;
  const context=freezeContext({contextRevision:s.revision,source:{input:plan.intent.input,history:clone(s.turns),requirements:clone(s.requirements??null),variables:clone(request.memoryContext.variablePanelSnapshot??null),summary:clone(request.memoryContext.summarySnapshot),memory:clone(request.memoryContext.memorySnapshot),worldbook:clone(request.worldbook)},messages:clone(request.messages)});
  run.comparison.context=context;r=await this.#save(r);return clone(context);
 });}
 comparisonPrepare({plan,stage}){return this.#run(async()=>{let r=await this.#comparisonStart(plan),s=r.data,run=readRun(r,plan.intent.operationId),item=itemFor(s,run,stage.name);
  if(!item&&stage.purpose==='summary'){const j=freezeJointSummary(s,run,stage.name);if(j)s.summary.jobs.push(j);r=await this.#save(r);item=itemFor(r.data,run,stage.name);}
  if(!item&&stage.purpose==='memory'){const j=this.#freezeMemory(s,run);if(j)s.memory.jobs.push(j);r=await this.#save(r);item=itemFor(r.data,run,stage.name);}
  if(stage.purpose==='compression'&&!item?.compressionRequest)return null;
  const request=item&&requestFor(item,stage.name);if(!request)fail('COMPARISON_BACKGROUND_NOT_READY');
  const choice=request.modelChoice;return {id:stage.id,operationId:stage.id,sessionId:s.id,kind:stage.purpose,batchId:plan.id,nativeRequestHash:digest(request),messages:clone(request.messages),messagesHash:request.messagesHash,modelChoice:clone(choice),parameters:clone(request.parameters)};
 });}
 async #comparisonCommit(payload,step){const r=await this.#load(payload.sessionId),run=r.data.runs.find(x=>x.comparison?.plan.id===payload.batchId);if(!run)return null;const found=run.comparison.commits.find(c=>c.operationId===payload.operationId);if(found){if(found.payloadHash!==digest(payload))fail('COMPARISON_OPERATION_COLLISION');return clone(found);}return step?{r,run}:null;}
 async comparisonLookup(payload){return this.#comparisonCommit(payload,false);}
 comparisonApply(payload,playback){return this.#run(async()=>{
  let prior=await this.#comparisonCommit(payload,false);if(prior)return prior;let {r,run}=await this.#comparisonCommit(payload,true)??{};if(!r)fail('COMPARISON_HOST_RECORD_MISSING');
  const id=run.operationId,meta=run.comparison;
  if(payload.step==='formal'){
   validateComparisonResult(payload.result,payload.request);if(!meta.context||payload.expectedContextRevision!==meta.context.contextRevision||!same(payload.context,meta.context)||payload.request.contextHash!==meta.context.hash||payload.request.messagesHash!==meta.context.messagesHash||!meta.plan.intent.candidates.some(c=>same(c.choice,payload.request.modelChoice)&&payload.request.id===meta.plan.stages.find(t=>t.candidateId===c.id)?.id))fail('COMPARISON_CONTEXT_CHANGED');
   const op=r.data.operations.find(o=>o.operationId===id);if(!op)fail('COMPARISON_HOST_RECORD_MISSING');
   if(op.state!=='completed'){
    if(op.state!=='prepared')fail('COMPARISON_ADOPTION_CONFLICT');meta.choice=clone(payload.request.modelChoice);op.request.modelConfiguration.choice=clone(meta.choice);op.request.model=meta.choice.entry.modelId;op.request.parameters=clone(meta.choice.wireParameters);op.request.capacity=modelCapacity(meta.choice,op.request.messages);op.requestHash=digest(op.request);
    r=await this.#accept(r,id,'story',comparisonReceipt(op.request,payload.request,payload.result),true);
   }else if(!same(op.receipt.comparison.request,payload.request))fail('COMPARISON_ADOPTION_CONFLICT');
  }else if(payload.step==='variables'||payload.step==='cg'){
   const op=r.data.operations.find(o=>o.operationId===id);if(op?.state!=='completed'||!same(op.receipt.comparison.request,payload.request))fail('COMPARISON_ADOPTION_CONFLICT');
   if(payload.step==='cg'){if(!playback)fail('COMPARISON_PLAYBACK_REQUIRED');const display=await playback.afterStory(r.data.id,id);if(display.state!=='ready')fail('COMPARISON_DISPLAY_PENDING');}
  }else{
   const stage=meta.plan.stages.find(t=>t.name===payload.step);if(!stage||payload.request.id!==stage.id)fail('COMPARISON_STAGE_INVALID');validateComparisonResult(payload.result,payload.request);
   const item=itemFor(r.data,run,stage.name),request=item&&requestFor(item,stage.name);if(!request)fail('COMPARISON_BACKGROUND_NOT_READY');
   const receipt=comparisonReceipt(request,payload.request,payload.result),saved=item[stage.purpose==='compression'?'compressionReceipt':'receipt'];
   if(!saved)r=await this.#accept(r,id,stage.name,receipt,true);else if(!same(saved,receipt))fail('COMPARISON_ADOPTION_CONFLICT');
   const after=itemFor(r.data,readRun(r,id),stage.name);if(!['applied','awaiting_compression'].includes(after.state))fail('COMPARISON_BACKGROUND_REJECTED');
  }
  run=readRun(r,id);const body={operationId:payload.operationId,payloadHash:digest(payload),contextRevision:r.data.revision+1},receipt={...body,hash:digest(body)};run.comparison.commits.push(receipt);await this.#save(r);return receipt;
 });}
 comparisonDraft(payload,plan){return this.#run(async()=>{
  if(!await this.#comparisonCommit(payload,true))await this.#comparisonStart(plan);
  const prior=await this.#comparisonCommit(payload,false);if(prior)return prior;const record=await this.#comparisonCommit(payload,true);if(!record)fail('COMPARISON_HOST_RECORD_MISSING');const {r,run}=record;
  if(payload.expectedContextRevision!==(run.comparison.context?.contextRevision??run.comparison.plan.state.contextRevision)||r.data.turns.some(t=>t.operationId===run.operationId))fail('COMPARISON_ADOPTION_CONFLICT');
  const op=r.data.operations.find(o=>o.operationId===run.operationId);if(op){if(op.state!=='prepared')fail('COMPARISON_ADOPTION_CONFLICT');op.state='not_dispatched';op.diagnostic='COMPARISON_DISCARDED';}r.data.draft=payload.input;
  const body={operationId:payload.operationId,payloadHash:digest(payload),contextRevision:r.data.revision+1},receipt={...body,hash:digest(body)};run.comparison.commits.push(receipt);await this.#save(r);return receipt;
 });}
 comparisonSettled(batch){if(batch.status!=='settled')return;return this.#run(async()=>{const r=await this.#load(batch.plan.intent.sessionId),run=r.data.runs.find(x=>x.comparison?.plan.id===batch.plan.id);if(!run||!RUN_PENDING.includes(run.state))return;
  run.state=batch.decision?.kind==='adopt'?'completed':'not_dispatched';run.diagnostic=null;run.settledRevision=r.data.revision+1;await this.#save(r);
 });}
 async reconcileStorage(){return this.#run(async()=>({session:await this.#records.reconcile(),budget:await this.#budget.reconcile()}));}
 async close(){if(this.#closed)return;this.#closed=true;await this.#queue;await this.#records.close();}
}
