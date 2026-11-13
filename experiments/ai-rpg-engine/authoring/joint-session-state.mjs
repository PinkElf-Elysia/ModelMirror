import {validateFutureConfigurations} from './comparison-future.mjs';
import {validatePlan as validateComparisonPlan,validateContext} from './comparison-contract.mjs';
import {panelTurn,validatePanelTurns} from './variable-panel.mjs';
import {validateRequirements,requirementsAt} from './requirements.mjs';
import {CONTROLLED_MODE,CONTROLLED_SESSION_FORMAT,CONTROLLED_BRANCH_FORMAT,isControlledSession,validateControlledReceipt} from './controlled-receipt.mjs';
import {checkSessionOrigin,originTurns,summaryOriginRevision,memoryOriginRevision} from './branch-origin.mjs';
import {canonical,keys,isId,isHash,sha256,fail} from './contracts.mjs';
import {validateModelState,modelChoiceAt,modelCapacity} from './model-control.mjs';
import {validateHistoryState,historyStateAt} from './history-control.mjs';
import {summaryRawHash} from './summary-state.mjs';
import {validateChatParameters,validateRunParameters,taskModelChoice} from './chat-parameters.mjs';
import {summaryAt,modelsAt,validateJointSummary,pendingJointSummary} from './joint-summary.mjs';
import {memoryAt} from './memory-state.mjs';
import {memoryContextPolicy,replayMemoryContext} from './memory-context.mjs';
import {validateMemoryJobs,pendingMemory,memoryOwnsId} from './memory-jobs.mjs';
import {validateJointPlan,planJointCalls} from './joint-plan.mjs';
import {replayWorldbookPlan} from './worldbook.mjs';
export const BRANCH_SESSION_FORMAT='modelmirror.rpg.offline-session/6';
export const JOINT_SESSION_FORMAT='modelmirror.rpg.offline-session/5';
export const JOINT_MODE='offline-fixture-joint';
export const JOINT_LIMITS=Object.freeze({runs:1000,replyCharacters:200000,messageCharacters:2000000,
 worldbook:{scanChars:2000000,injectedChars:1000000,systemChars:1000000,userChars:1000000}});
export const digest=v=>sha256(canonical(v)),same=(a,b)=>canonical(a)===canonical(b),clone=v=>structuredClone(v);
export const integer=n=>Number.isSafeInteger(n)&&n>=0;
export const raw=v=>typeof v==='string'&&v.isWellFormed();
export const jointSessionId=id=>'session-'+sha256('joint-session:'+id);
export const jointRequestId=(id,operationId)=>'request-'+digest({sessionId:id,operationId});
export const jointGroupId=(allowanceId,plan)=>'group-'+digest({allowanceId,sessionId:plan.input.sessionId,operationId:plan.input.operationId});
export const STORY_PENDING=Object.freeze(['prepared','dispatching','unknown']);
export const RUN_PENDING=Object.freeze(['prepared','running','unknown']);
export const runPending=s=>s.runs.find(r=>RUN_PENDING.includes(r.state))??null;
export const outcomeState=r=>r.status==='failed'?'failed':r.finishReason==='length'?'incomplete':!r.text.trim()?'invalid':'completed';
export function checkSession(id){if(typeof id!=='string'||!/^session-[a-f0-9]{64}$/.test(id))fail('OFFLINE_SESSION_INVALID');}
export function jointIntent(input){
 canonical(input);keys(input,['sessionId','operationId','expectedRevision','expectedModelRevision','expectedContextPolicyRevision','expectedSummaryRevision','expectedMemoryRevision','kind','input',...(Object.hasOwn(input,'expectedParameterAuthorizationRevision')?['expectedParameterAuthorizationRevision']:[])]);
 checkSession(input.sessionId);
 if(Object.hasOwn(input,'expectedParameterAuthorizationRevision')&&!isHash(input.expectedParameterAuthorizationRevision))fail('JOINT_INTENT_INVALID');
 if(!isId(input.operationId)||!integer(input.expectedRevision)||!integer(input.expectedModelRevision)||!integer(input.expectedSummaryRevision)||!integer(input.expectedMemoryRevision)||!isHash(input.expectedContextPolicyRevision)||!['send','update'].includes(input.kind)||
  (input.kind==='send'?(!raw(input.input)||!input.input.trim()||[...input.input].length>1000000):input.input!==null))fail('JOINT_INTENT_INVALID');
 return clone(input);
}
export function storyReceipt(receipt,request){
 canonical(receipt);const controlled=receipt.kind==='control-plane';
 if(controlled)validateControlledReceipt(receipt,request);else keys(receipt,['kind','requestId','requestHash','result','sha256']);keys(receipt.result,['status','text','finishReason']);
 const {sha256:hash,...body}=receipt,r=receipt.result;
 if((!controlled&&receipt.kind!=='offline-fixture')||receipt.requestId!==request.id||receipt.requestHash!==digest(request)||hash!==digest(body)||!['completed','failed'].includes(r.status)||
  !raw(r.text)||[...r.text].length>JOINT_LIMITS.replyCharacters||(r.status==='completed'?!['stop','length'].includes(r.finishReason):r.finishReason!==(controlled?'control_failure':'fixture_failure')))fail('OFFLINE_RECEIPT_MISMATCH');
 return receipt;
}
export const turnValue=(op,number)=>({id:'turn-'+number,number,operationId:op.operationId,requestId:op.request.id,complete:true,user:op.request.rawInput,
 userMessage:op.request.memoryContext.variablePanelSnapshot||op.request.memoryContext.requirementsSnapshot?.text?op.request.worldbook.assembly.user:op.request.messages.at(-1).content,assistant:op.receipt.result.text,rawHash:sha256(op.receipt.result.text),messagesHash:op.request.messagesHash,...panelTurn(op)});
