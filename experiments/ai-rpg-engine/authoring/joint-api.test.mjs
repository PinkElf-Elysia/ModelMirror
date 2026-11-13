import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {startAuthoringServer} from './server.mjs';
import {OfflineFixtureProvider} from './offline-session.mjs';
import {JointFixtureProvider} from './joint-fixture.mjs';
import {OfflineModelControl} from './model-control.mjs';
import {stageOperationId} from './joint-plan.mjs';
import {jointSessionId} from './joint-session-state.mjs';
import {modelPluginSettingsPath} from './model-ui.mjs';
import {blankDocument,sha256,canonical} from './contracts.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6q-tests/',import.meta.url));await mkdir(root,{recursive:true});
const base='/rpg-app/api/authoring',jp='/joint',mp='/model-settings',id=jointSessionId('session');
const catalog={catalogId:'joint-http',models:['story','summary','memory'].map(k=>({selectionId:k,selectionRevision:sha256(k),modelId:'fixture/'+k,name:'离线 '+k,available:true,contextLength:128000,maxOutputTokens:16000,
 parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}}))};
const reply=text=>({status:'completed',finishReason:'stop',text});
const proposal={operations:[{type:'create',name:'桅杆来信',keywords:['信'],match:'any',roles:['user','assistant'],content:'计划明早见面，尚未发生。',sourceTurns:[1]}]};
async function fixture(t,options={}){
 const directory=await mkdtemp(join(root,'case-')),control=new OfflineModelControl(catalog);let server,provider;
 const replies={};for(const name of ['one','two','three','manual']){
  replies[name]=reply('完整原文 '+name);replies[stageOperationId(id,name,'memory-after')]=reply(options.memoryText??JSON.stringify(name==='one'?proposal:{operations:[]}));
  for(const phase of ['summary-before','summary-after','compression-before','compression-after'])replies[stageOperationId(id,name,phase)]=reply('独立摘要：有待兑现的约定。');
 }
 async function open(faults=true){
  provider=options.scenario?await JointFixtureProvider.open(join(directory,'provider'),{replies:{story:reply('固定剧情'),summary:reply('固定摘要'),compression:reply('固定压缩'),memory:reply('{"operations":[]}'),memoryFirst:reply(JSON.stringify(proposal))}}):await OfflineFixtureProvider.open(join(directory,'provider'),{replies,fault:faults?options.providerFault:undefined});
  server=await startAuthoringServer({directory:join(directory,'works'),modelSettings:{directory:join(directory,'sessions'),provider,control},historySettings:{directory:join(directory,'history')},
   jointSettings:{directory:join(directory,'joint'),allowance:{id:'fixture',limit:options.limit??20},...(faults&&options.fault?{fault:options.fault}:{})}});
 }
 await open();t.after(async()=>{await server.close();await provider.close();});
 const req=async(path,body,headers={})=>{const r=await fetch(server.origin+base+path,body===undefined?{headers}:{method:'POST',headers:{Origin:server.origin,'Content-Type':'application/json','X-Authoring-Request':'1',...headers},body:JSON.stringify(body)});return {status:r.status,value:await r.json()};};
 const ok=async(path,body)=>{const r=await req(path,body);assert.equal(r.status,200,JSON.stringify(r.value));return r.value;};
 const doc=blankDocument();doc.metadata.name='联合接口样例';doc.metadata.audience='female';doc.sources.system='AUTHOR';doc.sources.prefix='PRE';doc.sources.suffix='POST';
 const w=await ok('/works',{operationId:'work',expectedRevision:null,document:doc}),v=await ok('/works/'+w.workId+'/publish',{operationId:'publish',expectedRevision:0});
 const creation={operationId:'session',expectedRevision:null,reference:{workId:w.workId,versionId:v.result.versionId},openingText:'原始开局：一封信',presetId:null};await ok(mp+'/sessions',creation);
 const f={directory,req,ok,creation,get server(){return server;},get provider(){return provider;},view:()=>ok(jp+'/sessions/'+id),settings:()=>ok(jp+'/settings/'+id),
  async intent(purpose,action,operationId=randomUUID()){
   const s=await f.settings();return {purpose,action,sessionId:id,operationId,expectedRevision:s.sessionRevision,expectedModelRevision:s.modelRevision,expectedHistoryRevision:s.historyRevision,
    expectedSummaryRevision:s.summaryRevision,expectedMemoryRevision:s.memoryRevision,expectedContextPolicyRevision:s.effectivePolicy.revision,expectedRegistryRevision:s.registryRevision,
    ...(action==='enable'?{permissions:s.plugins[purpose].permissions}:{}),...(action==='configure'?{config:{turns:1,includeInitialCharacter:false}}:{})};
  },
  async change(purpose,action,operationId){return ok(jp+'/settings/change',await f.intent(purpose,action,operationId));},
  async enable(purpose){await f.change(purpose,'install');await f.change(purpose,'enable');},
  async select(purpose){const s=(await f.view()).session;return ok(mp+'/configure',{sessionId:id,operationId:randomUUID(),expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,
   choice:{catalogRevision:control.catalog().revision,selectionId:purpose,selectionRevision:sha256(purpose),parameters:{maxOutputTokens:4096}}});},
  async input(operationId='one',kind='send'){const v=await f.view();return {sessionId:id,operationId,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,
   expectedContextPolicyRevision:v.effectivePolicy.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,kind,
   input:kind==='update'?null:operationId==='one'?creation.openingText:'继续找信'};},
  async send(name='one'){const r=await ok(jp+'/send',await f.input(name));await f.idle();return r;},idle:()=>server.sessionHost.waitForIdle(id),
  async recover(operationId='one'){const s=(await f.view()).session;return ok(jp+'/recover',{sessionId:id,operationId,expectedRevision:s.revision,expectedSummaryRevision:s.summary.snapshot.revision,expectedMemoryRevision:s.memory.snapshot.revision});},
  async reopen(){await server.close();await provider.close();await open(false);},
 };
 return f;
}
const rejects=async(f,path,body,code)=>{const r=await f.req(path,body);assert.equal(r.value.error?.code,code,JSON.stringify(r));return r;};

