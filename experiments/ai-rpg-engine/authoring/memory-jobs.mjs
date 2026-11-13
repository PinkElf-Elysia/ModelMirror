import {PARAMETER_DISPATCH_FAILURES} from './chat-parameters.mjs';
import {summaryOriginRevision} from './branch-origin.mjs';
import {canonical,keys,isId,isHash,sha256,fail,LIMITS} from './contracts.mjs';
import {MEMORY_TASK_FORMAT,LEGACY_MEMORY_TASK_FORMAT,initialAuthoringMemory,validateAuthoringMemory,memoryAt,memoryUpdateSource,createMemoryTask,inspectMemoryResult,commitMemoryUpdate,requireMemoryAuthorization,MEMORY_STATE_LIMITS} from './memory-state.mjs';
import {memoryContextPolicy} from './memory-context.mjs';
import {summaryAt,modelsAt} from './summary-jobs.mjs';
import {historyStateAt} from './history-control.mjs';
import {stageOperationId} from './joint-plan.mjs';
export const MEMORY_JOBS_FORMAT='modelmirror.rpg.authored-memory-jobs/1';
export const MEMORY_PENDING=Object.freeze(['prepared','dispatching','unknown']);
export const MEMORY_JOB_LIMIT=1000;
export const MEMORY_RECEIPT_RESERVE=MEMORY_STATE_LIMITS.rawCharacters*18+65536;
const terminal=['applied','rejected','revoked','not_dispatched','blocked','stale'];
const preparationErrors=['MODEL_CONTEXT_CAPACITY','MEMORY_MODEL_REQUIRED','MEMORY_CAPACITY_FULL','MEMORY_STATE_CAPACITY','MEMORY_SOURCE_CAPACITY','SUMMARY_SOURCE_CAPACITY'];
export const MEMORY_LOCAL_FAILURES=Object.freeze([...PARAMETER_DISPATCH_FAILURES,'MEMORY_AUTHORIZATION_CHANGED','MEMORY_MODEL_CHANGED','MEMORY_SOURCE_CHANGED','MEMORY_REVISION_CONFLICT','MODEL_SELECTION_CHANGED','MODEL_SELECTION_UNAVAILABLE','MODEL_CAPABILITY_UNKNOWN','MODEL_CONTEXT_CAPACITY','OFFLINE_FIXTURE_MISSING']);
const digest=v=>sha256(canonical(v)),same=(a,b)=>canonical(a)===canonical(b),clone=v=>structuredClone(v),integer=v=>Number.isSafeInteger(v)&&v>=0;
const grants=v=>{keys(v,['history','summary','memory']);Object.values(v).forEach(requireMemoryAuthorization);};
export const memoryJobId=(sessionId,storyOperationId)=>stageOperationId(sessionId,storyOperationId,'memory-after');
export const pendingMemory=s=>s.memory.jobs.find(j=>MEMORY_PENDING.includes(j.state))??null;
export const memoryOwnsId=(s,id)=>s.memory.jobs.some(j=>j.operationId===id)||s.memory.snapshot.versions.some(v=>v.operationId===id);
export function assertMemoryIdFree(s,id){
 if(!isId(id)||memoryOwnsId(s,id)||s.create.operationId===id||s.operations.some(o=>o.operationId===id)||s.models.changes.some(c=>c.intent.operationId===id)||s.history.changes.some(c=>c.intent.operationId===id)||
  s.summary.jobs.some(j=>j.operationId===id||j.compressionRequest?.operationId===id)||s.summary.snapshot.versions.some(v=>v.operationId===id||v.result?.compression?.request.operationId===id))fail('OFFLINE_OPERATION_COLLISION');
}
export const initialMemoryJobs=s=>({format:MEMORY_JOBS_FORMAT,snapshot:initialAuthoringMemory(s),jobs:[]});
export function memoryIntent(input){
 canonical(input);keys(input,['sessionId','operationId','expectedRevision','expectedMemoryRevision','expectedModelRevision','expectedContextPolicyRevision']);
 if(!/^session-[a-f0-9]{64}$/.test(input.sessionId)||!isId(input.operationId)||![input.expectedRevision,input.expectedMemoryRevision,input.expectedModelRevision].every(integer)||!isHash(input.expectedContextPolicyRevision))fail('MEMORY_OPERATION_INVALID');
 return clone(input);
}
function prepare(s,state,i,authorization,format){return createMemoryTask(s,state,{operationId:i.operationId,expectedRevision:i.expectedRevision,expectedMemoryRevision:i.expectedMemoryRevision,expectedModelRevision:i.expectedModelRevision,authorization:authorization.memory},format);}
export function freezeMemoryJob(s,input,authorization,trigger){
 const i=memoryIntent(input);grants(authorization);keys(trigger,['kind','operationId']);
 if(i.sessionId!==s.id||i.expectedRevision!==s.revision)fail('MEMORY_SESSION_CONFLICT');
 if(!['manual','after-story'].includes(trigger.kind)||!isId(trigger.operationId)||(trigger.kind==='manual'&&trigger.operationId!==i.operationId))fail('MEMORY_TRIGGER_INVALID');
 assertMemoryIdFree(s,i.operationId);if(s.memory.jobs.length>=MEMORY_JOB_LIMIT)fail('MEMORY_JOB_CAPACITY');if(pendingMemory(s))fail('MEMORY_RESULT_PENDING');
 if(!authorization.memory.enabled)fail('MEMORY_AUTHORIZATION_REQUIRED');
 if(i.expectedMemoryRevision!==s.memory.snapshot.revision)fail('MEMORY_REVISION_CONFLICT');
 if(i.expectedModelRevision!==s.models.revision)fail('MODEL_REVISION_CONFLICT');
 if(memoryContextPolicy(s,s.summary.snapshot,s.memory.snapshot,{authorization,historyConfig:s.history.config}).revision!==i.expectedContextPolicyRevision)fail('MEMORY_POLICY_CONFLICT');
 if(!memoryUpdateSource(s,s.memory.snapshot).needsUpdate)return null;
 if(trigger.kind==='after-story'){
  const story=s.operations.find(o=>o.operationId===trigger.operationId);
  if(!story||story.state!=='completed'||s.turns.at(-1).operationId!==story.operationId||!same(story.request.policy.authorization,authorization)||i.operationId!==memoryJobId(s.id,story.operationId)||s.memory.jobs.some(j=>j.trigger.kind==='after-story'&&j.trigger.operationId===story.operationId))fail('MEMORY_TRIGGER_INVALID');
 }
 const j={operationId:i.operationId,intent:i,intentHash:digest(i),trigger:clone(trigger),completedTurns:s.turns.length,historyRevision:s.history.revision,summaryRevision:s.summary.snapshot.revision,
  authorization:clone(authorization),request:null,receipt:null,state:'prepared',diagnostic:null,appliedVersionId:null,settledRevision:null};
 try{j.request=prepare(s,s.memory.snapshot,i,authorization);}catch(e){if(!preparationErrors.includes(e.code))throw e;j.state='blocked';j.diagnostic=e.code;j.settledRevision=s.revision+1;}
 if(j.request&&Buffer.byteLength(canonical({...s,memory:{...s.memory,jobs:[...s.memory.jobs,j]}}))+2*Buffer.byteLength(canonical(j.request))+MEMORY_RECEIPT_RESERVE>LIMITS.recordBytes){j.request=null;j.state='blocked';j.diagnostic='MEMORY_RECORD_CAPACITY';j.settledRevision=s.revision+1;}
 if(j.request===null)j.preparationFormat=MEMORY_TASK_FORMAT;
 return j;
}
export function autoMemoryIntent(s,story,authorization){return {sessionId:s.id,operationId:memoryJobId(s.id,story.operationId),expectedRevision:s.revision,expectedMemoryRevision:s.memory.snapshot.revision,expectedModelRevision:s.models.revision,
 expectedContextPolicyRevision:memoryContextPolicy(s,s.summary.snapshot,s.memory.snapshot,{authorization,historyConfig:s.history.config}).revision};}
