import test from 'node:test';
import assert from 'node:assert/strict';
import {canonical,sha256} from './contracts.mjs';
import {branchOrigin,originTurns} from './branch-origin.mjs';
import {emptyModelState} from './model-control.mjs';
import {changeEntries} from '../plugins/memory-palace.mjs';
import {emptyHistoryState,validateHistoryState,historyStateAt} from './history-control.mjs';
import {initialJointSummary} from './joint-summary.mjs';
import {initialMemoryJobs} from './memory-jobs.mjs';
import {jointSessionId,validateJointSession,BRANCH_SESSION_FORMAT} from './joint-session-state.mjs';
import {reviseMemory} from './memory-state.mjs';
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

function state(){
 const {s}=fixture();s.id=jointSessionId('child');s.binding.sessionId=s.id;
 Object.assign(s,{format:BRANCH_SESSION_FORMAT,createdAt:'2026-10-23T00:00:00Z',updatedAt:'2026-10-23T00:00:00Z',create:{operationId:'child',requestHash:sha256('create')},executionHash:sha256('execution'),presetId:null,draft:'',runs:[],operations:[],history:emptyHistoryState(s.origin.inheritance.settings.history),memoryEdits:[]});
 s.summary=initialJointSummary(s);s.memory=initialMemoryJobs(s);return s;
}
test('new /6 session validates a baseline with no jobs, grants, requests or selected models',()=>{
 const s=state();validateJointSession(s,s.executionHash);validateJointSession(JSON.parse(JSON.stringify(s)),s.executionHash);
 assert.equal(s.summary.jobs.length,0);assert.equal(s.memory.jobs.length,0);assert.equal(s.operations.length,0);assert.equal(s.runs.length,0);
 assert.equal(s.history.config.turns,1);assert.deepEqual(historyStateAt(s.history,0),{revision:0,config:s.history.config,initialConfig:s.history.config});
});
test('legacy /5 cannot acquire a hidden baseline and /6 cannot omit provenance',()=>{
 const s=state();s.format='modelmirror.rpg.offline-session/5';assert.throws(()=>validateJointSession(s,s.executionHash),{code:'JOINT_SESSION_CORRUPT'});
 const other=state();delete other.origin;assert.throws(()=>validateJointSession(other,other.executionHash),{code:'JOINT_SESSION_CORRUPT'});
});
test('inherited M1 config remains a non-authorizing base and cannot replace its checkpoint value',()=>{
 const s=state();s.history.config.turns=4;assert.throws(()=>validateJointSession(s,s.executionHash),{code:'HISTORY_STATE_CORRUPT'});
 const other=state();other.history.initialConfig.turns=4;assert.throws(()=>validateJointSession(other,other.executionHash),{code:'HISTORY_STATE_CORRUPT'});
 assert.throws(()=>validateHistoryState(state().history,state().id,0,state().binding.resourceHash),{code:'AUTHORING_UNKNOWN_FIELD'});
});
test('child manual revision starts after the imported version without fake completed jobs',()=>{
 const s=state(),m=s.memory.snapshot,e=m.entries[0],change={action:'update',id:e.id,baseVersion:e.version,value:{...e.value,content:'子路线的新约定'}};
 const input={sessionId:s.id,operationId:'manual',expectedRevision:0,expectedMemoryRevision:1,expectedContextPolicyRevision:sha256('policy'),change};
 s.memory.snapshot=reviseMemory(s,m,{operationId:input.operationId,expectedRevision:0,expectedMemoryRevision:1,change}).state;
 s.memoryEdits.push(input);s.revision=1;validateJointSession(s,s.executionHash);assert.equal(s.memory.jobs.length,0);
});
test('removing inherited versions or appending an unproved historical turn is rejected',()=>{
 const s=state();s.turns.push({...clone(s.turns[1]),id:'turn-3',number:3});assert.throws(()=>validateJointSession(s,s.executionHash));
 const other=state();other.memory.snapshot.versions=[];other.memory.snapshot.revision=0;other.memory.snapshot=seal(other.memory.snapshot);assert.throws(()=>validateJointSession(other,other.executionHash));
});
