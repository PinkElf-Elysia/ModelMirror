import {summaryOriginRevision} from './branch-origin.mjs';
// M2 task journal for the joint host. The retained /4 journal is not changed.
import {canonical,keys,isId,sha256,fail} from './contracts.mjs';
import {SESSION_RECORD_LIMIT} from './session-storage.mjs';
import {createSummaryTask,createSummaryCompression,inspectSummaryResult,validateAuthoringSummary,summaryUpdateSource} from './summary-state.mjs';
import {initialSummaryJobs,summaryAt,modelsAt,summaryIntent,summaryJobCandidates,SUMMARY_PENDING,SUMMARY_LOCAL_FAILURES,SUMMARY_JOB_LIMIT,SUMMARY_RECEIPT_RESERVE} from './summary-jobs.mjs';
import {memoryContextPolicy} from './memory-context.mjs';
import {memoryAt,requireMemoryAuthorization} from './memory-state.mjs';
import {historyStateAt} from './history-control.mjs';
import {stageOperationId} from './joint-plan.mjs';
export {summaryAt,modelsAt,summaryIntent,summaryJobCandidates,SUMMARY_PENDING,SUMMARY_LOCAL_FAILURES,SUMMARY_JOB_LIMIT,SUMMARY_RECEIPT_RESERVE};
export const JOINT_SUMMARY_FORMAT='modelmirror.rpg.authored-summary-jobs/2';
const digest=v=>sha256(canonical(v)),same=(a,b)=>canonical(a)===canonical(b),clone=v=>structuredClone(v);
const integer=v=>Number.isSafeInteger(v)&&v>=0;
const preparationErrors=['MODEL_CONTEXT_CAPACITY','SUMMARY_MODEL_REQUIRED','SUMMARY_STATE_CAPACITY','SUMMARY_SOURCE_CAPACITY'];
const terminal=['applied','rejected','revoked','not_dispatched','blocked','stale'];
export const initialJointSummary=s=>({...initialSummaryJobs(s),format:JOINT_SUMMARY_FORMAT});
export const jointCompressionId=j=>stageOperationId(j.intent.sessionId,j.runId,j.stageId==='summary-before'?'compression-before':'compression-after');
export const pendingJointSummary=s=>s.summary.jobs.find(j=>SUMMARY_PENDING.includes(j.state))??null;
const prepare=(s,state,i,auth)=>createSummaryTask(s,state,{operationId:i.operationId,expectedSummaryRevision:i.expectedSummaryRevision,expectedModelRevision:i.expectedModelRevision,authorization:auth.summary});
export function freezeJointSummary(s,run,stageId) {
 const stage=run.plan.stages.find(t=>t.stageId===stageId);
 if(!stage||stage.purpose!=='summary'||!['summary-before','summary-after'].includes(stageId))fail('JOINT_STAGE_INVALID');
 if(pendingJointSummary(s))fail('SUMMARY_RESULT_PENDING');
 if(s.summary.jobs.length>=SUMMARY_JOB_LIMIT)fail('SUMMARY_JOB_CAPACITY');
 const authorization=run.plan.input.authorization;
 if(!authorization.summary.enabled)fail('SUMMARY_AUTHORIZATION_REQUIRED');
 if(!summaryUpdateSource(s,s.summary.snapshot).needsUpdate)return null;
 if(s.summary.jobs.some(j=>j.operationId===stage.operationId))fail('OFFLINE_OPERATION_COLLISION');
 const intent={sessionId:s.id,operationId:stage.operationId,expectedRevision:s.revision,expectedSummaryRevision:s.summary.snapshot.revision,
  expectedModelRevision:s.models.revision,expectedContextPolicyRevision:memoryContextPolicy(s,s.summary.snapshot,s.memory.snapshot,{authorization,historyConfig:s.history.config}).revision};
 const job={operationId:stage.operationId,intent,intentHash:digest(intent),runId:run.operationId,stageId,completedTurns:s.turns.length,historyRevision:s.history.revision,memoryRevision:s.memory.snapshot.revision,
  authorization:clone(authorization),request:null,receipt:null,compressionRequest:null,compressionReceipt:null,state:'prepared',diagnostic:null,appliedVersionId:null,settledRevision:null};
 try {job.request=prepare(s,s.summary.snapshot,intent,authorization);}catch(e){if(!preparationErrors.includes(e.code))throw e;job.state='blocked';job.diagnostic=e.code;job.settledRevision=s.revision+1;}
 if(job.request&&Buffer.byteLength(canonical({...s,summary:{...s.summary,jobs:[...s.summary.jobs,job]}}))+3*Buffer.byteLength(canonical(job.request))+SUMMARY_RECEIPT_RESERVE>SESSION_RECORD_LIMIT){
  job.request=null;job.state='blocked';job.diagnostic='SUMMARY_RECORD_CAPACITY';job.settledRevision=s.revision+1;
 }
 return job;
}
export function validateJointSummary(s) {
 const log=s.summary;keys(log,['format','snapshot','jobs']);
 if(log.format!==JOINT_SUMMARY_FORMAT||!Array.isArray(log.jobs)||log.jobs.length>SUMMARY_JOB_LIMIT)fail('SUMMARY_JOURNAL_CORRUPT');
 validateAuthoringSummary(log.snapshot,s);
 const ids=new Set(),applied=[];let previous=-1,pending=0;
 for(const [index,j]of log.jobs.entries()){
  keys(j,['operationId','intent','intentHash','runId','stageId','completedTurns','historyRevision','memoryRevision','authorization','request','receipt','compressionRequest','compressionReceipt','state','diagnostic','appliedVersionId','settledRevision']);
  const i=summaryIntent(j.intent);keys(j.authorization,['history','summary','memory']);Object.values(j.authorization).forEach(requireMemoryAuthorization);
  const run=s.runs.find(r=>r.operationId===j.runId),stage=run?.plan.stages.find(t=>t.stageId===j.stageId),before=j.stageId==='summary-before';
  if(!run||!['summary-before','summary-after'].includes(j.stageId)||stage?.purpose!=='summary'||j.operationId!==stage.operationId||i.operationId!==j.operationId||i.sessionId!==s.id||j.intentHash!==digest(i)||
   !same(j.authorization,run.plan.input.authorization)||!j.authorization.summary.enabled||i.expectedRevision<=previous||i.expectedRevision>=s.revision||ids.has(j.operationId)||
   !integer(j.completedTurns)||j.completedTurns!==run.plan.input.completedTurns+(before?0:1)||!s.turns[j.completedTurns-1]||![...SUMMARY_PENDING,...terminal].includes(j.state))fail('SUMMARY_JOURNAL_CORRUPT');
  ids.add(j.operationId);ids.add(jointCompressionId(j));previous=i.expectedRevision;
  if(!before&&s.turns[j.completedTurns-1].operationId!==run.operationId)fail('SUMMARY_JOURNAL_CORRUPT');
  const history=historyStateAt(s.history,j.historyRevision),models=modelsAt(s.models,i.expectedModelRevision);
  if(s.models.changes.some(c=>(c.revision<=models.revision)!==(c.intent.expectedRevision<i.expectedRevision))||s.history.changes.some(c=>(c.revision<=history.revision)!==(c.intent.expectedRevision<i.expectedRevision))||
   i.expectedSummaryRevision!==summaryOriginRevision(s)+applied.filter(v=>v.settledRevision<=i.expectedRevision).length)fail('SUMMARY_JOURNAL_CORRUPT');
  const prior={...s,revision:i.expectedRevision,models,turns:s.turns.slice(0,j.completedTurns)},state=summaryAt(log.snapshot,i.expectedSummaryRevision),memory=memoryAt(s,s.memory.snapshot,j.memoryRevision);
  if(memoryContextPolicy(prior,state,memory,{authorization:j.authorization,historyConfig:history.config}).revision!==i.expectedContextPolicyRevision)fail('SUMMARY_JOURNAL_CORRUPT');
  let expected,error;try{expected=prepare(prior,state,i,j.authorization);}catch(e){error=e.code;}
  if(j.request===null){
   if(j.state!=='blocked'||j.receipt!==null||j.compressionRequest!==null||j.compressionReceipt!==null||!(preparationErrors.includes(j.diagnostic)&&j.diagnostic===error||j.diagnostic==='SUMMARY_RECORD_CAPACITY'&&!error&&expected?.kind==='summary'))fail('SUMMARY_JOURNAL_CORRUPT');
  }else if(error||expected?.kind!=='summary'||!same(expected,j.request))fail('SUMMARY_JOURNAL_CORRUPT');
  const {candidate,compression}=summaryJobCandidates(j);
  if(j.compressionRequest!==null&&(!candidate||candidate.status!=='requires-compression'||!same(j.compressionRequest,createSummaryCompression(candidate,jointCompressionId(j)))))fail('SUMMARY_JOURNAL_CORRUPT');
  if(j.compressionReceipt!==null&&j.compressionRequest===null)fail('SUMMARY_JOURNAL_CORRUPT');
  if(SUMMARY_PENDING.includes(j.state)){
   pending++;if(index!==log.jobs.length-1||j.completedTurns!==s.turns.length||j.settledRevision!==null||j.appliedVersionId!==null)fail('SUMMARY_JOURNAL_CORRUPT');
   const compressed=j.state.startsWith('compression_')||j.state==='awaiting_compression';
   if(compressed?(!j.compressionRequest||j.compressionReceipt!==null):j.receipt!==null)fail('SUMMARY_JOURNAL_CORRUPT');
   if(j.diagnostic!==(j.state.endsWith('unknown')?'SUMMARY_RESULT_UNKNOWN':null))fail('SUMMARY_JOURNAL_CORRUPT');
  }else{
   if(!integer(j.settledRevision)||j.settledRevision<=i.expectedRevision||j.settledRevision>s.revision)fail('SUMMARY_JOURNAL_CORRUPT');
   if(j.state==='applied'){
    const v=log.snapshot.versions[summaryOriginRevision(s)+applied.length];
    if(!v||v.operationId!==j.operationId||v.id!==j.appliedVersionId||j.diagnostic!==null||!same(v.result,{candidate,compression}))fail('SUMMARY_JOURNAL_CORRUPT');applied.push(j);
   }else{
    if(j.appliedVersionId!==null||typeof j.diagnostic!=='string')fail('SUMMARY_JOURNAL_CORRUPT');
    if(j.state==='rejected'&&(!(compression??candidate)||(compression??candidate).status!=='rejected'||j.diagnostic!==(compression??candidate).reason))fail('SUMMARY_JOURNAL_CORRUPT');
    if(j.state==='revoked'&&j.diagnostic!=='SUMMARY_AUTHORIZATION_CHANGED')fail('SUMMARY_JOURNAL_CORRUPT');
    if(j.state==='not_dispatched'&&((j.compressionRequest?j.compressionReceipt:j.receipt)!==null||!['SUMMARY_NOT_DISPATCHED',...SUMMARY_LOCAL_FAILURES].includes(j.diagnostic)))fail('SUMMARY_JOURNAL_CORRUPT');
    if(j.state==='stale'&&!SUMMARY_LOCAL_FAILURES.includes(j.diagnostic))fail('SUMMARY_JOURNAL_CORRUPT');
    if(j.state==='blocked'&&j.request!==null&&(!candidate||candidate.status!=='requires-compression'||!['MODEL_CONTEXT_CAPACITY','SUMMARY_RECORD_CAPACITY'].includes(j.diagnostic)))fail('SUMMARY_JOURNAL_CORRUPT');
   }
  }
 }
 if(pending>1||applied.length+summaryOriginRevision(s)!==log.snapshot.versions.length)fail('SUMMARY_JOURNAL_CORRUPT');
 for(const run of s.runs){
  const story=s.operations.find(o=>o.operationId===run.operationId);
  if(story?.state==='completed'&&run.plan.stages.some(t=>t.stageId==='summary-after')&&!log.jobs.some(j=>j.runId===run.operationId&&j.stageId==='summary-after'))fail('SUMMARY_JOURNAL_CORRUPT');
 }
 return log;
}
