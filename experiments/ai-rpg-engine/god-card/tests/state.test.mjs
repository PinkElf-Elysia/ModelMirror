import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createCardState} from '../../studio/card-state.mjs';
import {createModelPolicy} from '../../studio/card-model-policy.mjs';
import {initialModelState,requireModelState,controlledChoice} from '../../studio/model-runtime.mjs';
import {initialSummaryState,sealSummary,requireSummaryState} from '../../studio/summary-state.mjs';
import {initialHistoryState} from '../../studio/history-state.mjs';
import {initialMemoryState} from '../../studio/memory-state.mjs';
import {initialMemoryTasks,sealMemoryTasks,requireMemoryTasks} from '../../studio/memory-task-state.mjs';
import {atomicJson} from '../../studio/branch-archive.mjs';
import {sha,canonical} from '../../plugins/catalog.mjs';
const policy=createModelPolicy({cardId:'god',parametersFor:name=>name==='synthetic/god-state'?{max_tokens:2048,temperature:.3}:null});
const state=createCardState(policy),model=policy.controlledChoice({model:'synthetic/god-state',selectionId:'test',selectionRevision:'v1'});
const auth=enabled=>({enabled,revision:sha('grant-'+enabled)});
function session(){
 const s={id:'god-state-fixture',cardId:'god',runtime:{hash:sha('state-only-fixture'),descriptor:{cardId:'god'}},revision:2,history:[],turns:[],requests:{},characterText:'虚构开局',
 modelState:initialModelState(model),historyWindow:initialHistoryState(),rollingSummary:sealSummary({...initialSummaryState(),config:{timing:'before',model}}),
 memoryPalace:initialMemoryState(),memoryTasks:sealMemoryTasks({...initialMemoryTasks(),config:{model,revision:0}})};
 for(let n=1;n<=2;n++){const requestId='turn-'+n,raw='已完成原文'+n;s.history.push({role:'user',content:'原输入'+n},{role:'assistant',content:raw});s.turns.push({requestId,input:'原输入'+n,raw,rawHash:sha(raw),model:{selection:model,selectionRevision:0}});
 s.requests[requestId]={status:'complete',selection:model,selectionRevision:0};}
 return s;
}
test('bound God state accepts explicit non-Earth parameters; default and foreign card policies reject them',()=>{
 const s=session();state.requireModelState(s);state.summary.requireSummaryState(s);state.memory.requireMemoryTasks(s);
 assert.throws(()=>requireModelState(s),/MODEL_STATE_INVALID/);
 assert.throws(()=>requireSummaryState(s),/SUMMARY_STATE_INVALID/);
 assert.throws(()=>requireMemoryTasks(s),/MEMORY_TASK_STATE_INVALID/);
 for(const call of [state.requireModelState,state.summary.requireSummaryState,state.memory.requireMemoryTasks]){
  assert.throws(()=>call({...s,cardId:'earth'}),/CARD_STATE_BINDING_INVALID/);
  assert.throws(()=>call({...s,runtime:{...s.runtime,descriptor:{cardId:'earth'}}}),/CARD_STATE_BINDING_INVALID/);
 }
 const bad=structuredClone(s);bad.modelState.current=controlledChoice({model:'synthetic/god-state',selectionId:'test',selectionRevision:'v1'});
 assert.throws(()=>state.requireModelState(bad),/MODEL_STATE_INVALID/);
});
test('summary model output, manual revision, restore, source and persisted round-trip use the same policy',async()=>{
 const s=session(),original=canonical(s.history),raw='只发现钟楼损坏，修复仍是承诺。';
 const version=state.summary.modelSummaryVersion(s,{raw,finishReason:'stop',model,receipt:{requestId:'summary-one',requestHash:sha('summary-source'),responseHash:sha(raw)},expectedActiveVersionId:null,targetThrough:1});
 s.rollingSummary=state.summary.withSummaryVersion(s,version);
 const edit=state.summary.manualSummaryVersion(s,{text:'人工核对：尚未修好。'});s.rollingSummary=state.summary.withSummaryVersion(s,edit);
 const restore=state.summary.manualSummaryVersion(s,{restoreVersionId:version.id});s.rollingSummary=state.summary.withSummaryVersion(s,restore);
 assert.equal(state.summary.summaryUpdateSource(s).previousSummary,raw);
 const p=state.summary.effectiveContextPolicy(s,auth(true),auth(false));assert.equal(p.ready,true);assert.equal(p.mode,'rolling-summary');
 assert.equal(canonical(s.history),original);assert.equal(s.rollingSummary.versions[0].raw,raw);
 const root=fileURLToPath(new URL('../../.rpg04-work/god-card/b3-state/',import.meta.url));await mkdir(root,{recursive:true});
 const dir=await mkdtemp(join(root,'case-'));await atomicJson(join(dir,'session.json'),s);
 const restored=JSON.parse(await readFile(join(dir,'session.json'),'utf8'));state.requireModelState(restored);state.summary.requireSummaryState(restored);state.memory.requireMemoryTasks(restored);
 const corrupted=structuredClone(restored);corrupted.history[0].content+='changed';assert.throws(()=>state.summary.requireSummaryState(corrupted),/SUMMARY_STATE_INVALID/);
});
test('memory task choices, snapshot hashes and joint stage planning use the bound model policy',()=>{
 const s=session(),input={operationId:'pending-fixture'},messages=[{role:'user',content:'仅离线'}],source={from:1,through:2};
 const task={sessionId:s.id,stageId:'memory-after',purpose:'memory',requestId:sha('memory'),selection:model,messages,messagesHash:sha(canonical(messages)),source,sourceHash:sha(canonical(source)),status:'unknown'};
 s.memoryTasks=sealMemoryTasks({...s.memoryTasks,operations:{'pending-fixture':{input,inputHash:sha(canonical(input)),kind:'update',status:'unknown',plan:[{stageId:'memory-after',purpose:'memory'}],tasks:[task]}}});
 state.memory.requireMemoryTasks(s);
 const wrong=structuredClone(s);wrong.memoryTasks.operations['pending-fixture'].tasks[0].selection=controlledChoice({model:'synthetic/god-state',selectionId:'test',selectionRevision:'v1'});wrong.memoryTasks=sealMemoryTasks(wrong.memoryTasks);
 assert.throws(()=>state.memory.requireMemoryTasks(wrong),/MEMORY_TASK_STATE_INVALID/);
 s.memoryTasks=sealMemoryTasks({...s.memoryTasks,operations:{}});
 s.rollingSummary=sealSummary({...s.rollingSummary,config:{timing:'after',model}});
 const a={history:auth(false),summary:auth(true),memory:auth(true)};
 assert.deepEqual(state.memory.memoryPlan(s,a).map(x=>x.stageId),['summary-before','compression-before','story','summary-after','compression-after','memory-after']);
 assert.equal(state.memory.memoryContextPolicy(s,a).reason,'SUMMARY_UPDATE_REQUIRED');
});
