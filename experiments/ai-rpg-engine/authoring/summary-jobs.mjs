import {canonical, keys, isId, isHash, sha256, fail} from './contracts.mjs';
import {modelChoiceAt} from './model-control.mjs';
import {historyStateAt} from './history-control.mjs';
import {SESSION_RECORD_LIMIT} from './session-storage.mjs';
import {initialAuthoringSummary, validateAuthoringSummary, createSummaryTask, inspectSummaryResult,
  createSummaryCompression, requireSummaryAuthorization, summaryUpdateSource, SUMMARY_LIMITS} from './summary-state.mjs';
import {summaryContextPolicy} from './summary-context.mjs';

export const SUMMARY_JOBS_FORMAT = 'modelmirror.rpg.authored-summary-jobs/1';
export const SUMMARY_JOB_LIMIT = 1000;
export const SUMMARY_PENDING = Object.freeze(['prepared','dispatching','unknown','awaiting_compression','compression_dispatching','compression_unknown']);
const terminal = ['applied','rejected','revoked','not_dispatched','blocked','stale'];
const digest = v => sha256(canonical(v)), clone = v => structuredClone(v);
const same = (a,b) => canonical(a) === canonical(b);
const integer = v => Number.isSafeInteger(v) && v >= 0;
const preparationErrors = ['MODEL_CONTEXT_CAPACITY','SUMMARY_MODEL_REQUIRED','SUMMARY_STATE_CAPACITY','SUMMARY_SOURCE_CAPACITY'];
export const SUMMARY_LOCAL_FAILURES = Object.freeze(['SUMMARY_AUTHORIZATION_CHANGED','SUMMARY_MODEL_CHANGED','SUMMARY_SOURCE_CHANGED',
  'SUMMARY_REVISION_CONFLICT','MODEL_SELECTION_CHANGED','MODEL_SELECTION_UNAVAILABLE','MODEL_CAPABILITY_UNKNOWN','MODEL_CONTEXT_CAPACITY','OFFLINE_FIXTURE_MISSING']);