export function jointPlan(s,operationId,kind,authorization){
 return planJointCalls({sessionId:s.id,operationId,kind,completedTurns:s.turns.length,summaryCoveredThrough:s.summary.snapshot.versions.at(-1)?.coveredThrough??0,
  memoryProcessedThrough:s.memory.snapshot.processedThrough,authorization,sourceHash:summaryRawHash(s.turns),
  policyRevision:memoryContextPolicy(s,s.summary.snapshot,s.memory.snapshot,{authorization,historyConfig:s.history.config}).revision});
}
export function assertJointIdFree(s,id){
 if(!isId(id)||id===s.create.operationId||s.runs.some(r=>r.operationId===id||r.plan.stages.some(t=>t.operationId===id))||
  (s.futureConfigurations??[]).some(c=>c.intent.operationId===id)||(s.generationParameters?.changes??[]).some(c=>c.intent.operationId===id)||s.models.changes.some(c=>c.intent.operationId===id)||(s.requirements?.changes??[]).some(c=>c.intent.operationId===id)||s.history.changes.some(c=>c.intent.operationId===id)||memoryOwnsId(s,id))fail('OFFLINE_OPERATION_COLLISION');
}
export function pauseReason(s,auth){
 for(const [purpose,log]of [['summary',s.summary],['memory',s.memory]]){
  const job=log.jobs.at(-1);
  if(auth[purpose].enabled&&job&&['rejected','revoked','not_dispatched','blocked','stale'].includes(job.state)&&
   same(job.authorization[purpose],auth[purpose]))return purpose==='summary'?'SUMMARY_UPDATE_FAILED':'MEMORY_UPDATE_FAILED';
 }
 return null;
}
function revisionsAt(s,revision){
 const summary=summaryOriginRevision(s)+s.summary.jobs.filter(j=>j.state==='applied'&&j.settledRevision<=revision).length;
 const memory=memoryOriginRevision(s)+s.memory.snapshot.versions.filter(v=>v.kind==='manual'?v.edit.expectedRevision<revision:s.memory.jobs.some(j=>j.appliedVersionId===v.id&&j.state==='applied'&&j.settledRevision<=revision)).length;
 return {summary,memory};
}
export function validateJointSession(s,executionHash){
 canonical(s);keys(s,['format','id','revision','createdAt','updatedAt','create','binding','executionHash','allowanceId','presetId','examples','draft','runs','operations','turns','models','history','summary','memory','memoryEdits',...(Object.hasOwn(s,'futureConfigurations')?['futureConfigurations']:[]),...(Object.hasOwn(s,'generationParameters')?['generationParameters']:[]),...(Object.hasOwn(s,'requirements')?['requirements']:[]),...(Object.hasOwn(s,'origin')?['origin']:[]),...(Object.hasOwn(s,'variablePanel')?['variablePanel']:[])]);
 keys(s.create,['operationId','requestHash']);checkSession(s.id);
 const origin=checkSessionOrigin(s),controlled=isControlledSession(s);
 const format=controlled?(origin?CONTROLLED_BRANCH_FORMAT:CONTROLLED_SESSION_FORMAT):(origin?BRANCH_SESSION_FORMAT:JOINT_SESSION_FORMAT);
 if(s.format!==format||s.id!==jointSessionId(s.create.operationId)||!isId(s.create.operationId)||!isHash(s.create.requestHash)||!isId(s.allowanceId)||
  !integer(s.revision)||s.executionHash!==executionHash||![s.createdAt,s.updatedAt,s.draft].every(raw)||!(s.presetId===null||isId(s.presetId))||
  !Array.isArray(s.runs)||s.runs.length>JOINT_LIMITS.runs||!Array.isArray(s.operations)||s.operations.length>s.runs.length||!Array.isArray(s.turns)||
  !Array.isArray(s.memoryEdits)||!Array.isArray(s.examples)||s.examples.length>200||s.examples.length%2)fail('JOINT_SESSION_CORRUPT');
 s.examples.forEach((m,i)=>{keys(m,['role','content']);if(m.role!==(i%2?'assistant':'user')||!raw(m.content))fail('JOINT_SESSION_CORRUPT');});
 validateRequirements(s.requirements,s.id,s.revision,origin?.inheritance.settings.requirements);
 validateModelState(s.models,s.id,s.revision,s.binding.resourceHash);validateChatParameters(s);validateFutureConfigurations(s);validateHistoryState(s.history,s.id,s.revision,s.binding.resourceHash,origin?.inheritance.settings.history??null);
 const ids=new Set([s.create.operationId]),configIds=[...(s.futureConfigurations??[]),...(s.generationParameters?.changes??[]),...s.models.changes,...s.history.changes,...(s.requirements?.changes??[])].map(c=>c.intent.operationId);
 for(const id of configIds){if(ids.has(id))fail('OFFLINE_OPERATION_COLLISION');ids.add(id);}
 let previous=-1,pending=0;
 for(const [index,r]of s.runs.entries()){
  keys(r,['operationId','intent','intentHash','plan','groupId','historyRevision','summaryRevision','memoryRevision','state','diagnostic','settledRevision',...(s.generationParameters?['parameters']:[]),...(r.comparison?['comparison']:[])]);
  validateRunParameters(s,r);const i=jointIntent(r.intent);validateJointPlan(r.plan);
  if(r.comparison){const c=r.comparison;keys(c,['plan','choice','context','commits']);validateComparisonPlan(c.plan);if(c.plan.intent.sessionId!==s.id||c.plan.intent.operationId!==r.operationId||c.plan.intent.input!==r.intent.input||!c.plan.intent.candidates.some(x=>same(x.choice,c.choice))||!Array.isArray(c.commits)||c.commits.length>12)fail('COMPARISON_HOST_RECORD_INVALID');if(c.context)validateContext(c.context);const ids=new Set();for(const p of c.commits){keys(p,['operationId','payloadHash','contextRevision','hash']);const {hash,...body}=p;if(!isId(p.operationId)||ids.has(p.operationId)||!isHash(p.payloadHash)||!integer(p.contextRevision)||p.contextRevision>s.revision||hash!==digest(body))fail('COMPARISON_HOST_RECORD_INVALID');ids.add(p.operationId);}}

  if(r.operationId!==i.operationId||ids.has(r.operationId)||r.intentHash!==digest(i)||i.sessionId!==s.id||i.expectedRevision<=previous||i.expectedRevision>=s.revision||
   r.groupId!==jointGroupId(s.allowanceId,r.plan)||!['prepared','running','unknown','completed','paused','not_dispatched'].includes(r.state)||!(r.diagnostic===null||/^[A-Z0-9_]{1,100}$/.test(r.diagnostic)))fail('JOINT_RUN_CORRUPT');
  const at=revisionsAt(s,i.expectedRevision);
  if(r.summaryRevision!==at.summary||r.memoryRevision!==at.memory||i.expectedSummaryRevision!==at.summary||i.expectedMemoryRevision!==at.memory)fail('JOINT_RUN_CORRUPT');
  previous=i.expectedRevision;ids.add(r.operationId);
  for(const t of r.plan.stages){if(t.operationId===r.operationId&&t.stageId==='story')continue;if(ids.has(t.operationId))fail('OFFLINE_OPERATION_COLLISION');ids.add(t.operationId);}
  const models=modelsAt(s.models,i.expectedModelRevision),history=historyStateAt(s.history,r.historyRevision),summary=summaryAt(s.summary.snapshot,r.summaryRevision),memory=memoryAt(s,s.memory.snapshot,r.memoryRevision);
  if(s.models.changes.some(c=>(c.revision<=models.revision)!==(c.intent.expectedRevision<i.expectedRevision))||s.history.changes.some(c=>(c.revision<=history.revision)!==(c.intent.expectedRevision<i.expectedRevision)))fail('JOINT_RUN_CORRUPT');
  const turns=s.turns.filter(t=>t.number<=(origin?.turn??0)||s.operations.find(o=>o.operationId===t.operationId)?.intent.expectedRevision<i.expectedRevision);
  const prior={...s,...(s.requirements?{requirements:requirementsAt(s.requirements,i.expectedRevision)}:{}),revision:i.expectedRevision,models,history:{...history,changes:s.history.changes.slice(0,history.revision)},turns,summary:{...s.summary,snapshot:summary},memory:{...s.memory,snapshot:memory}};
  if(!same(r.plan,jointPlan(prior,r.operationId,i.kind,r.plan.input.authorization))||r.plan.input.policyRevision!==i.expectedContextPolicyRevision)fail('JOINT_RUN_CORRUPT');
  if(RUN_PENDING.includes(r.state)){pending++;if(index!==s.runs.length-1||r.settledRevision!==null)fail('JOINT_RUN_CORRUPT');}
  else if(!integer(r.settledRevision)||r.settledRevision<=i.expectedRevision||r.settledRevision>s.revision)fail('JOINT_RUN_CORRUPT');
 }
 if(pending>1)fail('JOINT_RUN_CORRUPT');
 const turns=origin?originTurns(origin):[];let storyPending=0;
 for(const op of s.operations){
  keys(op,['operationId','intent','intentHash','request','requestHash','state','receipt','diagnostic']);
  keys(op.intent,['sessionId','operationId','expectedRevision','expectedModelRevision','expectedContextPolicyRevision','input']);
  const i=op.intent,r=op.request,run=s.runs.find(x=>x.operationId===op.operationId);
  if(!run||run.intent.kind!=='send'||i.operationId!==op.operationId||i.sessionId!==s.id||!integer(i.expectedRevision)||i.expectedRevision>=s.revision||
   i.expectedRevision<=run.intent.expectedRevision||i.input!==run.intent.input||op.intentHash!==digest(i)||!['prepared','dispatching','unknown','completed','incomplete','invalid','failed','not_dispatched'].includes(op.state))fail('JOINT_STORY_CORRUPT');
  keys(r,['id','operationId','sessionId','bindingHash','executionHash','mode','model','policy','createdAt','rawInput','worldbook','draws','messages','messagesHash','memoryContext','modelConfiguration','parameters','capacity']);
  if(r.id!==jointRequestId(s.id,op.operationId)||r.operationId!==op.operationId||r.sessionId!==s.id||r.bindingHash!==digest(s.binding)||r.executionHash!==executionHash||
   r.mode!==(controlled?CONTROLLED_MODE:JOINT_MODE)||r.rawInput!==i.input||r.messagesHash!==digest(r.messages)||op.requestHash!==digest(r)||!raw(r.createdAt)||!same(r.policy.authorization,run.plan.input.authorization))fail('JOINT_STORY_CORRUPT');
  const ctx=r.memoryContext,choice=taskModelChoice(s,i.expectedModelRevision,'story',op.operationId),at=revisionsAt(s,i.expectedRevision);
  if(ctx.summarySnapshot.revision!==at.summary||ctx.memorySnapshot.revision!==at.memory||
   s.models.changes.some(c=>(c.revision<=i.expectedModelRevision)!==(c.intent.expectedRevision<i.expectedRevision)))fail('JOINT_STORY_CORRUPT');
  keys(r.modelConfiguration,['revision','choice']);
  const historyRevision=s.history.changes.filter(c=>c.intent.expectedRevision<i.expectedRevision).length;
  if(!choice||!same(r.modelConfiguration,{revision:i.expectedModelRevision,choice})||r.model!==choice.entry.modelId||!same(r.parameters,choice.wireParameters)||!same(r.capacity,modelCapacity(choice,r.messages))||
   ctx.sessionRevision!==i.expectedRevision||ctx.completedTurns!==turns.length||!same(ctx.models,modelsAt(s.models,i.expectedModelRevision))||
   !same(ctx.summarySnapshot,summaryAt(s.summary.snapshot,ctx.summarySnapshot.revision))||!same(ctx.memorySnapshot,memoryAt(s,s.memory.snapshot,ctx.memorySnapshot.revision))||
   !same(ctx.policy,r.policy)||r.policy.revision!==i.expectedContextPolicyRevision||!same(r.policy.historyConfig,historyStateAt(s.history,historyRevision).config))fail('JOINT_STORY_CORRUPT');
  const p=replayWorldbookPlan(r.worldbook,{operationId:op.operationId,revision:i.expectedRevision,contextHash:r.worldbook.contextHash});
  if(!same(r.messages,replayMemoryContext({...s,turns},{system:p.assembly.system,user:p.assembly.user},r.rawInput,ctx)))fail('JOINT_STORY_CORRUPT');
  if(op.receipt!==null)storyReceipt(op.receipt,r);
  if(STORY_PENDING.includes(op.state)){storyPending++;if(op!==s.operations.at(-1)||op.receipt!==null)fail('JOINT_STORY_CORRUPT');}
  else if(op.state==='not_dispatched'){if(op.receipt!==null)fail('JOINT_STORY_CORRUPT');}
  else {if(!op.receipt||outcomeState(op.receipt.result)!==op.state)fail('JOINT_STORY_CORRUPT');if(op.state==='completed')turns.push(turnValue(op,turns.length+1));}
 }
 if(storyPending>1||!same(turns,s.turns))fail('JOINT_STORY_CORRUPT');
 validatePanelTurns(s);validateJointSummary(s);validateMemoryJobs(s);
 const manual=s.memory.snapshot.versions.filter(v=>v.kind==='manual');
 if(s.memoryEdits.length!==manual.length)fail('JOINT_MEMORY_EDITS_CORRUPT');
 for(const [index,i]of s.memoryEdits.entries()){
  keys(i,['sessionId','operationId','expectedRevision','expectedMemoryRevision','expectedContextPolicyRevision','change']);const v=manual[index];
  if(i.sessionId!==s.id||!isHash(i.expectedContextPolicyRevision)||i.operationId!==v.operationId||
   !same(v.edit,{expectedRevision:i.expectedRevision,expectedMemoryRevision:i.expectedMemoryRevision,change:i.change}))fail('JOINT_MEMORY_EDITS_CORRUPT');
  if(ids.has(v.operationId))fail('OFFLINE_OPERATION_COLLISION');ids.add(v.operationId);
 }
 if(storyPending&&(pendingJointSummary(s)||pendingMemory(s)))fail('JOINT_PENDING_CORRUPT');
 if((storyPending||pendingJointSummary(s)||pendingMemory(s))&&!runPending(s))fail('JOINT_PENDING_CORRUPT');
 for(const r of s.runs)if(r.state==='completed'){
  if(r.intent.kind==='send'&&s.operations.find(o=>o.operationId===r.operationId)?.state!=='completed')fail('JOINT_RUN_CORRUPT');
  if(s.summary.jobs.some(j=>j.runId===r.operationId&&j.state!=='applied'))fail('JOINT_RUN_CORRUPT');
  const memoryStage=r.plan.stages.find(t=>t.purpose==='memory');
  if(memoryStage&&s.memory.jobs.find(j=>j.operationId===memoryStage.operationId)?.state!=='applied')fail('JOINT_RUN_CORRUPT');
 }
 const receipts=[...s.operations.map(o=>o.receipt),...s.summary.jobs.flatMap(j=>[j.receipt,j.compressionReceipt]),...s.memory.jobs.map(j=>j.receipt)].filter(Boolean);
 if(receipts.some(r=>r.kind!==(controlled?'control-plane':'offline-fixture')))fail('JOINT_RECEIPT_ORIGIN_MISMATCH');
 return s;
}
