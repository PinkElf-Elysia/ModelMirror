import {BranchCheckpoints} from './branch-storage.mjs';
import {panelSnapshot} from './variable-panel.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {AuthoringStore} from './store.mjs';
import {blankDocument,canonical,sha256} from './contracts.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {OfflineFixtureProvider} from './offline-session.mjs';
import {JointSessionHost} from './joint-session.mjs';
import {JointBudget} from './joint-budget.mjs';
import {stageOperationId} from './joint-plan.mjs';
import {jointSessionId,validateJointSession} from './joint-session-state.mjs';
import {OfflineModelControl,modelCapacity} from './model-control.mjs';
import {memoryAt} from './memory-state.mjs';
import {memoryContextPolicy,assembleMemoryContext} from './memory-context.mjs';
import {createAuthoringHistoryControl} from './history-control.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-variable-tests/',import.meta.url));await mkdir(root,{recursive:true});
const digest=v=>sha256(canonical(v)),clone=v=>structuredClone(v),reply=(text,finishReason='stop',status='completed')=>({status,text,finishReason});
const reject=(fn,code)=>assert.rejects(async()=>fn(),e=>{assert.equal(e.code,code,e.stack);return true;});
const id=jointSessionId('session'),opening='原始开局：雾港的一封信。',H='rpg.history-window',M2='rpg.rolling-summary',M3='rpg.memory-palace';
const deferred=()=>{let resolve;return {promise:new Promise(r=>{resolve=r;}),resolve};};
const catalog={catalogId:'joint-fixture',models:['story','summary','memory'].map(id=>({
 selectionId:id,selectionRevision:sha256(id),modelId:'fixture/'+id,name:'离线 '+id,available:true,contextLength:256000,maxOutputTokens:16000,
 parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]},
}))};
const proposal={operations:[{type:'create',name:'港口承诺',keywords:['雾港','信'],match:'any',roles:['user','assistant'],content:'双方计划次日见面，尚未兑现。',sourceTurns:[1]}]};
function replies(options={}){
 const result={};
 for(const name of ['one','two','three','four','five','manual','retry']){
  result[name]=reply(name==='one'?'古老灯塔。<details>卡内总结：约定明天见面</details>':'完整原文 '+name);
  for(const phase of ['summary-before','summary-after'])result[stageOperationId(id,name,phase)]=reply(options.summaryText??'两人有一个未兑现的港口承诺。');
  for(const phase of ['compression-before','compression-after'])result[stageOperationId(id,name,phase)]=reply('压缩后：承诺尚未兑现。');
  result[stageOperationId(id,name,'memory-after')]=reply(options.memoryText??JSON.stringify(name==='one'?proposal:{operations:[]}));
 }
 return {...result,...options.replies};
}
async function fixture(t,options={}){
 const directory=await mkdtemp(join(root,'run-'));let store=await AuthoringStore.open(join(directory,'works'));
 const doc=blankDocument();if(options.panel!==false)doc.settings.variablePanel={enabled:true,skeleton:JSON.stringify({地点:'码头',体力:80,背包:[]}),template:'<p>{{地点}}</p>',instructions:'随已发生事件更新。'};doc.metadata.name='独立作品 '+randomUUID();doc.metadata.audience='male';doc.sources.system=' FROZEN SYSTEM\r\n';doc.sources.prefix='PRE';doc.sources.suffix='POST';
 const work=await store.create({operationId:'work',expectedRevision:null,document:doc}),pub=await store.publish(work.workId,{operationId:'publish',expectedRevision:0}),reference={workId:work.workId,versionId:pub.result.versionId};
 const resolveRuntime=async ref=>{await options.beforeResolveRuntime?.();return openPublishedRuntime(store,ref);},modelControl=new OfflineModelControl(catalog);
 let host,control,provider,budget,branches;
 async function open(faults=true){
  control=await createAuthoringHistoryControl({directory:join(directory,'plugins'),lookupSession:x=>host.lookupSession(x),resolveRuntime});
  provider=await OfflineFixtureProvider.open(join(directory,'provider'),{replies:replies(options),fault:faults?options.providerFault:undefined});
  budget=await JointBudget.open({directory:join(directory,'budget'),allowanceId:'shared',limit:options.limit??30,storageFault:faults?options.budgetFault:undefined});
  branches=await BranchCheckpoints.open({directory:join(directory,'branches')});
  host=await JointSessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider,budget,modelControl,historyControl:control,branchStore:branches,fault:faults?options.fault:undefined,storageFault:faults?options.storageFault:undefined});
 }
 await open();await host.create({operationId:'session',expectedRevision:null,reference,openingText:opening,presetId:null});
 async function close(){await host.close();await control.close();await provider.close();await budget.close();await branches.close();}
 t.after(async()=>{await close();await store.close();});
 const f={directory,id,reference,get host(){return host;},get provider(){return provider;},get budget(){return budget;},get service(){return control.pluginService;},
  read:()=>host.read(id),idle:()=>host.waitForIdle(id),
  async select(purpose){const s=(await f.read()).session,c=modelControl.catalog();return host.configureModel({sessionId:id,operationId:randomUUID(),expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,
   choice:{catalogRevision:c.revision,selectionId:purpose,selectionRevision:sha256(purpose),parameters:{maxOutputTokens:4096}}});},
  async change(action,pluginId){const c=await f.service.catalog(),p=c.plugins.find(p=>p.id===pluginId);return f.service.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,
   ...(['enable','disable'].includes(action)?{sessionId:id,expectedSessionRevision:(await host.lookupSession(id)).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},
  async enable(plugin){await f.change('install',plugin);await f.change('enable',plugin);},
  async input(operationId='one',input=opening,kind='send'){const v=await f.read();return {sessionId:id,operationId,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,kind,input};},
  async send(...args){return host.send(await f.input(...args));},
  async sent(...args){const r=await f.send(...args);await f.idle();return r;},
  async update(operationId='manual'){return host.update(await f.input(operationId,null,'update'));},
  async recover(operationId='one'){const s=(await f.read()).session;return host.recover({sessionId:id,operationId,expectedRevision:s.revision,expectedSummaryRevision:s.summary.snapshot.revision,expectedMemoryRevision:s.memory.snapshot.revision});},
  async reopen(){await close();await store.close();store=await AuthoringStore.open(join(directory,'works'));await open(false);},
 };
 for(const p of ['story','summary','memory'])if(options[p+'Model']!==false)await f.select(p);
 if(options.summary===true)await f.enable(M2);if(options.memory===true)await f.enable(M3);
 return f;
}

const data=(place='灯塔',hp=70)=>({地点:place,体力:hp,背包:['海图']});
const output=value=>'完整剧情。<variable_panel>'+JSON.stringify(value)+'</variable_panel>';
test('state snapshot persists with full reply; next input carries it once; restart and duplicate stay stable',async t=>{
 const f=await fixture(t,{replies:{one:reply(output(data())),two:reply(output(data('山径',60)))}});
 assert.equal(panelSnapshot((await f.read()).session).data.地点,'码头');
 const i=await f.input();const first=await f.host.send(i);await f.idle();
 let s=(await f.read()).session;assert.equal(s.turns[0].variablePanel.status,'applied');assert.equal(s.turns[0].assistant,output(data()));assert.equal(s.turns[0].userMessage,'PRE\n\n'+opening+'\n\nPOST');
 const second=await f.sent('two','前往山径');s=(await f.read()).session;
 assert.equal(second.operation.request.messages[0].content,' FROZEN SYSTEM\r\n');
 assert.equal(second.operation.request.messages.at(-1).content.match(/【变量面板】/g).length,1);
 assert.ok(second.operation.request.messages.at(-1).content.includes('灯塔'));assert.equal(panelSnapshot(s).data.地点,'山径');
 const before=canonical(s);await f.reopen();assert.equal(canonical((await f.read()).session),before);assert.equal(f.provider.dispatchCount,0);assert.equal((await f.host.send(i)).replayed,true);
});
test('M1 clipping and M2/M3 keep full raw input; panel stays independent',async t=>{
 const f=await fixture(t,{summary:true,memory:true,replies:{one:reply(output(data())),two:reply(output(data('山径',60))),three:reply(output(data('港口',55)))}});
 await f.enable(H);let s=(await f.read()).session;
 await f.host.configureHistory({sessionId:id,operationId:'window',expectedRevision:s.revision,expectedHistoryRevision:s.history.revision,expectedContextPolicyRevision:(await f.read()).historyConfigurationPolicyRevision,config:{turns:1,includeInitialCharacter:false}}).catch(e=>{throw e;});
 await f.sent();await f.sent('two','继续');await f.sent('three','返回');s=(await f.read()).session;
 assert.equal(panelSnapshot(s).data.地点,'港口');assert.ok(s.memory.jobs[0].request.source.turns[0].assistant.includes('<variable_panel>'));assert.equal(s.memory.jobs[0].request.source.turns[0].user,opening);
 assert.equal(s.operations[2].request.memoryContext.variablePanelSnapshot.state.data.地点,'山径');
});
test('invalid/missing snapshots preserve previous state and raw outputs without extra dispatch',async t=>{
 const bad=output({...data(),体力:'错误'}),f=await fixture(t,{replies:{one:reply(output(data())),two:reply(bad),three:reply('没有变量数据')}});
 await f.sent();await f.sent('two','继续');await f.sent('three','继续');const s=(await f.read()).session;
 assert.equal(s.turns[1].variablePanel.diagnostic,'PANEL_DATA_TYPE');assert.equal(s.turns[1].assistant,bad);assert.equal(s.turns[2].variablePanel.status,'missing');assert.equal(panelSnapshot(s).data.地点,'灯塔');assert.equal(f.provider.dispatchCount,3);
});
test('unknown result recovers same receipt exactly once after restart',async t=>{
 const f=await fixture(t,{replies:{one:reply(output(data()))},providerFault:(phase,r)=>{if(phase==='afterReceipt'&&!r.kind)throw Error('lost');}});
 await f.sent();assert.equal((await f.read()).session.turns.length,0);await f.reopen();await f.recover();let s=(await f.read()).session;assert.equal(s.turns.length,1);assert.equal(panelSnapshot(s).revision,1);assert.equal(f.provider.dispatchCount,0);await f.recover();assert.equal((await f.read()).session.turns.length,1);
});
for(const phaseName of ['beforeCommit','afterCommit'])test('state and raw turn atomic through '+phaseName,async t=>{
 let armed=true;const f=await fixture(t,{replies:{one:reply(output(data()))},storageFault:(phase,record)=>{if(armed&&phase===phaseName&&record.data.turns?.length===1){armed=false;throw Error('disk');}}});
 await assert.rejects(f.send());await f.reopen();await f.recover();const s=(await f.read()).session;assert.equal(s.turns.length,1);assert.equal(panelSnapshot(s).data.地点,'灯塔');assert.equal(f.provider.dispatchCount,0);
});
test('truncated story never applies state or creates a completed turn',async t=>{
 const f=await fixture(t,{replies:{one:reply(output(data()),'length')}});await f.sent();const s=(await f.read()).session;assert.equal(s.turns.length,0);assert.equal(panelSnapshot(s).data.地点,'码头');assert.equal(s.operations[0].receipt.result.text,output(data()));
});
test('branch inherits checkpoint state, no future state, no model grants or extra allowance',async t=>{
 const f=await fixture(t,{replies:{one:reply(output(data())),two:reply(output(data('未来港口',30)))}});await f.enable('rpg.branch-save');await f.sent();await f.sent('two','未来');const v=await f.read();
 const made=await f.host.fork({sessionId:id,operationId:'fork',expectedRevision:v.session.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,turn:1,name:'灯塔分支'});
 assert.equal(panelSnapshot(made.session).data.地点,'灯塔');assert.equal(made.session.operations.length,0);assert.equal(made.session.models.current.story,null);assert.equal((await f.read()).budget.limit,30);await f.reopen();assert.equal(panelSnapshot((await f.host.read(made.session.id)).session).data.地点,'灯塔');
});
test('no panel keeps original request text, no snapshot and one call',async t=>{
 const f=await fixture(t,{panel:false});const r=await f.sent();assert.equal(r.operation.request.messages.at(-1).content,'PRE\n\n'+opening+'\n\nPOST');assert.equal(Object.hasOwn(r.operation.request.memoryContext,'variablePanelSnapshot'),false);assert.equal(Object.hasOwn((await f.read()).session,'variablePanel'),false);assert.equal(f.provider.dispatchCount,1);
});
