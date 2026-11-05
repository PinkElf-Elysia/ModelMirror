import {requirementsAt} from './requirements.mjs';
import {summaryOriginRevision,memoryOriginRevision,branchOrigin,validateBranchOrigin} from './branch-origin.mjs';
import {canonical,keys,isId,isHash,sha256,fail} from './contracts.mjs';
import {SESSION_RECORD_LIMIT} from './session-storage.mjs';
import {validateJointSession,runPending,assertJointIdFree,checkSession} from './joint-session-state.mjs';
import {summaryAt,modelsAt} from './joint-summary.mjs';
import {historyStateAt} from './history-control.mjs';
import {memoryAt} from './memory-state.mjs';
import {memoryContextPolicy} from './memory-context.mjs';
import {summaryRawHash} from './summary-state.mjs';

export const INHERITED_CHECKPOINT_FORMAT='modelmirror.rpg.authored-branch-checkpoint/2';
export const BRANCH_CHECKPOINT_FORMAT='modelmirror.rpg.authored-branch-checkpoint/1';
const digest=v=>sha256(canonical(v)),same=(a,b)=>canonical(a)===canonical(b),clone=v=>structuredClone(v);
const integer=n=>Number.isSafeInteger(n)&&n>=0;
export const branchCheckpointId=(sessionId,operationId)=>'branch-'+digest({sessionId,operationId});
export function branchIntent(input){
 canonical(input);keys(input,['sessionId','operationId','expectedRevision','expectedSummaryRevision','expectedMemoryRevision','expectedContextPolicyRevision','turn','name']);
 checkSession(input.sessionId);
 if(!isId(input.operationId)||![input.expectedRevision,input.expectedSummaryRevision,input.expectedMemoryRevision].every(integer)||
  !isHash(input.expectedContextPolicyRevision)||!Number.isSafeInteger(input.turn)||input.turn<1||input.turn>1000||
  typeof input.name!=='string'||!input.name.isWellFormed()||!input.name.trim()||input.name!==input.name.trim()||[...input.name].length>80)fail('BRANCH_INTENT_INVALID');
 return clone(input);
}
// A settlement boundary, not the parent's state when the branch is requested.
// Later edits, model switches and completed/unknown runs never enter this prefix.
function sealedPrefix(parent,turn){
 const selected=parent.turns[turn-1],run=parent.runs.find(r=>r.operationId===selected?.operationId);
 if(!selected||!run||!['completed','paused'].includes(run.state)||!integer(run.settledRevision))fail('BRANCH_POINT_UNSETTLED');
 const revision=run.settledRevision,at={...clone(parent),revision,draft:'',updatedAt:parent.operations.find(o=>o.operationId===run.operationId).request.createdAt};
 at.runs=at.runs.filter(r=>integer(r.settledRevision)&&r.settledRevision<=revision);
 if(parent.requirements)at.requirements=requirementsAt(parent.requirements,revision);
 const ids=new Set(at.runs.map(r=>r.operationId));at.operations=at.operations.filter(o=>ids.has(o.operationId));at.turns=at.turns.slice(0,turn);
 at.models=modelsAt(parent.models,parent.models.changes.filter(c=>c.intent.expectedRevision<revision).length);
 const historyRevision=parent.history.changes.filter(c=>c.intent.expectedRevision<revision).length;
 at.history={...historyStateAt(parent.history,historyRevision),changes:clone(parent.history.changes.slice(0,historyRevision))};
 at.summary.jobs=at.summary.jobs.filter(j=>integer(j.settledRevision)&&j.settledRevision<=revision);
 at.summary.snapshot=summaryAt(parent.summary.snapshot,summaryOriginRevision(parent)+at.summary.jobs.filter(j=>j.state==='applied').length);
 at.memory.jobs=at.memory.jobs.filter(j=>integer(j.settledRevision)&&j.settledRevision<=revision);
 const applied=new Set(at.memory.jobs.filter(j=>j.state==='applied').map(j=>j.appliedVersionId));
 const memoryRevision=memoryOriginRevision(parent)+parent.memory.snapshot.versions.filter(v=>v.kind==='manual'?v.edit.expectedRevision<revision:applied.has(v.id)).length;
 at.memory.snapshot=memoryAt(parent,parent.memory.snapshot,memoryRevision);
 at.memoryEdits=at.memoryEdits.filter(i=>i.expectedRevision<revision);
 validateJointSession(at,at.executionHash);return at;
}
function dispatchSnapshot(s){
 const op=s.operations.find(o=>o.operationId===s.turns.at(-1).operationId),r=op.request,c=r.memoryContext;
 return {requestId:r.id,messagesHash:r.messagesHash,...(c.requirementsSnapshot?{requirements:clone(c.requirementsSnapshot)}:{}),summary:clone(c.summarySnapshot),memory:clone(c.memorySnapshot),recall:clone(c.recall)};
}
// This projection is the only material intended for a child. Audit requests,
// plugin receipts, executable jobs and grants stay exclusively in the archive.
function inheritedState(s){
 const summary=s.summary.snapshot.versions.at(-1),memory=s.memory.snapshot;
 const {sessionId:ignored,...binding}=s.binding;
 return {binding:clone(binding),allowanceId:s.allowanceId,presetId:s.presetId,examples:clone(s.examples),
  turns:s.turns.map(({id,number,user,userMessage,assistant,rawHash,messagesHash,variablePanel})=>({id,number,user,userMessage,assistant,rawHash,messagesHash,...(variablePanel?{variablePanel:clone(variablePanel)}:{})})),
  settings:{history:clone(s.history.config),modelSuggestions:clone(s.models.current),...(s.requirements?{requirements:s.requirements.text}:{})},
  summary:{text:summary?.effectiveText??null,coveredThrough:summary?.coveredThrough??0,sourceHash:summaryRawHash(s.turns.slice(0,summary?.coveredThrough??0)),versionId:summary?.id??null},
  memory:{entries:clone(memory.entries),processedThrough:memory.processedThrough,sourceHash:summaryRawHash(s.turns.slice(0,memory.processedThrough)),versionId:memory.versions.at(-1)?.id??null}};
}
function bounded(checkpoint){if(Buffer.byteLength(canonical(checkpoint))>SESSION_RECORD_LIMIT-1024)fail('BRANCH_CHECKPOINT_CAPACITY');return checkpoint;}
function checkParent(parent,input,authorization){
 validateJointSession(parent,parent.executionHash);const i=branchIntent(input);
 if(i.sessionId!==parent.id||i.expectedRevision!==parent.revision)fail('BRANCH_SESSION_CONFLICT');
 if(i.expectedSummaryRevision!==parent.summary.snapshot.revision||i.expectedMemoryRevision!==parent.memory.snapshot.revision)fail('BRANCH_MEMORY_CONFLICT');
 const policy=memoryContextPolicy(parent,parent.summary.snapshot,parent.memory.snapshot,{authorization,historyConfig:parent.history.config});
 if(i.expectedContextPolicyRevision!==policy.revision)fail('BRANCH_POLICY_CONFLICT');
 // An unresolved request must be settled under its original ID, not escaped
 // by creating another route. No snapshot creation dispatches or refunds it.
 if(runPending(parent))fail('BRANCH_RESULT_PENDING');
 assertJointIdFree(parent,i.operationId);return i;
}
export function createBranchCheckpoint(parent,input,authorization){
 const i=checkParent(parent,input,authorization);
 const archive=sealedPrefix(parent,i.turn),body={format:BRANCH_CHECKPOINT_FORMAT,id:branchCheckpointId(parent.id,i.operationId),intent:i,
  boundary:{sessionId:parent.id,turn:i.turn,settledRevision:archive.revision,rawHash:summaryRawHash(archive.turns)},archive,
  dispatch:dispatchSnapshot(archive),inheritance:inheritedState(archive)};
 return bounded({...body,sha256:digest(body)});
}
export function createInheritedBranchCheckpoint(parent,input,authorization,sources){
 const i=checkParent(parent,input,authorization),origin=parent.origin;
 if(!origin||i.turn>origin.turn||!Array.isArray(sources)||!sources.length)fail('BRANCH_LINEAGE_INVALID');
 const archive=sealedPrefix(sources.at(-1).archive,i.turn),body={format:INHERITED_CHECKPOINT_FORMAT,id:branchCheckpointId(parent.id,i.operationId),intent:i,
  boundary:{sessionId:parent.id,sourceSessionId:archive.id,turn:i.turn,settledRevision:archive.revision,rawHash:summaryRawHash(archive.turns)},archive,
  dispatch:dispatchSnapshot(archive),inheritance:inheritedState(archive),parentOrigin:clone(origin),lineage:sources.map(c=>({id:c.id,sha256:c.sha256}))};
 const value=bounded({...body,sha256:digest(body)});validateBranchCheckpoint(value);validateBranchLineage(value,sources);return value;
}
export function validateBranchCheckpoint(checkpoint){
 const inherited=checkpoint?.format===INHERITED_CHECKPOINT_FORMAT;
 canonical(checkpoint);keys(checkpoint,['format','id','intent','boundary','archive','dispatch','inheritance','sha256',...(inherited?['parentOrigin','lineage']:[])]);bounded(checkpoint);
 const {sha256:hash,...body}=checkpoint,i=branchIntent(checkpoint.intent),s=checkpoint.archive;
 if(![BRANCH_CHECKPOINT_FORMAT,INHERITED_CHECKPOINT_FORMAT].includes(checkpoint.format)||hash!==digest(body)||checkpoint.id!==branchCheckpointId(i.sessionId,i.operationId))fail('BRANCH_CHECKPOINT_INVALID');
 validateJointSession(s,s.executionHash);
 if(!inherited){assertJointIdFree(s,i.operationId);if(s.id!==i.sessionId||s.revision>i.expectedRevision||s.summary.snapshot.revision>i.expectedSummaryRevision||s.memory.snapshot.revision>i.expectedMemoryRevision)fail('BRANCH_CHECKPOINT_INVALID');}
 else{
  const origin=validateBranchOrigin(checkpoint.parentOrigin),lineage=checkpoint.lineage;
  if(!Array.isArray(lineage)||lineage.length<1||lineage.length>32||i.turn>origin.turn||!same(lineage[0],{id:origin.checkpointId,sha256:origin.checkpointHash})||
   new Set(lineage.map(r=>r.id)).size!==lineage.length||lineage.some(r=>r.id===checkpoint.id))fail('BRANCH_LINEAGE_INVALID');
  for(const r of lineage){keys(r,['id','sha256']);if(!/^branch-[a-f0-9]{64}$/.test(r.id)||!isHash(r.sha256))fail('BRANCH_LINEAGE_INVALID');}
 }
 if(s.turns.length!==i.turn||!same(s,sealedPrefix(s,i.turn))||!same(checkpoint.boundary,{sessionId:i.sessionId,...(inherited?{sourceSessionId:s.id}:{}),turn:i.turn,settledRevision:s.revision,rawHash:summaryRawHash(s.turns)})||
  !same(checkpoint.dispatch,dispatchSnapshot(s))||!same(checkpoint.inheritance,inheritedState(s)))fail('BRANCH_CHECKPOINT_INVALID');
 return checkpoint;
}
export function validateBranchLineage(checkpoint,sources){
 if(checkpoint.format!==INHERITED_CHECKPOINT_FORMAT)return checkpoint;
 if(!Array.isArray(sources)||sources.length!==checkpoint.lineage.length)fail('BRANCH_LINEAGE_INVALID');
 let expected=checkpoint.parentOrigin;
 for(const source of sources){
  if(!expected||!same(branchOrigin(source),expected)||checkpoint.intent.turn>source.boundary.turn)fail('BRANCH_LINEAGE_INVALID');
  expected=source.archive.origin&&checkpoint.intent.turn<=source.archive.origin.turn?source.archive.origin:null;
 }
 if(expected||!same(checkpoint.archive,sealedPrefix(sources.at(-1).archive,checkpoint.intent.turn)))fail('BRANCH_LINEAGE_INVALID');
 return checkpoint;
}
export function branchInheritance(checkpoint){validateBranchCheckpoint(checkpoint);return clone(checkpoint.inheritance);}