// Worst-case JSON escaping and repeated raw receipts in the compression parent /
// immutable version. This is storage headroom, not tokens or billed-call quota.
export const SUMMARY_RECEIPT_RESERVE = SUMMARY_LIMITS.rawCharacters * 60 + 65536;
export const summaryJobId = (sessionId, storyOperationId) => 'm2-' + digest({sessionId,storyOperationId,phase:'after-story'});
export const compressionJobId = operationId => 'm2c-' + digest({operationId,phase:'compression'});
export const pendingSummary = s => s.summary.jobs.find(j=>SUMMARY_PENDING.includes(j.state)) ?? null;
export const summaryOwnsId = (s,id) => s.summary.jobs.some(j=>j.operationId===id || compressionJobId(j.operationId)===id);
export function assertSummaryIdFree(s,id) {
  if (!isId(id) || summaryOwnsId(s,id) || id===s.create.operationId || s.operations.some(o=>o.operationId===id) ||
    s.models.changes.some(c=>c.intent.operationId===id) || s.history.changes.some(c=>c.intent.operationId===id)) fail('OFFLINE_OPERATION_COLLISION');
}
export function initialSummaryJobs(s) { return {format:SUMMARY_JOBS_FORMAT,snapshot:initialAuthoringSummary(s),jobs:[]}; }
export function summaryAt(state,revision) {
  if(!integer(revision)||revision>state.revision)fail('SUMMARY_JOURNAL_CORRUPT');
  const versions=clone(state.versions.slice(0,revision)),{sha256:ignored,...body}=state;
  const next={...body,revision,activeVersionId:versions.at(-1)?.id??null,versions};
  return {...next,sha256:digest(next)};
}
export function modelsAt(models,revision) {
  if(!integer(revision)||revision>models.revision)fail('SUMMARY_JOURNAL_CORRUPT');
  return {revision,current:Object.fromEntries(['story','summary','memory'].map(p=>[p,modelChoiceAt(models,revision,p)])),changes:clone(models.changes.slice(0,revision))};
}
export function summaryIntent(input) {
  canonical(input);keys(input,['sessionId','operationId','expectedRevision','expectedSummaryRevision','expectedModelRevision','expectedContextPolicyRevision']);
  if(!/^session-[a-f0-9]{64}$/.test(input.sessionId)||!isId(input.operationId)||
    ![input.expectedRevision,input.expectedSummaryRevision,input.expectedModelRevision].every(integer)||!isHash(input.expectedContextPolicyRevision))fail('SUMMARY_OPERATION_INVALID');
  return clone(input);
}
function grants(value) { keys(value,['history','summary','memory']);Object.values(value).forEach(requireSummaryAuthorization); }
function prepare(s,state,intent,authorization) {
  return createSummaryTask(s,state,{operationId:intent.operationId,expectedSummaryRevision:intent.expectedSummaryRevision,
    expectedModelRevision:intent.expectedModelRevision,authorization:authorization.summary});
}
export function freezeSummaryJob(s,intent,authorization,trigger) {
  summaryIntent(intent);grants(authorization);
  assertSummaryIdFree(s,intent.operationId);assertSummaryIdFree(s,compressionJobId(intent.operationId));
  if(s.summary.jobs.length>=SUMMARY_JOB_LIMIT)fail('SUMMARY_JOB_CAPACITY');
  if(pendingSummary(s))fail('SUMMARY_RESULT_PENDING');
  if(!authorization.summary.enabled)fail('SUMMARY_AUTHORIZATION_REQUIRED');
  if(authorization.memory.enabled)fail('AUTHORING_MEMORY_EXECUTION_UNAVAILABLE');
  if(!summaryUpdateSource(s,s.summary.snapshot).needsUpdate)return null;
  const job={operationId:intent.operationId,intent:clone(intent),intentHash:digest(intent),trigger:clone(trigger),completedTurns:s.turns.length,
    historyRevision:s.history.revision,authorization:clone(authorization),request:null,receipt:null,compressionRequest:null,compressionReceipt:null,
    state:'prepared',diagnostic:null,appliedVersionId:null,settledRevision:null};
  try { job.request=prepare(s,s.summary.snapshot,intent,authorization); }
  catch(error) {
    if(!preparationErrors.includes(error.code))throw error;
    job.state='blocked';job.diagnostic=error.code;job.settledRevision=s.revision+1;
  }
  // Include request copies in the compression job and both immutable version
  // candidates, in addition to bounded raw receipts. If it cannot fit, retain
  // the completed story and a blocked job without the oversized task body.
  if(job.request && Buffer.byteLength(canonical({...s,summary:{...s.summary,jobs:[...s.summary.jobs,job]}}))+3*Buffer.byteLength(canonical(job.request))+SUMMARY_RECEIPT_RESERVE>SESSION_RECORD_LIMIT) {
    job.request=null;job.state='blocked';job.diagnostic='SUMMARY_RECORD_CAPACITY';job.settledRevision=s.revision+1;
  }
  return job;
}
export function autoSummaryIntent(s,story,authorization) {
  return {sessionId:s.id,operationId:summaryJobId(s.id,story.operationId),expectedRevision:s.revision,
    expectedSummaryRevision:s.summary.snapshot.revision,expectedModelRevision:s.models.revision,
    expectedContextPolicyRevision:summaryContextPolicy(s,s.summary.snapshot,{authorization,historyConfig:s.history.config}).revision};
}
export function settleSummaryJob(s,job,state,diagnostic=null) {
  job.state=state;job.diagnostic=diagnostic;job.settledRevision=s.revision+1;
}
export function summaryJobCandidates(job) {
  return {candidate:job.receipt?inspectSummaryResult(job.request,job.receipt):null,
    compression:job.compressionReceipt?inspectSummaryResult(job.compressionRequest,job.compressionReceipt):null};
}
// All journal records share the story/session CAS. Hash checks are complemented
// by rebuilding the actual source, model timeline, summary prefix and receipts.
export function validateSummaryJobs(s) {
  const log=s.summary;keys(log,['format','snapshot','jobs']);
  if(log.format!==SUMMARY_JOBS_FORMAT||!Array.isArray(log.jobs)||log.jobs.length>SUMMARY_JOB_LIMIT)fail('SUMMARY_JOURNAL_CORRUPT');
  validateAuthoringSummary(log.snapshot,s);
  const occupied=new Set([s.create.operationId,...s.operations.map(o=>o.operationId),...s.models.changes.map(c=>c.intent.operationId),...s.history.changes.map(c=>c.intent.operationId)]);
  const applied=[],auto=new Set();let previous=-1,pending=0;
  for(const [index,j] of log.jobs.entries()) {
    keys(j,['operationId','intent','intentHash','trigger','completedTurns','historyRevision','authorization','request','receipt','compressionRequest','compressionReceipt','state','diagnostic','appliedVersionId','settledRevision']);
    const i=summaryIntent(j.intent);grants(j.authorization);keys(j.trigger,['kind','operationId']);
    if(j.operationId!==i.operationId||j.intentHash!==digest(i)||i.sessionId!==s.id||i.expectedRevision<=previous||i.expectedRevision>=s.revision||
      !integer(j.completedTurns)||j.completedTurns<2||j.completedTurns>s.turns.length||!j.authorization.summary.enabled||j.authorization.memory.enabled||
      ![...SUMMARY_PENDING,...terminal].includes(j.state)||occupied.has(j.operationId)||occupied.has(compressionJobId(j.operationId)))fail('SUMMARY_JOURNAL_CORRUPT');
    occupied.add(j.operationId);occupied.add(compressionJobId(j.operationId));previous=i.expectedRevision;
    if(j.trigger.kind==='after-story') {
      const parent=s.operations.find(o=>o.operationId===j.trigger.operationId);
      if(!parent||parent.state!=='completed'||!parent.request.policy.authorization.summary.enabled||auto.has(parent.operationId)||
        s.turns[j.completedTurns-1].operationId!==parent.operationId||j.operationId!==summaryJobId(s.id,parent.operationId)||
        !same(j.authorization,parent.request.policy.authorization))fail('SUMMARY_JOURNAL_CORRUPT');
      auto.add(parent.operationId);
    } else if(j.trigger.kind!=='manual'||j.trigger.operationId!==j.operationId)fail('SUMMARY_JOURNAL_CORRUPT');
    const history=historyStateAt(s.history,j.historyRevision),models=modelsAt(s.models,i.expectedModelRevision);
    if(s.models.changes.some(c=>(c.revision<=models.revision)!==(c.intent.expectedRevision<i.expectedRevision))||
      s.history.changes.some(c=>(c.revision<=history.revision)!==(c.intent.expectedRevision<i.expectedRevision))||
      i.expectedSummaryRevision!==applied.filter(a=>a.settledRevision<=i.expectedRevision).length)fail('SUMMARY_JOURNAL_CORRUPT');
    const prior={...s,revision:i.expectedRevision,turns:s.turns.slice(0,j.completedTurns),models},state=summaryAt(log.snapshot,i.expectedSummaryRevision);
    if(summaryContextPolicy(prior,state,{authorization:j.authorization,historyConfig:history.config}).revision!==i.expectedContextPolicyRevision)fail('SUMMARY_JOURNAL_CORRUPT');
    let expected,preparationError;
    try {expected=prepare(prior,state,i,j.authorization);}catch(error){preparationError=error.code;}
    if(j.request===null) {
      if(j.state!=='blocked'||j.receipt!==null||j.compressionRequest!==null||j.compressionReceipt!==null||
        !(preparationErrors.includes(j.diagnostic)&&j.diagnostic===preparationError||j.diagnostic==='SUMMARY_RECORD_CAPACITY'&&!preparationError&&expected?.kind==='summary'))fail('SUMMARY_JOURNAL_CORRUPT');
    } else if(preparationError||expected?.kind!=='summary'||!same(expected,j.request))fail('SUMMARY_JOURNAL_CORRUPT');
    const {candidate,compression}=summaryJobCandidates(j);
    if(j.compressionRequest!==null&&(!candidate||candidate.status!=='requires-compression'||!same(j.compressionRequest,createSummaryCompression(candidate,compressionJobId(j.operationId)))))fail('SUMMARY_JOURNAL_CORRUPT');
    if(j.compressionReceipt!==null&&j.compressionRequest===null)fail('SUMMARY_JOURNAL_CORRUPT');
    if(SUMMARY_PENDING.includes(j.state)) {
      pending++;if(index!==log.jobs.length-1||j.completedTurns!==s.turns.length||j.settledRevision!==null||j.appliedVersionId!==null)fail('SUMMARY_JOURNAL_CORRUPT');
      const compressionPhase=j.state.startsWith('compression_')||j.state==='awaiting_compression';
      if(compressionPhase?(!j.compressionRequest||j.compressionReceipt!==null):j.receipt!==null)fail('SUMMARY_JOURNAL_CORRUPT');
      if(j.diagnostic!==(j.state.endsWith('unknown')?'SUMMARY_RESULT_UNKNOWN':null))fail('SUMMARY_JOURNAL_CORRUPT');
    } else {
      if(!integer(j.settledRevision)||j.settledRevision<=i.expectedRevision||j.settledRevision>s.revision)fail('SUMMARY_JOURNAL_CORRUPT');
      if(j.state==='applied') {
        const v=log.snapshot.versions[applied.length];
        if(!v||v.kind!=='model'||v.operationId!==j.operationId||v.id!==j.appliedVersionId||j.diagnostic!==null||
          !same(v.result,{candidate,compression}))fail('SUMMARY_JOURNAL_CORRUPT');
        applied.push(j);
      } else {
        if(j.appliedVersionId!==null||typeof j.diagnostic!=='string')fail('SUMMARY_JOURNAL_CORRUPT');
        if(j.state==='rejected'&&(!(compression??candidate)||(compression??candidate).status!=='rejected'||j.diagnostic!==(compression??candidate).reason))fail('SUMMARY_JOURNAL_CORRUPT');
        if(j.state==='revoked'&&j.diagnostic!=='SUMMARY_AUTHORIZATION_CHANGED')fail('SUMMARY_JOURNAL_CORRUPT');
        if(j.state==='not_dispatched'&&((j.compressionRequest?j.compressionReceipt:j.receipt)!==null||
          !['SUMMARY_NOT_DISPATCHED',...SUMMARY_LOCAL_FAILURES].includes(j.diagnostic)))fail('SUMMARY_JOURNAL_CORRUPT');
        if(j.state==='stale'&&!SUMMARY_LOCAL_FAILURES.includes(j.diagnostic))fail('SUMMARY_JOURNAL_CORRUPT');
        if(j.state==='blocked'&&j.request!==null&&(!candidate||candidate.status!=='requires-compression'||!['MODEL_CONTEXT_CAPACITY','SUMMARY_RECORD_CAPACITY'].includes(j.diagnostic)))fail('SUMMARY_JOURNAL_CORRUPT');
      }
    }
  }
  if(pending>1||applied.length!==log.snapshot.versions.length)fail('SUMMARY_JOURNAL_CORRUPT');
  for(const [index,turn] of s.turns.entries()) {
    const story=s.operations.find(o=>o.operationId===turn.operationId);
    if(index>0&&story?.request.policy.authorization.summary.enabled&&!auto.has(turn.operationId))fail('SUMMARY_JOURNAL_CORRUPT');
  }
  for(const o of s.operations) {
    const rev=o.request.summaryContext?.policy.summaryRevision;
    if(rev!==applied.filter(j=>j.settledRevision<=o.intent.expectedRevision).length)fail('SUMMARY_JOURNAL_CORRUPT');
  }
  return log;
}