test('joint routes default off; no server modules or legacy session route exposed',async t=>{
 const directory=await mkdtemp(join(root,'off-')),server=await startAuthoringServer({directory});t.after(()=>server.close());
 assert.deepEqual(await(await fetch(server.origin+base+jp)).json(),{enabled:false,realProvider:false,comparison:false});
 for(const p of [base+jp+'/sessions/'+id,'/joint-session.mjs','/joint-budget.mjs'])assert.equal((await fetch(server.origin+p)).status,404);
 const f=await fixture(t);assert.equal((await f.req('/sessions/'+id)).status,404);assert.equal((await f.req('/history-settings')).value.enabled,false);
});

test('M1 M2 M3 independent grants and explicit model selections; no configuration dispatch',async t=>{
 const f=await fixture(t);assert.equal((await f.settings()).realProvider,false);for(const purpose of ['summary','memory']){
  await f.change(purpose,'install');await rejects(f,jp+'/settings/change',await f.intent(purpose,'enable'),purpose==='summary'?'SUMMARY_MODEL_REQUIRED':'MEMORY_MODEL_REQUIRED');
  await f.select(purpose);await f.change(purpose,'enable');
 }
 await f.enable('history');await f.change('history','configure');let v=await f.view();assert.equal(v.effectivePolicy.mode,'rolling-summary');assert.equal(v.effectivePolicy.authorization.memory.enabled,true);
 await f.change('summary','disable');v=await f.view();assert.equal(v.effectivePolicy.mode,'history-window');assert.equal(v.session.history.config.turns,1);assert.equal(f.provider.dispatchCount,0);
 await f.change('memory','uninstall');await f.change('memory','install');assert.equal((await f.view()).effectivePolicy.authorization.memory.enabled,false);
 const before=canonical(await f.settings());await f.reopen();assert.equal(canonical(await f.settings()),before);
 const r=await f.ok(mp+'/reconcile',{sessionId:null,operationId:'session',kind:'create'});assert.equal(r.found,true);assert.equal(r.view.session.id,id);assert.equal(f.provider.dispatchCount,0);
});

