import test from 'node:test';
import assert from 'node:assert/strict';
import {canonical,sha256} from './contracts.mjs';
import {branchOrigin,originTurns,checkSessionOrigin,validateBranchOrigin} from './branch-origin.mjs';
import {emptyModelState,OfflineModelControl} from './model-control.mjs';
import {initialAuthoringSummary,validateAuthoringSummary,summaryUpdateSource,createSummaryTask} from './summary-state.mjs';
import {initialAuthoringMemory,validateAuthoringMemory,memoryUpdateSource,createMemoryTask,reviseMemory,memoryAt} from './memory-state.mjs';
import {changeEntries} from '../plugins/memory-palace.mjs';
const digest=v=>sha256(canonical(v)),clone=v=>structuredClone(v),seal=v=>{const {sha256:_,...body}=v;return {...body,sha256:digest(body)};};
const value={name:'灯塔约定',keywords:['灯塔'],match:'any',roles:['user','assistant'],content:'约定尚未兑现。',enabled:true};
function fixture(){
 const parentId='session-'+sha256('parent'),id='session-'+sha256('child');
 let entries=changeEntries([],{action:'create',value},'memory-'+sha256('entry').slice(0,40));
 entries=changeEntries(entries,{action:'update',id:entries[0].id,baseVersion:1,value:{...value,enabled:false}},'unused');
 const inheritance={binding:{format:'modelmirror.rpg.authored-session-binding/1',workId:'work-'+sha256('work'),versionId:'version-'+sha256('version'),runtimeHash:sha256('runtime'),openingHash:sha256('开局'),resourceHash:sha256('parent resource'),openingText:'开局'},allowanceId:'shared',presetId:null,examples:[],
  turns:[1,2].map(n=>({id:'turn-'+n,number:n,user:'原始输入 '+n,userMessage:'PRE 原始输入 '+n+' POST',assistant:'完整原文 '+n,rawHash:sha256('完整原文 '+n),messagesHash:sha256('messages '+n)})),
  settings:{history:{turns:1,includeInitialCharacter:false},modelSuggestions:{story:null,summary:null,memory:null}},
  summary:{text:'第一回合达成一项约定，尚未兑现。',coveredThrough:1,sourceHash:sha256('summary source'),versionId:sha256('summary version')},
  memory:{entries,processedThrough:2,sourceHash:sha256('memory source'),versionId:sha256('memory version')}};
 const checkpoint={id:'branch-'+sha256('checkpoint'),sha256:sha256('sealed checkpoint'),boundary:{sessionId:parentId,turn:2},inheritance};
 const origin=branchOrigin(checkpoint),s={id,revision:0,binding:{...inheritance.binding,sessionId:id,resourceHash:sha256('child resource')},allowanceId:'shared',origin,turns:originTurns(origin),models:emptyModelState(),examples:[]};
 return {checkpoint,s};
}
function select(s,purpose){
 const control=new OfflineModelControl({catalogId:'branch-fixture',models:[{selectionId:purpose,selectionRevision:sha256(purpose),modelId:'fixture/'+purpose,name:purpose,available:true,contextLength:128000,maxOutputTokens:16000,parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}}]}),catalog=control.catalog();
 const input={catalogRevision:catalog.revision,selectionId:purpose,selectionRevision:sha256(purpose),parameters:{maxOutputTokens:4096}},choice=control.select(input);
 const intent={sessionId:s.id,operationId:'choose-'+purpose,expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,choice:input};
 s.models.changes.push({revision:++s.models.revision,intent,intentHash:digest(intent),choice});s.models.current[purpose]=choice;s.revision++;
}
function add(s){const n=s.turns.length+1;s.turns.push({id:'turn-'+n,number:n,operationId:'new-story',requestId:'new-request',complete:true,user:'新输入',userMessage:'PRE 新输入 POST',assistant:'分支新剧情',rawHash:sha256('分支新剧情'),messagesHash:sha256('new messages')});s.revision++;}
const grant={enabled:true,revision:sha256('new grant')};
test('child starts with frozen raw turns, summary coverage and protected disabled entry revisions',()=>{
 const {s}=fixture(),summary=initialAuthoringSummary(s),memory=initialAuthoringMemory(s);
 assert.equal(summary.revision,1);assert.equal(summary.versions[0].kind,'inherited');assert.equal(summary.versions[0].coveredThrough,1);assert.equal(summaryUpdateSource(s,summary).needsUpdate,false);
 assert.equal(memory.revision,1);assert.equal(memory.processedThrough,2);assert.equal(memory.entries[0].protected,true);assert.equal(memory.entries[0].value.enabled,false);assert.equal(memory.entries[0].revisions.length,2);
 assert.deepEqual(s.models.current,{story:null,summary:null,memory:null});assert.equal(memoryUpdateSource(s,memory).needsUpdate,false);
 validateAuthoringSummary(summary,s);validateAuthoringMemory(memory,s);
});
test('inheritance is copied, not a live parent reference, and contains no executable requests or grants',()=>{
 const {s,checkpoint}=fixture(),before=canonical(s.origin);checkpoint.inheritance.turns[0].assistant='FUTURE';checkpoint.inheritance.memory.entries[0].value.content='FUTURE';
 assert.equal(canonical(s.origin),before);assert.ok(!canonical(s.origin).includes('FUTURE'));
 const forbidden=new Set(['authorization','jobs','operations','request','receipt','runId','operationId']);
 function walk(x){if(x&&typeof x==='object')for(const [k,v]of Object.entries(x)){assert.ok(!forbidden.has(k),k);walk(v);}}walk(s.origin);
});
test('after explicit child model choices, task sources start at the inherited coverage only',()=>{
 const {s}=fixture(),summary=initialAuthoringSummary(s),memory=initialAuthoringMemory(s);select(s,'summary');select(s,'memory');add(s);
 const st=createSummaryTask(s,summary,{operationId:'summary-next',expectedSummaryRevision:1,expectedModelRevision:s.models.revision,authorization:grant});
 const mt=createMemoryTask(s,memory,{operationId:'memory-next',expectedRevision:s.revision,expectedMemoryRevision:1,expectedModelRevision:s.models.revision,authorization:grant});
 assert.deepEqual(st.source.newTurns,[2]);assert.equal(st.source.previousSummary,summary.versions[0].effectiveText);
 assert.deepEqual(mt.source.turns.map(t=>t.number),[3]);assert.equal(mt.source.entries[0].protected,true);assert.equal(mt.source.entries[0].enabled,false);
 assert.equal(mt.sessionId,s.id);assert.equal(mt.authorization.revision,grant.revision);
});
test('inherited model suggestions cannot authorize or select a child model',()=>{
 const {s}=fixture(),summary=initialAuthoringSummary(s),memory=initialAuthoringMemory(s);add(s);
 assert.throws(()=>createSummaryTask(s,summary,{operationId:'s',expectedSummaryRevision:1,expectedModelRevision:0,authorization:grant}),{code:'SUMMARY_MODEL_REQUIRED'});
 assert.throws(()=>createMemoryTask(s,memory,{operationId:'m',expectedRevision:s.revision,expectedMemoryRevision:1,expectedModelRevision:0,authorization:grant}),{code:'MEMORY_MODEL_REQUIRED'});
});
test('manual child edits and revision replay preserve inherited memory and never alter origin',()=>{
 const {s}=fixture(),m=initialAuthoringMemory(s),entry=m.entries[0],before=canonical(s.origin);
 const changed=reviseMemory(s,m,{operationId:'child-edit',expectedRevision:0,expectedMemoryRevision:1,change:{action:'update',id:entry.id,baseVersion:2,value:{...value,content:'只属于子路线的修订。'}}}).state;
 assert.equal(changed.revision,2);assert.equal(changed.entries[0].version,3);assert.equal(canonical(s.origin),before);
 assert.deepEqual(memoryAt(s,changed,1),m);assert.equal(changed.entries[0].protected,true);validateAuthoringMemory(changed,s);
});
test('resealed summaries and memories cannot replace the checkpoint baseline',()=>{
 const {s}=fixture(),summary=initialAuthoringSummary(s),memory=initialAuthoringMemory(s);
 const ms=clone(memory);ms.entries[0].value.content='tampered';assert.throws(()=>validateAuthoringMemory(seal(ms),s));
 const ss=clone(summary);ss.versions[0].effectiveText='tampered';ss.versions[0].id=digest(Object.fromEntries(Object.entries(ss.versions[0]).filter(([k])=>k!=='id')));ss.activeVersionId=ss.versions[0].id;
 assert.throws(()=>validateAuthoringSummary(seal(ss),s),{code:'SUMMARY_VERSION_INVALID'});
});
test('wrong work, allowance, future turns and extra execution fields are rejected',()=>{
 for(const field of ['workId','versionId','openingText']){const {s}=fixture();s.binding[field]='different';assert.throws(()=>checkSessionOrigin(s),{code:'BRANCH_ORIGIN_MISMATCH'});}
 const {s}=fixture();s.allowanceId='new-allowance';assert.throws(()=>checkSessionOrigin(s),{code:'BRANCH_ORIGIN_MISMATCH'});
 const other=fixture().s;other.turns[1].assistant='FUTURE';assert.throws(()=>checkSessionOrigin(other),{code:'BRANCH_ORIGIN_MISMATCH'});
 const origin=fixture().s.origin;origin.inheritance.jobs=[];assert.throws(()=>validateBranchOrigin(seal(origin)));
});
test('zero-plugin origin keeps empty summary and memory without inventing a summary',()=>{
 const {checkpoint}=fixture();checkpoint.inheritance.summary={text:null,coveredThrough:0,sourceHash:sha256('empty'),versionId:null};checkpoint.inheritance.memory={entries:[],processedThrough:0,sourceHash:sha256('empty'),versionId:null};
 const origin=branchOrigin(checkpoint),s=fixture().s;s.origin=origin;s.turns=originTurns(origin);
 const summary=initialAuthoringSummary(s),memory=initialAuthoringMemory(s);assert.equal(summary.revision,0);assert.equal(memory.processedThrough,0);assert.equal(memory.entries.length,0);assert.equal(memoryUpdateSource(s,memory).turns.length,2);
});