export function settleMemoryJob(s,j,state,diagnostic=null){j.state=state;j.diagnostic=diagnostic;j.settledRevision=s.revision+1;}
export const memoryJobCandidate=j=>j.receipt?inspectMemoryResult(j.request,j.receipt):null;
// The enclosing host owns the session CAS and the registry lock. Rebuild every
// task from its historical source; a self-consistent outer hash is insufficient.
export function validateMemoryJobs(s){
 const log=s.memory;keys(log,['format','snapshot','jobs']);
 if(log.format!==MEMORY_JOBS_FORMAT||!Array.isArray(log.jobs)||log.jobs.length>MEMORY_JOB_LIMIT)fail('MEMORY_JOURNAL_CORRUPT');
 validateAuthoringMemory(log.snapshot,s);
 const occupied=new Set([s.create.operationId,...s.operations.map(o=>o.operationId),...s.models.changes.map(c=>c.intent.operationId),...s.history.changes.map(c=>c.intent.operationId),...s.summary.jobs.flatMap(j=>[j.operationId,...(j.compressionRequest?[j.compressionRequest.operationId]:[])])]);
 const applied=new Map(),automatic=new Set();let previous=-1,pending=0;
 for(const [index,j]of log.jobs.entries()){
  keys(j,['operationId','intent','intentHash','trigger','completedTurns','historyRevision','summaryRevision','authorization','request','receipt','state','diagnostic','appliedVersionId','settledRevision',...(Object.hasOwn(j,'preparationFormat')?['preparationFormat']:[])]);
  if(Object.hasOwn(j,'preparationFormat')&&(j.request!==null||j.preparationFormat!==MEMORY_TASK_FORMAT))fail('MEMORY_JOURNAL_CORRUPT');
  const i=memoryIntent(j.intent);grants(j.authorization);keys(j.trigger,['kind','operationId']);
  if(j.operationId!==i.operationId||j.intentHash!==digest(i)||i.sessionId!==s.id||i.expectedRevision<=previous||i.expectedRevision>=s.revision||!integer(j.completedTurns)||j.completedTurns<1||j.completedTurns>s.turns.length||!j.authorization.memory.enabled||
   ![...MEMORY_PENDING,...terminal].includes(j.state)||occupied.has(j.operationId))fail('MEMORY_JOURNAL_CORRUPT');occupied.add(j.operationId);previous=i.expectedRevision;
  if(j.trigger.kind==='after-story'){
   const story=s.operations.find(o=>o.operationId===j.trigger.operationId);
   if(!story||story.state!=='completed'||!story.request.policy.authorization.memory.enabled||automatic.has(story.operationId)||s.turns[j.completedTurns-1].operationId!==story.operationId||j.operationId!==memoryJobId(s.id,story.operationId)||!same(story.request.policy.authorization,j.authorization))fail('MEMORY_JOURNAL_CORRUPT');automatic.add(story.operationId);
  }else if(j.trigger.kind!=='manual'||j.trigger.operationId!==j.operationId)fail('MEMORY_JOURNAL_CORRUPT');
  const models=modelsAt(s.models,i.expectedModelRevision),history=historyStateAt(s.history,j.historyRevision),summary=summaryAt(s.summary.snapshot,j.summaryRevision),state=memoryAt(s,log.snapshot,i.expectedMemoryRevision);
  if(s.models.changes.some(c=>(c.revision<=models.revision)!==(c.intent.expectedRevision<i.expectedRevision))||s.history.changes.some(c=>(c.revision<=history.revision)!==(c.intent.expectedRevision<i.expectedRevision)))fail('MEMORY_JOURNAL_CORRUPT');
  if(j.summaryRevision!==summaryOriginRevision(s)+s.summary.jobs.filter(v=>v.state==='applied'&&v.settledRevision<=i.expectedRevision).length)fail('MEMORY_JOURNAL_CORRUPT');
  const prior={...s,revision:i.expectedRevision,turns:s.turns.slice(0,j.completedTurns),models};
  if(memoryContextPolicy(prior,summary,state,{authorization:j.authorization,historyConfig:history.config}).revision!==i.expectedContextPolicyRevision)fail('MEMORY_JOURNAL_CORRUPT');
  let expected,error;try{expected=prepare(prior,state,i,j.authorization,j.request?.format??j.preparationFormat??LEGACY_MEMORY_TASK_FORMAT);}catch(e){error=e.code;}
  if(j.request===null){if(j.state!=='blocked'||j.receipt!==null||!(preparationErrors.includes(j.diagnostic)&&j.diagnostic===error||j.diagnostic==='MEMORY_RECORD_CAPACITY'&&!error&&expected?.kind==='memory'))fail('MEMORY_JOURNAL_CORRUPT');}
  else if(error||expected?.kind!=='memory'||!same(j.request,expected))fail('MEMORY_JOURNAL_CORRUPT');
  const candidate=memoryJobCandidate(j);
  if(MEMORY_PENDING.includes(j.state)){
   pending++;if(index!==log.jobs.length-1||j.completedTurns!==s.turns.length||j.settledRevision!==null||j.appliedVersionId!==null||j.receipt!==null||j.diagnostic!==(j.state==='unknown'?'MEMORY_RESULT_UNKNOWN':null))fail('MEMORY_JOURNAL_CORRUPT');
  }else{
   if(!integer(j.settledRevision)||j.settledRevision<=i.expectedRevision||j.settledRevision>s.revision)fail('MEMORY_JOURNAL_CORRUPT');
   if(j.state==='applied'){
    const v=log.snapshot.versions.find(v=>v.id===j.appliedVersionId);
    if(!v||v.kind!=='model'||v.operationId!==j.operationId||j.diagnostic!==null||!same(v.result,candidate)||applied.has(v.id))fail('MEMORY_JOURNAL_CORRUPT');applied.set(v.id,j);
   }else{
    if(j.appliedVersionId!==null||typeof j.diagnostic!=='string')fail('MEMORY_JOURNAL_CORRUPT');
    if(j.state==='rejected'){
     if(!candidate)fail('MEMORY_JOURNAL_CORRUPT');let rejected;try{commitMemoryUpdate(prior,state,{candidate,authorization:j.authorization.memory});}catch(e){rejected=e.code;}
     if(!rejected||rejected!==j.diagnostic)fail('MEMORY_JOURNAL_CORRUPT');
    }
    if(j.state==='revoked'&&j.diagnostic!=='MEMORY_AUTHORIZATION_CHANGED')fail('MEMORY_JOURNAL_CORRUPT');
    if(j.state==='not_dispatched'&&(j.receipt!==null||!['MEMORY_NOT_DISPATCHED',...MEMORY_LOCAL_FAILURES].includes(j.diagnostic)))fail('MEMORY_JOURNAL_CORRUPT');
    if(j.state==='stale'&&(!candidate||!MEMORY_LOCAL_FAILURES.includes(j.diagnostic)))fail('MEMORY_JOURNAL_CORRUPT');
    if(j.state==='blocked'&&j.request!==null)fail('MEMORY_JOURNAL_CORRUPT');
   }
  }
 }
 if(pending>1||applied.size!==log.snapshot.versions.filter(v=>v.kind==='model').length)fail('MEMORY_JOURNAL_CORRUPT');
 let last=-1;
 const committed=log.snapshot.versions.map(v=>{const n=v.kind==='inherited'?0:v.kind==='manual'?v.edit.expectedRevision+1:applied.get(v.id)?.settledRevision;if(!integer(n)||n<=last||n>s.revision)fail('MEMORY_JOURNAL_CORRUPT');last=n;return n;});
 for(const j of log.jobs)if(j.intent.expectedMemoryRevision!==committed.filter(n=>n<=j.intent.expectedRevision).length)fail('MEMORY_JOURNAL_CORRUPT');
 for(const t of s.turns){const story=s.operations.find(o=>o.operationId===t.operationId);if(story?.request.policy.authorization.memory.enabled&&!automatic.has(t.operationId))fail('MEMORY_JOURNAL_CORRUPT');}
 return log;
}