test('HTTP estimate reserves nothing; send atomically persists story then M2 then original-source M3',async t=>{
 const order=[],f=await fixture(t,{providerFault:(phase,r)=>{if(phase==='afterAccepted')order.push(r.kind??'story');}});
 for(const purpose of ['story','summary','memory'])await f.select(purpose);await f.enable('summary');await f.enable('memory');
 const estimate=await f.ok(jp+'/estimate',await f.input());assert.equal(estimate.plan.maximumCalls,2);assert.equal((await f.view()).budget.used,0);
 await f.send();await f.send('two');const r=await f.send('three'),s=(await f.view()).session;
 assert.deepEqual(order,['story','memory','story','summary','memory','story','summary','memory']);
 assert.equal(s.turns.length,3);assert.equal(s.memory.snapshot.processedThrough,3);assert.equal((await f.view()).budget.used,8);
 assert.equal(r.operation.request.messages.filter(m=>m.role==='system').length,1);assert.match(r.operation.request.messages[0].content,/AUTHOR.*独立摘要.*桅杆来信/s);
 assert.equal(s.memory.jobs[1].request.source.turns[0].user,'继续找信');assert.equal(s.memory.jobs[1].request.source.turns[0].assistant,'完整原文 two');
});

test('all settings revisions, permissions and trusted-field injection rejected before writes',async t=>{
 const f=await fixture(t);await f.select('memory');await f.change('memory','install');const i=await f.intent('memory','enable');
 for(const [field,code]of [['expectedRevision','OFFLINE_REVISION_CONFLICT'],['expectedModelRevision','MODEL_REVISION_CONFLICT'],['expectedHistoryRevision','HISTORY_REVISION_CONFLICT'],['expectedSummaryRevision','SUMMARY_REVISION_CONFLICT'],['expectedMemoryRevision','MEMORY_REVISION_CONFLICT'],['expectedRegistryRevision','PLUGIN_REGISTRY_CONFLICT']])await rejects(f,jp+'/settings/change',{...i,[field]:99},code);
 await rejects(f,jp+'/settings/change',{...i,permissions:[]},'PLUGIN_PERMISSION_MISMATCH');
 for(const x of [{system:'injected'},{providerUrl:'https://foreign.invalid'},{history:[]}])await rejects(f,jp+'/settings/change',{...i,...x},'AUTHORING_UNKNOWN_FIELD');
 assert.equal((await f.req(jp+'/settings/change',i,{Origin:'https://foreign.invalid'})).status,403);assert.equal(f.provider.dispatchCount,0);
});

test('setting IDs cannot be reused for model selection, creating a session, send or manual edits',async t=>{
 const f=await fixture(t),i=await f.intent('memory','install','reserved-setting');const a=await f.ok(jp+'/settings/change',i),b=await f.ok(jp+'/settings/change',i);assert.equal(b.replayed,true);assert.deepEqual(a.receipt,b.receipt);
 await rejects(f,jp+'/settings/change',{...i,expectedRevision:99},'JOINT_SETTINGS_OPERATION_COLLISION');
 for(const path of [mp+'/configure',jp+'/send',jp+'/memory/edit'])await rejects(f,path,{operationId:i.operationId},'JOINT_SETTINGS_OPERATION_COLLISION');
 await rejects(f,mp+'/sessions',{...f.creation,operationId:i.operationId},'JOINT_SETTINGS_OPERATION_COLLISION');
 await rejects(f,jp+'/settings/change',await f.intent('memory','uninstall','session'),'OFFLINE_OPERATION_COLLISION');
});

