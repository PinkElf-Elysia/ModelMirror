import {legacyArtifacts} from '../character/legacy-compat.mjs';
// B6-I adapter of offline-session.mjs. The original host stays byte-identical
// because retained acceptance services verify it live. Shared pure kernels and
// storage remain imported; consolidation requires a later explicit rollout.
import {readFile} from 'node:fs/promises';
import {OfflineFixtureProvider} from './offline-session.mjs';
import {scenarioFixturePort} from './fixture-scenario.mjs';
import {summaryContextPolicy,assembleSummaryContext,replaySummaryContext} from './summary-context.mjs';
import {inspectSummaryResult,createSummaryCompression,commitSummaryUpdate,SUMMARY_LIMITS} from './summary-state.mjs';
import {initialSummaryJobs,validateSummaryJobs,pendingSummary,summaryOwnsId,assertSummaryIdFree,summaryIntent,
  freezeSummaryJob,autoSummaryIntent,summaryJobCandidates,settleSummaryJob,summaryAt,modelsAt,summaryJobId,compressionJobId,
  SUMMARY_LOCAL_FAILURES,SUMMARY_PENDING,SUMMARY_JOB_LIMIT,SUMMARY_RECEIPT_RESERVE} from './summary-jobs.mjs';
import {randomBytes} from 'node:crypto';
import {canonical, keys, isId, isHash, sha256, fail} from './contracts.mjs';
import {SessionRecords, SESSION_RECORD_LIMIT} from './session-storage.mjs';
import {requirePublishedRuntime, runtimeReference} from './runtime-binding.mjs';
import {buildWorldbookPlan, replayWorldbookPlan} from './worldbook.mjs';
import {checkPlayerInput} from './input-assistance.mjs';
import {dialoguePreview} from './dialogue.mjs';
import {requireOfflineModelControl, emptyModelState, validateModelIntent, validateModelState, modelChoiceAt, modelCapacity, MODEL_CHANGE_LIMIT} from './model-control.mjs';

import {HISTORY_CHANGE_LIMIT, requireHistoryControl, emptyHistoryState, validateHistoryIntent, validateHistoryState, contextPolicyRevision, historyStateAt} from './history-control.mjs';

export const SUMMARY_SESSION_FORMAT = 'modelmirror.rpg.offline-session/4';
export const SUMMARY_MODE = 'offline-fixture-summary';
export const OFFLINE_LIMITS = Object.freeze({operations:1000, replyCharacters:200000, messageCharacters:2000000,
  worldbook:{scanChars:2000000,injectedChars:1000000,systemChars:1000000,userChars:1000000}});
const digest = value => sha256(canonical(value));
const same = (a,b) => canonical(a) === canonical(b);
const integer = value => Number.isSafeInteger(value) && value >= 0;
const raw = value => typeof value === 'string' && value.isWellFormed();
const pendingStates = new Set(['prepared','dispatching','unknown']);
const terminalStates = new Set(['completed','incomplete','invalid','failed','not_dispatched']);
const timestamp = () => new Date().toISOString();
const sessionId = operationId => 'session-' + sha256('summary-session:' + operationId);
const requestId = (id, operationId) => 'request-' + digest({sessionId:id,operationId});
const sample = () => randomBytes(6).readUIntBE(0,6) / 281474976710656;
const artifactPaths = ['model-plugin.mjs','controlled-receipt.mjs','summary-session.mjs','fixture-scenario.mjs','summary-jobs.mjs','summary-state.mjs','summary-context.mjs','../plugins/rolling-summary.mjs','../plugins/history-window.mjs','../plugins/host.mjs','contracts.mjs','runtime-binding.mjs','runtime-plugins.mjs','offline-session.mjs','history-control.mjs','model-control.mjs','session-storage.mjs','worldbook.mjs','worldbook-regex.mjs','input-assistance.mjs','dialogue.mjs'];
const artifacts = async () => legacyArtifacts(Object.fromEntries(await Promise.all(artifactPaths.map(async path => [path,sha256(await readFile(new URL(path,import.meta.url)))]))),import.meta.url);
const loadedArtifacts = await artifacts(), executionHash = digest(loadedArtifacts);
function snapshot(value, fields) { canonical(value); keys(value,fields); return structuredClone(value); }
function checkOperation(id) { if (!isId(id)) fail('OFFLINE_OPERATION_INVALID'); }
function checkSession(id) { if (typeof id !== 'string' || !/^session-[a-f0-9]{64}$/.test(id)) fail('OFFLINE_SESSION_INVALID'); }
function resultValue(value) {
  const out = snapshot(value,['status','text','finishReason']);
  if (!['completed','failed'].includes(out.status) || !raw(out.text) || [...out.text].length > OFFLINE_LIMITS.replyCharacters ||
      !(out.status === 'completed' ? ['stop','length'].includes(out.finishReason) : out.finishReason === 'fixture_failure')) fail('OFFLINE_FIXTURE_INVALID');
  return out;
}
function receiptValue(receipt, request) {
  keys(receipt,['kind','requestId','requestHash','result','sha256']);
  const {sha256:hash,...body} = receipt;
  if (receipt.kind !== 'offline-fixture' || receipt.requestId !== request.id || receipt.requestHash !== digest(request) || hash !== digest(body)) fail('OFFLINE_RECEIPT_MISMATCH');
  resultValue(receipt.result); return receipt;
}
// The original provider owns the private brand and durable receipts. Capture its
// methods rather than accepting an arbitrary transport object or an overridden
// instance method. This host has no network/provider adapter.
const fixtureCount=Object.getOwnPropertyDescriptor(OfflineFixtureProvider.prototype,'dispatchCount').get;
const fixtureMethods=Object.fromEntries(['preflight','dispatch','lookup'].map(k=>[k,OfflineFixtureProvider.prototype[k]]));
function isFixture(value) { try { fixtureCount.call(value); return true; } catch { return scenarioFixturePort(value)!==null; } }
function fixturePort(value) {
  const scenario=scenarioFixturePort(value);if(scenario)return scenario;
  if(!isFixture(value))fail('OFFLINE_HOST_REQUIRED');
  return Object.freeze(Object.fromEntries(Object.entries(fixtureMethods).map(([k,fn])=>[k,fn.bind(value)])));
}