for(const action of ['install','enable','disable','uninstall'])test('lost '+action+' receipt recovered by original settings intent after restart',async t=>{
 let armed=false;const f=await fixture(t,{fault:phase=>{if(armed&&phase==='afterChange'){armed=false;throw Error('lost');}}});await f.select('memory');
 if(action!=='install')await f.change('memory','install');if(['disable','uninstall'].includes(action))await f.change('memory','enable');
 const i=await f.intent('memory',action);armed=true;await rejects(f,jp+'/settings/change',i,'JOINT_SETTINGS_RESULT_UNKNOWN');
 await rejects(f,jp+'/settings/change',i,'JOINT_SETTINGS_RESULT_UNKNOWN');await f.reopen();assert.deepEqual((await f.settings()).unconfirmed,i);
 const r=await f.ok(jp+'/settings/reconcile',i);assert.equal(r.state,'applied');assert.equal(r.settings.unconfirmed,null);assert.equal(f.provider.dispatchCount,0);
});

test('pre-change failure stays unapplied after recovery and blocks new work until checked',async t=>{
 let armed=true;const f=await fixture(t,{fault:phase=>{if(armed&&phase==='beforeChange'){armed=false;throw Error('before');}}}),i=await f.intent('memory','install');
 await rejects(f,jp+'/settings/change',i,'JOINT_SETTINGS_RESULT_UNKNOWN');await rejects(f,mp+'/configure',{},'JOINT_SETTINGS_RESULT_PENDING');
 const r=await f.ok(jp+'/settings/reconcile',i);assert.equal(r.state,'not_applied');assert.equal(r.settings.plugins.memory.installed,false);
 assert.equal((await f.ok(jp+'/settings/change',i)).state,'applied');assert.equal(f.provider.dispatchCount,0);
});

test('HTTP unknown memory survives disable and restart; recovery never starts a new call',async t=>{
 const f=await fixture(t,{providerFault:(phase,r)=>{if(phase==='afterReceipt'&&r.kind==='memory')throw Error('lost');}});await f.select('story');await f.select('memory');await f.enable('memory');await f.send();
 assert.equal((await f.view()).session.memory.jobs[0].state,'unknown');await f.change('memory','disable');await f.reopen();
 const r=await f.recover();assert.equal(r.run.state,'paused');const v=await f.view();assert.equal(v.session.memory.jobs[0].state,'revoked');assert.equal(v.session.memory.snapshot.revision,0);assert.equal(v.budget.used,2);assert.equal(f.provider.dispatchCount,0);
});

test('insufficient full reservation blocks story without consuming calls; new session has same allowance',async t=>{
 const f=await fixture(t,{limit:1});await f.select('story');await f.select('memory');await f.enable('memory');
 const r=await f.send();assert.equal(r.run.diagnostic,'JOINT_BUDGET_EXHAUSTED');assert.equal(f.provider.dispatchCount,0);
 const other=await f.ok(mp+'/sessions',{...f.creation,operationId:'another-session'}),v=await f.ok(jp+'/sessions/'+other.session.id);assert.equal(v.budget.remaining,1);assert.equal(v.budget.limit,1);
});

test('manual memory revision protected, replayed once, original model output stays unchanged',async t=>{
 const f=await fixture(t);await f.select('story');await f.select('memory');await f.enable('memory');await f.send();
 const v=await f.view(),entry=v.session.memory.snapshot.entries[0],raw=v.session.memory.jobs[0].receipt;
 const edit={sessionId:id,operationId:'edit',expectedRevision:v.session.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,
  change:{action:'update',id:entry.id,baseVersion:entry.version,value:{...entry.value,content:'人工纠正'}}};
 const r=await f.req(jp+'/memory/edit',edit);assert.equal(r.status,200,JSON.stringify(r.value));
 assert.equal((await f.ok(jp+'/memory/edit',edit)).replayed,true);assert.deepEqual((await f.view()).session.memory.jobs[0].receipt,raw);assert.equal(f.provider.dispatchCount,2);
});