function validateState(s) {
  const contextual=true, controlled=true;
  keys(s,['format','id','revision','createdAt','updatedAt','create','binding','executionHash','presetId','examples','draft','operations','turns','summary',...(controlled?['models']:[]),...(contextual?['history']:[])]);
  checkSession(s.id); keys(s.create,['operationId','requestHash']); checkOperation(s.create.operationId);
  if (s.format!==SUMMARY_SESSION_FORMAT || !integer(s.revision) || s.id!==sessionId(s.create.operationId) || !isHash(s.create.requestHash) ||
      s.executionHash!==executionHash || !raw(s.draft) || !raw(s.createdAt) || !raw(s.updatedAt) || !(s.presetId===null || isId(s.presetId))) fail('OFFLINE_SESSION_CORRUPT');
  if (!Array.isArray(s.examples) || s.examples.length>200 || s.examples.length%2 || !Array.isArray(s.operations) || s.operations.length>OFFLINE_LIMITS.operations || !Array.isArray(s.turns)) fail('OFFLINE_SESSION_CORRUPT');
  s.examples.forEach((message,i)=>{keys(message,['role','content']); if(message.role!==(i%2?'assistant':'user')||!raw(message.content))fail('OFFLINE_SESSION_CORRUPT');});
  if(controlled)validateModelState(s.models,s.id,s.revision);
  if(contextual)validateHistoryState(s.history,s.id,s.revision,s.binding.resourceHash);
  const configIds=[...(controlled?s.models.changes.map(c=>c.intent.operationId):[]),...(contextual?s.history.changes.map(c=>c.intent.operationId):[])];
  const ids=new Set(configIds); if(ids.size!==configIds.length || (contextual&&ids.has(s.create.operationId)))fail('OFFLINE_SESSION_CORRUPT');
  let pending=0; const turns=[];
  for (const [i,op] of s.operations.entries()) {
    keys(op,['operationId','intent','intentHash','request','requestHash','state','receipt','diagnostic']);
    checkOperation(op.operationId); keys(op.intent,['sessionId','operationId','expectedRevision','input',...(controlled?['expectedModelRevision']:[]),...(contextual?['expectedContextPolicyRevision']:[])]);
    if ((contextual && (op.operationId===s.create.operationId || !isHash(op.intent.expectedContextPolicyRevision))) || ids.has(op.operationId) || op.intent.sessionId!==s.id || op.intent.operationId!==op.operationId || !integer(op.intent.expectedRevision) || op.intent.expectedRevision>=s.revision || (i>0&&op.intent.expectedRevision<=s.operations[i-1].intent.expectedRevision) || !raw(op.intent.input) ||
        op.intentHash!==digest(op.intent) || ![...pendingStates,...terminalStates].includes(op.state)) fail('OFFLINE_SESSION_CORRUPT');
    ids.add(op.operationId);
    const r=op.request; keys(r,['id','operationId','sessionId','bindingHash','executionHash','mode','model','policy','createdAt','rawInput','worldbook','draws','messages','messagesHash','summaryContext',...(controlled?['modelConfiguration','parameters','capacity']:[])]);
    if(r.id!==requestId(s.id,op.operationId)||r.operationId!==op.operationId||r.sessionId!==s.id||r.bindingHash!==digest(s.binding)||r.executionHash!==executionHash||
      r.mode!==SUMMARY_MODE||r.rawInput!==op.intent.input||r.messagesHash!==digest(r.messages)||op.requestHash!==digest(r)||!raw(r.createdAt))fail('OFFLINE_SESSION_CORRUPT');
    if(controlled){
      keys(r.modelConfiguration,['revision','choice']);
      const revision=r.modelConfiguration.revision,choice=modelChoiceAt(s.models,revision,'story');
      if(!choice||revision!==op.intent.expectedModelRevision||!same(r.modelConfiguration.choice,choice)||r.model!==choice.entry.modelId||
          !same(r.parameters,choice.wireParameters)||!same(r.capacity,modelCapacity(choice,r.messages)))fail('MODEL_REQUEST_CORRUPT');
      if(s.models.changes.some(c=>(c.revision<=revision)!==(c.intent.expectedRevision<op.intent.expectedRevision)))fail('MODEL_REQUEST_CORRUPT');
    }
    const p=replayWorldbookPlan(r.worldbook,{operationId:op.operationId,revision:op.intent.expectedRevision,contextHash:r.worldbook.contextHash});
    const ctx=r.summaryContext;
    const historyRevision=s.history.changes.filter(c=>c.intent.expectedRevision<op.intent.expectedRevision).length;
    if(ctx.sessionRevision!==op.intent.expectedRevision||ctx.completedTurns!==turns.length||
      !same(ctx.models,modelsAt(s.models,op.intent.expectedModelRevision))||
      !same(ctx.summarySnapshot,summaryAt(s.summary.snapshot,r.policy.summaryRevision))||
      !same(ctx.policy,r.policy)||r.policy.revision!==op.intent.expectedContextPolicyRevision||
      !same(r.policy.historyConfig,historyStateAt(s.history,historyRevision).config))fail('SUMMARY_CONTEXT_EVIDENCE_INVALID');
    const expected=replaySummaryContext({...s,turns},{system:p.assembly.system,user:p.assembly.user},ctx);
    if (!same(r.messages,expected)) fail('OFFLINE_SESSION_CORRUPT');
    if (pendingStates.has(op.state)) { pending++; if(i!==s.operations.length-1||op.receipt!==null)fail('OFFLINE_SESSION_CORRUPT'); }
    if (op.receipt!==null) receiptValue(op.receipt,r);
    if (op.state==='not_dispatched' && op.receipt!==null) fail('OFFLINE_SESSION_CORRUPT');
    if (['completed','incomplete','invalid','failed'].includes(op.state)) {
      if(!op.receipt||outcomeState(op.receipt.result)!==op.state)fail('OFFLINE_SESSION_CORRUPT');
      if(op.state==='completed')turns.push(turnValue(op,turns.length+1));
    }
    if (!(op.diagnostic===null || ['OFFLINE_RESULT_UNKNOWN','OFFLINE_NOT_DISPATCHED','MODEL_SELECTION_CHANGED','MODEL_SELECTION_UNAVAILABLE','MODEL_CAPABILITY_UNKNOWN','MODEL_CONTEXT_CAPACITY',...(contextual?['HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH']:[])].includes(op.diagnostic))) fail('OFFLINE_SESSION_CORRUPT');
  }
  if(pending>1||!same(s.turns,turns))fail('OFFLINE_SESSION_CORRUPT');
  validateSummaryJobs(s);
  if(pending&&pendingSummary(s))fail('SUMMARY_JOURNAL_CORRUPT');
  return s;
}
function outcomeState(out) { return out.status==='failed'?'failed':out.finishReason==='length'?'incomplete':!out.text.trim()?'invalid':'completed'; }
function turnValue(op,number) {
  return {id:'turn-'+number,number,operationId:op.operationId,requestId:op.request.id,complete:true,user:op.request.rawInput,
    userMessage:op.request.messages.at(-1).content,assistant:op.receipt.result.text,rawHash:sha256(op.receipt.result.text),messagesHash:op.request.messagesHash};
}