test('interactive joint fixture accepts new browser IDs and preserves memory journal on reopen',async t=>{
 const f=await fixture(t,{scenario:true});for(const p of ['story','summary','memory'])await f.select(p);await f.enable('summary');await f.enable('memory');
 for(const name of ['one','two']){const input=await f.input(name);input.operationId=randomUUID();await f.ok(jp+'/send',input);await f.idle();}
 const before=(await f.view()).session;assert.equal(before.memory.snapshot.processedThrough,2);assert.equal(before.memory.snapshot.entries.length,1);assert.equal(f.provider.dispatchCount,5);
 await f.reopen();assert.deepEqual((await f.view()).session,before);assert.equal(f.provider.dispatchCount,0);
});


test('reopening model editor reads joint grant journal and preserves unknown-setting lock',async t=>{
 let armed=false;const f=await fixture(t,{fault:phase=>{if(armed&&phase==='beforeChange'){armed=false;throw Error('lost settings');}}});
 const view=await f.ok(mp+'/sessions/'+id),path=modelPluginSettingsPath(view.session);
 const settings=await f.ok(path);assert.equal(settings.sessionId,id);assert.equal(settings.unconfirmed,null);
 assert.equal((await f.req('/history-settings/sessions/'+id)).status,404);
 armed=true;const intent=await f.intent('memory','install');await rejects(f,jp+'/settings/change',intent,'JOINT_SETTINGS_RESULT_UNKNOWN');
 await f.reopen();const reopened=await f.ok(mp+'/sessions/'+id),locked=await f.ok(modelPluginSettingsPath(reopened.session));
 assert.deepEqual(locked.unconfirmed,intent);assert.equal(f.provider.dispatchCount,0);
 assert.equal(modelPluginSettingsPath({id,history:{},format:'modelmirror.rpg.offline-session/6'}),'/joint/settings/'+id);
 assert.equal(modelPluginSettingsPath({id,history:{},format:'modelmirror.rpg.offline-session/4'}),'/history-settings/sessions/'+id);
 assert.equal(modelPluginSettingsPath({id}),null);
});


test('分支 HTTP：独立授权、原意图核对、新子路线与共享预算；拒绝客户端受信字段',async t=>{
 const f=await fixture(t,{scenario:true});await f.select('story');await f.send();const v=await f.view();
 const input={sessionId:id,operationId:'fork',expectedRevision:v.session.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,
 expectedContextPolicyRevision:v.effectivePolicy.revision,turn:1,name:'支线'};
 const no=await f.req(jp+'/branch',input);assert.notEqual(no.status,200);await f.enable('branch');
 for(const extra of [{system:'injected'},{history:[]},{providerUrl:'https://foreign.invalid'}])assert.notEqual((await f.req(jp+'/branch',{...input,...extra})).status,200);
 assert.equal((await f.req(jp+'/branch',input,{Origin:'https://foreign.invalid'})).status,403);
 const absent=await f.ok(jp+'/branch/reconcile',input);assert.equal(absent.state,'not_submitted');
 const made=await f.ok(jp+'/branch',input);assert.deepEqual(made.intent,input);assert.equal(made.session.turns.length,1);assert.equal(made.session.operations.length,0);
 const again=await f.ok(jp+'/branch',input);assert.equal(again.replayed,true);assert.equal(again.session.id,made.session.id);
 const checked=await f.ok(jp+'/branch/reconcile',input);assert.equal(checked.state,'completed');assert.equal(checked.session.id,made.session.id);
 const child=await f.ok(jp+'/settings/'+made.session.id);assert.equal(child.plugins.branch.enabled,false);assert.deepEqual(child.models,{story:null,summary:null,memory:null});
 assert.equal(child.budget.used,1);assert.equal(child.budget.limit,20);assert.equal(f.provider.dispatchCount,1);
 await f.reopen();assert.equal((await f.ok(jp+'/branch/reconcile',input)).state,'completed');assert.equal(f.provider.dispatchCount,0);
 await rejects(f,jp+'/settings/change',await f.intent('branch','disable','fork'),'OFFLINE_OPERATION_COLLISION');
});


test('第五插件设置：独立授权、模型回执、停用重装与原操作恢复不增加调用',async t=>{
 const f=await fixture(t);await f.enable('model');let s=await f.settings();
 assert.equal(s.plugins.model.enabled,true);assert.equal(s.models.story,null);
 for(const p of ['story','summary','memory'])await f.select(p);
 const configured=(await f.view()).session.models;
 assert.equal(configured.changes[0].pluginReceipt.pluginId,'rpg.model-selector');
 assert.equal(configured.changes[1].pluginReceipt,undefined);assert.equal(configured.changes[2].pluginReceipt,undefined);
 const budget=(await f.view()).budget;await f.change('model','disable');
 assert.equal((await f.settings()).plugins.model.enabled,false);assert.deepEqual((await f.view()).session.models,configured);
 await f.select('story');assert.equal((await f.view()).session.models.changes.at(-1).pluginReceipt,undefined);
 await f.change('model','enable');const oldEpoch=(await f.select('story')).change.pluginReceipt.epoch;
 await f.change('model','uninstall');await f.change('model','install');
 assert.equal((await f.settings()).plugins.model.enabled,false);await f.change('model','enable');
 assert.notEqual((await f.select('story')).change.pluginReceipt.epoch,oldEpoch);
 const before=canonical(await f.settings());await f.reopen();assert.equal(canonical(await f.settings()),before);
 assert.deepEqual((await f.view()).budget,budget);assert.equal(f.provider.dispatchCount,0);
});

test('第五插件设置回执丢失只核对原操作，停用不清除选择',async t=>{
 let armed=false;const f=await fixture(t,{fault:phase=>{if(armed&&phase==='afterChange'){armed=false;throw Error('lost model grant receipt');}}});
 await f.change('model','install');const intent=await f.intent('model','enable');armed=true;
 await rejects(f,jp+'/settings/change',intent,'JOINT_SETTINGS_RESULT_UNKNOWN');await f.reopen();
 assert.deepEqual((await f.settings()).unconfirmed,intent);const r=await f.ok(jp+'/settings/reconcile',intent);
 assert.equal(r.state,'applied');assert.equal(r.settings.plugins.model.enabled,true);assert.equal(r.settings.unconfirmed,null);
 assert.equal(f.provider.dispatchCount,0);
});


test('持续要求接口校验同源、版本、操作ID与受信字段，刷新重启不派发',async t=>{
 const f=await fixture(t),v=await f.view(),b={sessionId:id,operationId:'req',expectedRevision:v.session.revision,expectedRequirementsRevision:0,expectedContextPolicyRevision:v.effectivePolicy.revision,text:'保持第二人称'};
 assert.equal((await f.req(jp+'/requirements',b,{Origin:'https://untrusted.invalid'})).status,403);
 await rejects(f,jp+'/requirements',{...b,system:'injection'},'AUTHORING_UNKNOWN_FIELD');
 const r=await f.ok(jp+'/requirements',b);assert.equal(r.change.intent.text,b.text);assert.equal((await f.ok(jp+'/requirements',b)).replayed,true);
 await rejects(f,jp+'/requirements',{...b,operationId:'conflict'},'OFFLINE_REVISION_CONFLICT');
 await f.reopen();assert.equal((await f.view()).session.requirements.text,b.text);assert.equal(f.provider.dispatchCount,0);
});