export class SummarySessionHost {
  #records; #resolve; #provider; #draw; #fault; #modelControl; #historyControl; #queue=Promise.resolve(); #closed=false; #active=new Set();
  constructor(records,resolveRuntime,provider,draw,fault,modelControl,historyControl=null) { if (!(records instanceof SessionRecords)||!isFixture(provider)||typeof resolveRuntime!=='function'||typeof draw!=='function'||typeof fault!=='function')fail('OFFLINE_HOST_REQUIRED'); this.#records=records; this.#resolve=resolveRuntime; this.#provider=fixturePort(provider); this.#draw=draw; this.#fault=fault; this.#modelControl=modelControl===null?null:requireOfflineModelControl(modelControl); this.#historyControl=historyControl===null?null:requireHistoryControl(historyControl); if(!this.#modelControl)fail('MODEL_CONTROL_REQUIRED');if(!this.#historyControl)fail('HISTORY_CONTROL_HOST_REQUIRED'); }
  static async open({directory,resolveRuntime,provider,recoverDeadOwner=false,draw=sample,fault=async()=>{},storageFault=async()=>{},modelControl=null,historyControl=null}) {
    if(typeof resolveRuntime!=='function'||!isFixture(provider)||typeof draw!=='function'||typeof fault!=='function')fail('OFFLINE_HOST_REQUIRED');
    requireOfflineModelControl(modelControl);requireHistoryControl(historyControl);
    const records=await SessionRecords.open(directory,{recoverDeadOwner,fault:storageFault,scope:'authored-summary-sessions'});
    try {
      // Local CAS reconciliation never dispatches or applies a provider result.
      await records.reconcile();
      return Object.freeze(new SummarySessionHost(records,resolveRuntime,provider,draw,fault,modelControl,historyControl));
    } catch(error) { await records.close(); throw error; }
  }
  get readiness() { return {mode:SUMMARY_MODE,sessionPersistence:true,offlineDispatch:true,controlledModels:true,runtimeReady:false,
    pluginExecution:true,historyWindow:true,summaryExecution:true,memoryExecution:false,realProvider:false,executionHash,artifactHashes:{...loadedArtifacts}}; }
  #run(action) {
    if(this.#closed)return Promise.reject(Object.assign(new Error('OFFLINE_HOST_CLOSED'),{code:'OFFLINE_HOST_CLOSED'}));
    const result=this.#queue.then(action);this.#queue=result.catch(()=>{});return result;
  }
  async #runtime(binding) {
    if(digest(await artifacts())!==executionHash)fail('OFFLINE_FILES_CHANGED_RESTART_REQUIRED');
    const runtime=requirePublishedRuntime(await this.#resolve(runtimeReference({workId:binding.workId,versionId:binding.versionId})));
    runtime.requireBinding(binding); await runtime.verify(); return runtime;
  }
  async #load(id) {
    checkSession(id);const record=await this.#records.get(id);if(!record)fail('OFFLINE_SESSION_NOT_FOUND');
    validateState(record.data);
    const runtime=await this.#runtime(record.data.binding);
    const preview=dialoguePreview(runtime.configuration().document.enhancements,record.data.presetId);
    if(preview.presetId!==record.data.presetId||!same(preview.messages,record.data.examples))fail('OFFLINE_SESSION_CORRUPT');
    return {...record,runtime};
  }
  async #save(record) {
    record.data.revision++;record.data.updatedAt=timestamp();validateState(record.data);
    const saved=await this.#records.put(record.data.id,record.hash,record.data);
    return {...saved,runtime:record.runtime};
  }
  create(input) {
    input=snapshot(input,['operationId','expectedRevision','reference','openingText','presetId']);checkOperation(input.operationId);
    const ref=runtimeReference(input.reference);if(input.expectedRevision!==null)fail('OFFLINE_REVISION_INVALID');
    return this.#run(async()=>{
      if(digest(await artifacts())!==executionHash)fail('OFFLINE_FILES_CHANGED_RESTART_REQUIRED');
      const id=sessionId(input.operationId), hash=digest(input), prior=await this.#records.get(id);
      if(prior){if(prior.data.create?.requestHash!==hash)fail('OFFLINE_OPERATION_COLLISION');await this.#load(id);return {session:structuredClone(prior.data),replayed:true};}
      const runtime=requirePublishedRuntime(await this.#resolve(ref));await runtime.verify();
      const binding=runtime.bindSession({sessionId:id,openingText:input.openingText});
      const preview=dialoguePreview(runtime.configuration().document.enhancements,input.presetId);
      if(input.presetId!==preview.presetId)fail('OFFLINE_PRESET_INVALID');
      const s={format:SUMMARY_SESSION_FORMAT,id,revision:0,createdAt:timestamp(),updatedAt:timestamp(),create:{operationId:input.operationId,requestHash:hash},binding,
        executionHash,presetId:input.presetId,examples:preview.messages,draft:input.openingText,operations:[],turns:[],...(this.#modelControl?{models:emptyModelState()}: {}),...(this.#historyControl?{history:emptyHistoryState()}: {})};
      s.summary=initialSummaryJobs(s);validateState(s);await this.#records.put(id,null,s);return {session:structuredClone(s),replayed:false};
    });
  }
  async read(id) {
    const {data:s}=await this.#load(id),op=s.operations.at(-1),job=s.summary.jobs.at(-1),busy=this.#active.has(id);
    const authorization=await this.#historyControl.authorization(id);
    return {session:structuredClone(s),busy,pending:s.operations.some(o=>pendingStates.has(o.state))||!!pendingSummary(s),
      status:op?.state==='dispatching'&&!busy?'unknown':op?.state??'idle',
      summaryStatus:job&&!busy&&['dispatching','compression_dispatching'].includes(job.state)?'unknown':job?.state??'idle',
      effectivePolicy:summaryContextPolicy(s,s.summary.snapshot,{authorization,historyConfig:s.history.config}),
      historyConfigurationPolicyRevision:contextPolicyRevision(s.history,authorization)};
  }
  async lookupSession(id) {
    // Registry callbacks cannot recursively read authorization.
    const {data:s}=await this.#load(id);
    return {id:s.id,revision:s.revision,binding:s.binding,completedTurns:s.turns.length,busy:this.#active.has(id),
      pending:s.operations.some(o=>pendingStates.has(o.state))||!!pendingSummary(s)};
  }
  async waitForIdle(id) { await this.#queue;return this.read(id); }
  async modelCatalog(id) {
    if(!this.#modelControl)fail('MODEL_CONTROL_REQUIRED');
    const record=await this.#load(id);
    return {catalog:this.#modelControl.list(),authoredSuggestion:structuredClone(record.runtime.configuration().document.model),
      sessionRevision:record.data.revision,models:structuredClone(record.data.models),realProvider:false};
  }
  configureHistory(input) {
    input=validateHistoryIntent(input);
    if(!this.#historyControl)fail('HISTORY_CONTROL_HOST_REQUIRED');
    if(this.#active.has(input.sessionId))fail('OFFLINE_SESSION_BUSY');
    return this.#run(async()=>{
      let record=await this.#load(input.sessionId);const s=record.data;
      const prior=s.history.changes.find(c=>c.intent.operationId===input.operationId);
      if(prior){if(prior.intentHash!==digest(input))fail('OFFLINE_OPERATION_COLLISION');return {revision:s.revision,change:structuredClone(prior),replayed:true};}
      if(input.operationId===s.create.operationId||s.operations.some(o=>o.operationId===input.operationId)||s.models.changes.some(c=>c.intent.operationId===input.operationId))fail('OFFLINE_OPERATION_COLLISION');
      if(s.operations.some(o=>pendingStates.has(o.state)))fail('OFFLINE_RESULT_PENDING');
      if(pendingSummary(s))fail('SUMMARY_RESULT_PENDING');
      if(summaryOwnsId(s,input.operationId))fail('OFFLINE_OPERATION_COLLISION');
      if(s.revision!==input.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');
      if(s.history.revision!==input.expectedHistoryRevision)fail('HISTORY_REVISION_CONFLICT');
      if(s.history.changes.length>=HISTORY_CHANGE_LIMIT)fail('HISTORY_SETTING_CAPACITY');
      return this.#historyControl.commitConfiguration(input,s.history,async change=>{
        s.history.revision=change.revision;s.history.config={...input.config};s.history.changes.push(change);
        record=await this.#save(record);
        return {revision:record.data.revision,change:structuredClone(change),replayed:false};
      });
    });
  }
  configureModel(input) {
    input=validateModelIntent(input);checkSession(input.sessionId);
    if(!this.#modelControl)fail('MODEL_CONTROL_REQUIRED');
    if(this.#active.has(input.sessionId))fail('OFFLINE_SESSION_BUSY');
    return this.#run(async()=>{
      let record=await this.#load(input.sessionId);const s=record.data;
      const prior=s.models.changes.find(c=>c.intent.operationId===input.operationId);
      if(prior){if(prior.intentHash!==digest(input))fail('OFFLINE_OPERATION_COLLISION');return {revision:s.revision,change:structuredClone(prior),replayed:true};}
      if(s.operations.some(o=>o.operationId===input.operationId)||(s.history&&(input.operationId===s.create.operationId||s.history.changes.some(c=>c.intent.operationId===input.operationId))))fail('OFFLINE_OPERATION_COLLISION');
      if(s.operations.some(o=>pendingStates.has(o.state)))fail('OFFLINE_RESULT_PENDING');
      if(pendingSummary(s))fail('SUMMARY_RESULT_PENDING');
      if(summaryOwnsId(s,input.operationId))fail('OFFLINE_OPERATION_COLLISION');
      if(s.revision!==input.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');
      if(s.models.revision!==input.expectedModelRevision)fail('MODEL_REVISION_CONFLICT');
      if(s.models.changes.length>=MODEL_CHANGE_LIMIT)fail('MODEL_SETTING_CAPACITY');
      const choice=input.choice===null?null:this.#modelControl.select(input.choice);
      const change={revision:s.models.revision+1,intent:input,intentHash:digest(input),choice};
      s.models.revision=change.revision;s.models.current[input.purpose]=choice;s.models.changes.push(change);
      record=await this.#save(record);
      return {revision:record.data.revision,change:structuredClone(change),replayed:false};
    });
  }
  #reply(record,op,replayed=false) { return {sessionId:record.data.id,revision:record.data.revision,operation:structuredClone(op),replayed,mode:SUMMARY_MODE}; }
  send(input) {
    input=snapshot(input,['sessionId','operationId','expectedRevision','input',...(this.#modelControl?['expectedModelRevision']:[]),...(this.#historyControl?['expectedContextPolicyRevision']:[])]);checkSession(input.sessionId);checkOperation(input.operationId);
    if(!integer(input.expectedRevision)||(this.#modelControl&&!integer(input.expectedModelRevision))||(this.#historyControl&&!isHash(input.expectedContextPolicyRevision))||!raw(input.input)||!input.input.trim())fail('OFFLINE_SEND_INVALID');
    let resolveStory,rejectStory;const delivered=new Promise((resolve,reject)=>{resolveStory=resolve;rejectStory=reject;});
    const work=this.#run(async()=>{
      this.#active.add(input.sessionId);
      try {
        let record=await this.#load(input.sessionId),s=record.data;
        const prior=s.operations.find(op=>op.operationId===input.operationId);
        if(prior){if(prior.intentHash!==digest(input))fail('OFFLINE_OPERATION_COLLISION');return this.#reply(record,prior,true);}
        if(s.models?.changes.some(c=>c.intent.operationId===input.operationId)||(s.history&&(input.operationId===s.create.operationId||s.history.changes.some(c=>c.intent.operationId===input.operationId))))fail('OFFLINE_OPERATION_COLLISION');
        if(s.operations.some(op=>pendingStates.has(op.state)))fail('OFFLINE_RESULT_PENDING');
        if(pendingSummary(s))fail('SUMMARY_RESULT_PENDING');
        if(summaryOwnsId(s,input.operationId)||summaryOwnsId(s,compressionJobId(input.operationId)))fail('OFFLINE_OPERATION_COLLISION');
        if(s.revision!==input.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');
        const authorization=this.#historyControl?await this.#historyControl.authorization(s.id):null;
        const effective=summaryContextPolicy(s,s.summary.snapshot,{authorization,historyConfig:s.history.config});
        if(effective.revision!==input.expectedContextPolicyRevision)fail('SUMMARY_POLICY_CONFLICT');
        if(!effective.canAssemble)fail(effective.reason);
        if(authorization.summary.enabled) {
          if(s.summary.jobs.length>=SUMMARY_JOB_LIMIT)fail('SUMMARY_JOB_CAPACITY');
          assertSummaryIdFree(s,summaryJobId(s.id,input.operationId));
          assertSummaryIdFree(s,compressionJobId(summaryJobId(s.id,input.operationId)));
        }
        if(s.turns.reduce((n,t)=>n+[...t.user].length+[...t.assistant].length,0)+[...input.input].length+OFFLINE_LIMITS.replyCharacters>SUMMARY_LIMITS.sourceCharacters)fail('SUMMARY_SOURCE_CAPACITY');
        const choice=s.models?.current.story;
        if(this.#modelControl){
          if(input.expectedModelRevision!==s.models.revision)fail('MODEL_REVISION_CONFLICT');
          if(!choice)fail('MODEL_SELECTION_REQUIRED');
          this.#modelControl.assertCurrent(choice);
        }
        if(!s.turns.length&&input.input!==s.binding.openingText)fail('OFFLINE_OPENING_MISMATCH');
        if(s.operations.length>=OFFLINE_LIMITS.operations)fail('OFFLINE_OPERATION_CAPACITY');
        const document=record.runtime.configuration().document,check=checkPlayerInput(input.input,document.enhancements.blockedWords);
        if(!check.allowed)fail(check.issue?'OFFLINE_INPUT_CAPACITY':'OFFLINE_INPUT_BLOCKED');
        this.#provider.preflight(input.operationId);
        const draws=document.worldbook.entries.filter(e=>e.enabled).map(e=>({id:e.id,trigger:e.chance>0&&e.chance<100?this.#draw():null,content:e.mode==='weighted'?this.#draw():null}));
        const plan=await buildWorldbookPlan({operationId:input.operationId,revision:s.revision,document,currentInput:input.input,
          turns:s.turns.map(t=>({id:t.id,complete:true,user:t.user,assistant:t.assistant})),draws,capacity:{...OFFLINE_LIMITS.worldbook}});
        const assembled=assembleSummaryContext({session:s,state:s.summary.snapshot,authorization,historyConfig:s.history.config,base:{system:plan.assembly.system,user:plan.assembly.user},expectedPolicyRevision:input.expectedContextPolicyRevision});
        const messages=assembled.messages;
        if(messages.reduce((sum,m)=>sum+[...m.content].length,0)>OFFLINE_LIMITS.messageCharacters)fail('OFFLINE_CONTEXT_CAPACITY');
        const capacity=this.#modelControl?modelCapacity(choice,messages):null;
        if(this.#modelControl)this.#modelControl.assertCurrent(choice);
        const request={id:requestId(s.id,input.operationId),operationId:input.operationId,sessionId:s.id,bindingHash:digest(s.binding),executionHash,
          mode:SUMMARY_MODE,model:choice?.entry.modelId??'offline-fixture',policy:assembled.evidence.policy,summaryContext:assembled.evidence,createdAt:timestamp(),rawInput:input.input,worldbook:plan,draws,messages,messagesHash:digest(messages),
          ...(this.#modelControl?{modelConfiguration:{revision:s.models.revision,choice:structuredClone(choice)},parameters:{...choice.wireParameters},capacity}: {})};
        let op={operationId:input.operationId,intent:input,intentHash:digest(input),request,requestHash:digest(request),state:'prepared',receipt:null,diagnostic:null};
        s.operations.push(op);s.draft=input.input;
        // Reserve room for worst-case escaped receipt and duplicate raw turn text.
        if(Buffer.byteLength(canonical(s))+OFFLINE_LIMITS.replyCharacters*18+65536>SESSION_RECORD_LIMIT)fail('OFFLINE_RECORD_CAPACITY');
        record=await this.#save(record);await this.#fault('afterPrepared',structuredClone(request));
        op=record.data.operations.at(-1);op.state='dispatching';record=await this.#save(record);
        await this.#fault('beforeDispatch',structuredClone(request));
        // The registry guard awaits file-backed authorization. Recheck model
        // qualification inside its start callback, immediately before transport.
        let response;
        try {
          const start=()=>{if(this.#modelControl)this.#modelControl.assertCurrent(choice);return this.#provider.dispatch(request);};
          response=(this.#historyControl?await this.#historyControl.startDispatch(s.id,authorization,start):{response:start()}).response;
        } catch(error) {
          const known=['MODEL_SELECTION_CHANGED','MODEL_SELECTION_UNAVAILABLE','MODEL_CAPABILITY_UNKNOWN','MODEL_CONTEXT_CAPACITY','HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH'];
          if(!known.includes(error.code))throw error;
          op=record.data.operations.at(-1);op.state='not_dispatched';op.diagnostic=error.code;record=await this.#save(record);
          return this.#reply(record,record.data.operations.at(-1));
        }
        let receipt;
        try {receipt=await response;}
        catch {
          // A transport error after start is always uncertain, even if its code
          // happens to resemble a local qualification/authorization rejection.
          op=record.data.operations.at(-1);op.state='unknown';op.diagnostic='OFFLINE_RESULT_UNKNOWN';record=await this.#save(record);
          return this.#reply(record,record.data.operations.at(-1));
        }
        await this.#fault('afterProvider',structuredClone(request));
        record=await this.#apply(record,receipt);
        const result=this.#reply(record,record.data.operations.at(-1));
        resolveStory(result);
        await this.#fault('afterStorySaved',structuredClone(record.data));
        const job=record.data.summary.jobs.at(-1);
        if(job?.trigger.operationId===input.operationId&&job.state==='prepared')await this.#runSummary(record,job.operationId);
        return result;
      } finally { this.#active.delete(input.sessionId); }
    });
    work.then(resolveStory,rejectStory);
    return delivered;
  }
  async #apply(record,receipt) {
    const s=record.data,op=s.operations.at(-1);receiptValue(receipt,op.request);
    op.receipt=structuredClone(receipt);op.state=outcomeState(receipt.result);op.diagnostic=null;
    if(op.state==='completed'){
      s.turns.push(turnValue(op,s.turns.length+1));s.draft='';
      const auth=op.request.policy.authorization;
      if(auth.summary.enabled) {
        const job=freezeSummaryJob(s,autoSummaryIntent(s,op,auth),auth,{kind:'after-story',operationId:op.operationId});
        if(job)s.summary.jobs.push(job);
      }
    }
    return this.#save(record);
  }
  recover(input) {
    input=snapshot(input,['sessionId','operationId','expectedRevision']);checkSession(input.sessionId);checkOperation(input.operationId);
    if(!integer(input.expectedRevision))fail('OFFLINE_REVISION_INVALID');
    return this.#run(async()=>{
      await this.#records.reconcile();
      let record=await this.#load(input.sessionId),op=record.data.operations.find(x=>x.operationId===input.operationId);
      if(!op)fail('OFFLINE_OPERATION_NOT_FOUND');
      if(terminalStates.has(op.state))return this.#reply(record,op,true);
      if(record.data.revision!==input.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');
      if(op.state==='prepared'){
        op.state='not_dispatched';op.diagnostic='OFFLINE_NOT_DISPATCHED';record=await this.#save(record);
      } else {
        // Only lookup of the frozen request; never dispatch, rebuild or draw here.
        const receipt=await this.#provider.lookup(op.request);
        if(receipt)record=await this.#apply(record,receipt);
        else if(op.state!=='unknown') {op.state='unknown';op.diagnostic='OFFLINE_RESULT_UNKNOWN';record=await this.#save(record);}
      }
      return this.#reply(record,record.data.operations.find(x=>x.operationId===input.operationId));
    });
  }
  #summaryReply(record,id,replayed=false) {
    return {sessionId:record.data.id,revision:record.data.revision,summaryRevision:record.data.summary.snapshot.revision,
      job:structuredClone(record.data.summary.jobs.find(j=>j.operationId===id)),replayed,mode:SUMMARY_MODE};
  }
  async #summaryGuard(record,job,action) {
    return this.#historyControl.pluginService.guardSummaryTask(record.data.id,job.authorization.summary.revision,auth=>{
      if(!auth.enabled)fail('SUMMARY_AUTHORIZATION_CHANGED');return action(auth);
    });
  }
  async #runSummary(record,id) {
    let job=record.data.summary.jobs.find(j=>j.operationId===id);
    if(!['prepared','awaiting_compression'].includes(job.state))fail('SUMMARY_PHASE_INVALID');
    const compression=job.state==='awaiting_compression',request=compression?job.compressionRequest:job.request;
    job.state=compression?'compression_dispatching':'dispatching';
    record=await this.#save(record);
    await this.#fault('summaryBeforeDispatch',structuredClone(request));
    job=record.data.summary.jobs.find(j=>j.operationId===id);
    let response;
    try {
      const start=()=>{
        const models=record.data.models;
        if(!same(models.current.summary,request.modelChoice)||models.changes.slice(request.modelRevision).some(c=>c.intent.purpose==='summary'))fail('SUMMARY_MODEL_CHANGED');
        this.#modelControl.assertCurrent(request.modelChoice);
        this.#provider.preflight(request.operationId);
        return {response:this.#provider.dispatch(request)};
      };
      response=(await this.#summaryGuard(record,job,start)).response;
    } catch(error) {
      if(!SUMMARY_LOCAL_FAILURES.includes(error.code))throw error;
      settleSummaryJob(record.data,job,error.code==='SUMMARY_AUTHORIZATION_CHANGED'?'revoked':'not_dispatched',error.code);
      return this.#save(record);
    }
    let receipt;
    try {receipt=await response;}
    catch {
      job.state=compression?'compression_unknown':'unknown';job.diagnostic='SUMMARY_RESULT_UNKNOWN';
      return this.#save(record);
    }
    await this.#fault('summaryAfterProvider',structuredClone(request));
    record=await this.#acceptSummaryReceipt(record,id,receipt);
    await this.#fault('summaryAfterSaved',structuredClone(record.data));
    job=record.data.summary.jobs.find(j=>j.operationId===id);
    // Only the same live, explicitly started background task may use its one
    // compression phase. Recovery and restart never enter this dispatch path.
    if(job.state==='awaiting_compression')return this.#runSummary(record,id);
    return record;
  }
  async #acceptSummaryReceipt(record,id,receipt) {
    const s=record.data,job=s.summary.jobs.find(j=>j.operationId===id);
    const compression=['compression_dispatching','compression_unknown'].includes(job.state);
    const request=compression?job.compressionRequest:job.request;
    inspectSummaryResult(request,receipt);
    job[compression?'compressionReceipt':'receipt']=structuredClone(receipt);
    try {
      return await this.#summaryGuard(record,job,async authorization=>{
        const {candidate,compression:compressed}=summaryJobCandidates(job),effective=compressed??candidate;
        if(effective.status==='rejected')settleSummaryJob(s,job,'rejected',effective.reason);
        else if(candidate.status==='requires-compression'&&!compressed) {
          try {
            job.compressionRequest=createSummaryCompression(candidate,compressionJobId(id));
            if(Buffer.byteLength(canonical(s))+Buffer.byteLength(canonical(job.request))+Buffer.byteLength(canonical(job.compressionRequest))+SUMMARY_RECEIPT_RESERVE>SESSION_RECORD_LIMIT)fail('SUMMARY_RECORD_CAPACITY');
            job.state='awaiting_compression';job.diagnostic=null;
          } catch(error) {
            if(!['MODEL_CONTEXT_CAPACITY','SUMMARY_RECORD_CAPACITY'].includes(error.code))throw error;
            job.compressionRequest=null;settleSummaryJob(s,job,'blocked',error.code);
          }
        } else {
          const updated=commitSummaryUpdate(s,s.summary.snapshot,{candidate,compression:compressed,authorization});
          s.summary.snapshot=updated.state;job.appliedVersionId=updated.version.id;settleSummaryJob(s,job,'applied');
        }
        return this.#save(record);
      });
    } catch(error) {
      if(!SUMMARY_LOCAL_FAILURES.includes(error.code))throw error;
      settleSummaryJob(s,job,error.code==='SUMMARY_AUTHORIZATION_CHANGED'?'revoked':'stale',error.code);
      return this.#save(record);
    }
  }
  requestSummary(input) {
    input=summaryIntent(input);
    return this.#run(async()=>{
      this.#active.add(input.sessionId);
      try {
        let record=await this.#load(input.sessionId);const s=record.data;
        const prior=s.summary.jobs.find(j=>j.operationId===input.operationId);
        if(prior){if(prior.intentHash!==digest(input)||prior.trigger.kind!=='manual')fail('OFFLINE_OPERATION_COLLISION');return this.#summaryReply(record,prior.operationId,true);}
        assertSummaryIdFree(s,input.operationId);
        if(s.operations.some(o=>pendingStates.has(o.state)))fail('OFFLINE_RESULT_PENDING');
        if(pendingSummary(s))fail('SUMMARY_RESULT_PENDING');
        if(s.revision!==input.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');
        if(s.summary.snapshot.revision!==input.expectedSummaryRevision)fail('SUMMARY_REVISION_CONFLICT');
        if(s.models.revision!==input.expectedModelRevision)fail('MODEL_REVISION_CONFLICT');
        const authorization=await this.#historyControl.authorization(s.id);
        if(summaryContextPolicy(s,s.summary.snapshot,{authorization,historyConfig:s.history.config}).revision!==input.expectedContextPolicyRevision)fail('SUMMARY_POLICY_CONFLICT');
        const job=freezeSummaryJob(s,input,authorization,{kind:'manual',operationId:input.operationId});
        if(!job)fail('SUMMARY_ALREADY_CURRENT');
        s.summary.jobs.push(job);record=await this.#save(record);
        await this.#fault('summaryAfterPrepared',structuredClone(job));
        if(job.state==='prepared')record=await this.#runSummary(record,job.operationId);
        return this.#summaryReply(record,job.operationId);
      } finally {this.#active.delete(input.sessionId);}
    });
  }
  #recoveryInput(input) {
    input=snapshot(input,['sessionId','operationId','expectedRevision','expectedSummaryRevision']);checkSession(input.sessionId);checkOperation(input.operationId);
    if(!integer(input.expectedRevision)||!integer(input.expectedSummaryRevision))fail('SUMMARY_OPERATION_INVALID');return input;
  }
  recoverSummary(input) {
    input=this.#recoveryInput(input);
    return this.#run(async()=>{
      await this.#records.reconcile();
      let record=await this.#load(input.sessionId),s=record.data,job=s.summary.jobs.find(j=>j.operationId===input.operationId);
      if(!job)fail('OFFLINE_OPERATION_NOT_FOUND');
      if(!SUMMARY_PENDING.includes(job.state)||job.state==='awaiting_compression')return this.#summaryReply(record,job.operationId,true);
      if(s.revision!==input.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');
      if(s.summary.snapshot.revision!==input.expectedSummaryRevision)fail('SUMMARY_REVISION_CONFLICT');
      if(job.state==='prepared') {
        settleSummaryJob(s,job,'not_dispatched','SUMMARY_NOT_DISPATCHED');record=await this.#save(record);
      } else {
        const compression=job.state.startsWith('compression_');
        const receipt=await this.#provider.lookup(compression?job.compressionRequest:job.request);
        if(receipt)record=await this.#acceptSummaryReceipt(record,job.operationId,receipt);
        else if(!job.state.endsWith('unknown')) {
          job.state=compression?'compression_unknown':'unknown';job.diagnostic='SUMMARY_RESULT_UNKNOWN';record=await this.#save(record);
        }
      }
      return this.#summaryReply(record,job.operationId);
    });
  }
  continueSummary(input) {
    input=this.#recoveryInput(input);
    return this.#run(async()=>{
      this.#active.add(input.sessionId);
      try {
        let record=await this.#load(input.sessionId),s=record.data,job=s.summary.jobs.find(j=>j.operationId===input.operationId);
        if(!job)fail('OFFLINE_OPERATION_NOT_FOUND');
        if(!SUMMARY_PENDING.includes(job.state))return this.#summaryReply(record,job.operationId,true);
        if(job.state!=='awaiting_compression')fail('SUMMARY_RESULT_PENDING');
        if(s.revision!==input.expectedRevision)fail('OFFLINE_REVISION_CONFLICT');
        if(s.summary.snapshot.revision!==input.expectedSummaryRevision)fail('SUMMARY_REVISION_CONFLICT');
        record=await this.#runSummary(record,job.operationId);
        return this.#summaryReply(record,job.operationId);
      } finally {this.#active.delete(input.sessionId);}
    });
  }
  async reconcileStorage() { return this.#run(()=>this.#records.reconcile()); }
  async close() { if(this.#closed)return;this.#closed=true;await this.#queue;await this.#records.close(); }
}
