import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {TurnDisplayJournal,turnDisplayView} from './turn-display.mjs';
import {AuthoringStore} from './store.mjs';
import {blankDocument,canonical,sha256} from './contracts.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {JointFixtureProvider} from './joint-fixture.mjs';
import {JointSessionHost} from './joint-session.mjs';
import {JointBudget} from './joint-budget.mjs';
import {BranchCheckpoints} from './branch-storage.mjs';
import {branchCheckpointId} from './branch-checkpoint.mjs';
import {branchChildId} from './branch-session.mjs';
import {OfflineModelControl} from './model-control.mjs';
import {createAuthoringHistoryControl} from './history-control.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6z-tests/',import.meta.url));await mkdir(root,{recursive:true});
const digest=x=>sha256(canonical(x)),clone=structuredClone,M2='rpg.rolling-summary',M3='rpg.memory-palace',B='rpg.branch-save',H='rpg.history-window';
const reject=(fn,code)=>assert.rejects(async()=>fn(),e=>{assert.equal(e.code,code,e.stack);return true;});
const reply=text=>({status:'completed',text,finishReason:'stop'}),opening='原始开局：青灯港来信';
const catalog={catalogId:'branch-fixture',models:['story','summary','memory'].map(id=>({selectionId:id,selectionRevision:sha256(id),modelId:'fixture/'+id,name:id,
 available:true,contextLength:256000,maxOutputTokens:16000,parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}}))};
const replies={story:reply('青灯港约定尚未兑现。<details><summary>原始记事</summary>完整原文</details>'),summary:reply('港口约定仍未兑现。'),compression:reply('港口约定未兑现。'),
 memory:reply('{"operations":[]}'),memoryFirst:reply(JSON.stringify({operations:[{type:'create',name:'港口约定',keywords:['青灯港'],match:'any',roles:['user','assistant'],content:'计划明天见面，尚未兑现。',sourceTurns:[1]}]}))};
async function fixture(t,options={}){
 const directory=await mkdtemp(join(root,'run-'));let store=await AuthoringStore.open(join(directory,'works'));
 const d=blankDocument();d.metadata.name='独立分支样例';d.metadata.audience='male';d.sources.system='原系统不动';d.sources.prefix='PRE';d.sources.suffix='POST';
 if(options.media){d.enhancements.cg.entries=[{id:'port',name:'青灯港',categoryId:null,enabled:true,roles:['assistant'],rule:'or',keywords:['青灯港'],target:'background',mode:'weighted',images:[{alias:'a',weight:1},{alias:'b',weight:1}]}];d.settings.media.bgm='quiet';}
 const w=await store.create({operationId:'work',expectedRevision:null,document:d});
 if(options.media){
  const a=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII=','base64');
  for(const [i,alias]of ['a','b','quiet'].entries())await store.putAsset(w.workId,{operationId:'asset-'+alias,expectedRevision:i,alias,fileName:alias+(i===2?'.mp3':'.png'),mediaType:i===2?'audio/mpeg':'image/png',bytes:i===2?Buffer.concat([Buffer.from('ID3'),Buffer.alloc(10)]):a});
 }
 const p=await store.publish(w.workId,{operationId:'publish',expectedRevision:options.media?3:0}),reference={workId:w.workId,versionId:p.result.versionId};
 const resolveRuntime=ref=>openPublishedRuntime(store,ref),modelControl=new OfflineModelControl(catalog);let host,control,provider,budget,branches;
 async function open(faults=true){
  control=await createAuthoringHistoryControl({directory:join(directory,'plugins'),lookupSession:id=>host.lookupSession(id),resolveRuntime});
  provider=await JointFixtureProvider.open(join(directory,'provider'),{replies,fault:faults?options.providerFault:undefined});
  budget=await JointBudget.open({directory:join(directory,'budget'),allowanceId:'shared',limit:30});
  branches=await BranchCheckpoints.open({directory:join(directory,'branches'),storageFault:faults?options.branchStorageFault:undefined});
  host=await JointSessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider,budget,modelControl,historyControl:control,branchStore:branches,
   fault:faults?options.fault:undefined,storageFault:faults?options.storageFault:undefined});
 }
 await open();const made=await host.create({operationId:'parent',expectedRevision:null,reference,openingText:opening,presetId:null}),parent=made.session.id;
 async function close(){await host.close();await control.close();await provider.close();await budget.close();await branches.close();}
 t.after(async()=>{await close();await store.close();});
 const f={directory,parent,resolveRuntime,get host(){return host;},get branches(){return branches;},get provider(){return provider;},get budget(){return budget;},get service(){return control.pluginService;},
 read:(id=parent)=>host.read(id),
 async select(id,purpose){const s=(await f.read(id)).session,c=modelControl.catalog();return host.configureModel({sessionId:id,operationId:randomUUID(),expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,
 choice:{catalogRevision:c.revision,selectionId:purpose,selectionRevision:sha256(purpose),parameters:{maxOutputTokens:4096}}});},
 async change(id,action,pluginId){const c=await f.service.catalog(),p=c.plugins.find(x=>x.id===pluginId);return f.service.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,
 ...(['enable','disable'].includes(action)?{sessionId:id,expectedSessionRevision:(await host.lookupSession(id)).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},
 async enable(id,pluginId){if(!(await f.service.sessionStatus(id,pluginId)).installed)await f.change(id,'install',pluginId);await f.change(id,'enable',pluginId);},
 async input(id,operationId,input){const v=await f.read(id);return {sessionId:id,operationId,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,
 expectedContextPolicyRevision:v.effectivePolicy.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,input};},
 async send(id,operationId,input){const r=await host.send(await f.input(id,operationId,input));await host.waitForIdle(id);return r;},
 async branch(id=parent,turn=1,operationId='fork'){const v=await f.read(id);return {sessionId:id,operationId,expectedRevision:v.session.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,
 expectedMemoryRevision:v.session.memory.snapshot.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,turn,name:'新的路线'};},
 async reopen(){await close();await store.close();store=await AuthoringStore.open(join(directory,'works'));await open(false);},
 };
 for(const p of ['story','summary','memory'])await f.select(parent,p);
 for(const p of [M2,M3,B])await f.enable(parent,p);
 return f;
}
async function edit(f,id,text){const v=await f.read(id),entry=v.session.memory.snapshot.entries[0];return f.host.editMemory({sessionId:id,operationId:randomUUID(),expectedRevision:v.session.revision,
 expectedMemoryRevision:v.session.memory.snapshot.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,change:{action:'update',id:entry.id,baseVersion:entry.version,value:{...entry.value,content:text}}});}

test('分支点封存 → 子路线独立选模授权 → 继续剧情/M2/M3 → 重启，预算不因分支增加',async t=>{
 const f=await fixture(t);await f.send(f.parent,'one',opening);await edit(f,f.parent,'在第二轮之前人工核实。');await f.send(f.parent,'two','青灯港后来呢');
 const before=await f.read(),intent=await f.branch(f.parent,2),made=await f.host.fork(intent),id=made.session.id;
 assert.equal(made.session.format,'modelmirror.rpg.offline-session/6');assert.equal(made.session.turns.length,2);assert.equal(made.session.operations.length,0);
 assert.equal(made.session.summary.jobs.length,0);assert.equal(made.session.memory.jobs.length,0);assert.equal(made.session.models.revision,0);
 assert.equal(made.session.memory.snapshot.entries[0].protected,true);assert.equal(made.session.memory.snapshot.entries[0].value.content,'在第二轮之前人工核实。');
 assert.equal(made.session.summary.snapshot.versions[0].kind,'inherited');assert.equal(made.session.memory.snapshot.versions[0].kind,'inherited');
 for(const p of [H,M2,M3,B,'rpg.model-selector'])assert.equal((await f.service.sessionStatus(id,p)).enabled,false);
 assert.deepEqual((await f.read()).session,before.session);assert.deepEqual((await f.read(id)).budget,before.budget);assert.equal(f.provider.dispatchCount,5);
 await reject(()=>f.send(id,'no-model','青灯港'),'MODEL_SELECTION_REQUIRED');
 await edit(f,f.parent,'父路线之后的未来信息。');assert.equal((await f.read(id)).session.memory.snapshot.entries[0].value.content,'在第二轮之前人工核实。');
 for(const p of ['story','summary','memory'])await f.select(id,p);
 await f.enable(id,M2);await f.enable(id,M3);const result=await f.send(id,'continue','青灯港约定');
 const child=(await f.read(id)).session;assert.equal(child.turns.length,3);assert.equal(child.memory.jobs[0].state,'applied');assert.equal(child.memory.jobs[0].request.source.from,3);
 assert.equal(child.summary.jobs[0].state,'applied');assert.equal(child.summary.snapshot.versions.at(-1).coveredThrough,2);assert.equal(f.provider.dispatchCount,8);
 assert.ok(result.operation.request.messages[0].content.includes('在第二轮之前人工核实。'));assert.ok(!canonical(result).includes('父路线之后的未来信息。'));
 await f.enable(id,B);const nested=await f.host.fork(await f.branch(id,3,'nested'));assert.equal(nested.session.turns.length,3);assert.equal(nested.session.memory.snapshot.processedThrough,3);
 assert.equal((await f.budget.status()).limit,30);assert.equal((await f.budget.status()).used,8);
 await f.reopen();assert.equal(f.provider.dispatchCount,0);assert.deepEqual((await f.read(id)).session,child);assert.equal((await f.host.fork(intent)).session.id,id);
 assert.equal((await f.read(nested.session.id)).session.turns.length,3);assert.equal(f.provider.dispatchCount,0);
});

test('同 ID 重复返回原子路线；修改意图或复用为其他任务均拒绝',async t=>{
 const f=await fixture(t);await f.send(f.parent,'one',opening);const input=await f.branch(),made=await f.host.fork(input);
 assert.equal((await f.host.fork(input)).replayed,true);assert.equal((await f.host.fork(input)).session.id,made.session.id);
 await reject(()=>f.host.fork({...input,name:'另一条路线'}),'BRANCH_OPERATION_COLLISION');
 await reject(()=>f.send(f.parent,'fork','青灯港'),'OFFLINE_OPERATION_COLLISION');
 const v=await f.read();await reject(()=>f.host.configureModel({sessionId:f.parent,operationId:'fork',expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,purpose:'story',choice:null}),'OFFLINE_OPERATION_COLLISION');
 assert.equal(f.provider.dispatchCount,2);assert.equal((await f.budget.status()).used,2);
});

for(const phase of ['afterBranchCheckpoint','branchBeforeChildSave','branchAfterChildSave'])test(phase+' 中断后仅原 ID 明确恢复，重启不自动建子路线或派发',async t=>{
 let armed=true;const f=await fixture(t,{fault:p=>{if(armed&&p===phase){armed=false;throw Error('branch disk interruption');}}});
 await f.send(f.parent,'one',opening);const input=await f.branch();await assert.rejects(f.host.fork(input));
 const sealed=await f.branches.read(branchCheckpointId(f.parent,'fork')),id=branchChildId(sealed),budget=await f.budget.status();
 await f.reopen();assert.equal(f.provider.dispatchCount,0);
 if(phase!=='branchAfterChildSave')await reject(()=>f.read(id),'OFFLINE_SESSION_NOT_FOUND');
 await f.change(f.parent,'disable',B);const recovered=await f.host.fork(input);assert.equal(recovered.session.id,id);assert.equal(recovered.replayed,true);
 assert.equal((await f.service.sessionStatus(id,B)).enabled,false);assert.deepEqual(await f.budget.status(),budget);assert.equal(f.provider.dispatchCount,0);
});

test('未知原结果不能靠分支绕开，停用也不能；只核对原操作',async t=>{
 const f=await fixture(t,{providerFault:(phase,r)=>{if(phase==='afterReceipt'&&r.kind==='memory')throw Error('lost receipt');}});
 await f.send(f.parent,'one',opening);
 const input=await f.branch();await reject(()=>f.host.fork(input),'MEMORY_RESULT_PENDING');await f.change(f.parent,'disable',M3);
 await reject(()=>f.host.fork(input),'MEMORY_RESULT_PENDING');assert.equal(f.provider.dispatchCount,2);
});

test('存储中的来源投影重新计算 hash 仍须核对宿主封存记录',async t=>{
 const f=await fixture(t);await f.send(f.parent,'one',opening);const made=await f.host.fork(await f.branch()),id=made.session.id;
 await f.host.close();const path=join(f.directory,'sessions','records',id+'.json'),record=JSON.parse(await readFile(path,'utf8'));
 // Alter a model suggestion: it is not a grant, but may not impersonate a seal.
 record.data.origin.inheritance.settings.modelSuggestions.story=null;
 const {sha256:ignored,...body}=record.data.origin;record.data.origin.sha256=digest(body);record.sha256=digest(record.data);
 await writeFile(path,canonical(record)+'\n');await f.reopen();await assert.rejects(f.read(id));assert.equal(f.provider.dispatchCount,0);
});


test('未授权或提案后撤权，不产生封存、子会话或调用',async t=>{
 let revoke=false,f;f=await fixture(t,{fault:async phase=>{if(phase==='branchPrepared'&&revoke){revoke=false;await f.change(f.parent,'disable',B);}}});
 await f.send(f.parent,'one',opening);await f.change(f.parent,'disable',B);
 await assert.rejects(f.host.fork(await f.branch()));assert.equal(await f.branches.read(branchCheckpointId(f.parent,'fork')),null);
 await f.change(f.parent,'enable',B);revoke=true;await assert.rejects(f.host.fork(await f.branch()));
 assert.equal(await f.branches.read(branchCheckpointId(f.parent,'fork')),null);assert.equal(f.provider.dispatchCount,2);
});


test('从继承的祖先回合继续分支，回到该回合封存状态，不带入父路线后来的事实',async t=>{
 const f=await fixture(t);await f.send(f.parent,'one',opening);await f.send(f.parent,'two','第二轮');
 const child=await f.host.fork(await f.branch(f.parent,2,'child'));await f.enable(child.session.id,B);
 const past=await f.host.fork(await f.branch(child.session.id,1,'past'));
 assert.equal(past.session.turns.length,1);assert.equal(past.session.summary.snapshot.versions.length,0);assert.equal(past.session.memory.snapshot.processedThrough,1);
 assert.equal(past.session.origin.parentSessionId,child.session.id);assert.equal(f.provider.dispatchCount,5);
 const sealed=await f.branches.read(past.checkpointId);assert.equal(sealed.format,'modelmirror.rpg.authored-branch-checkpoint/2');assert.equal(sealed.boundary.sourceSessionId,f.parent);
 await f.enable(past.session.id,B);const again=await f.host.fork(await f.branch(past.session.id,1,'again'));
 assert.deepEqual(again.session.origin.inheritance.turns,past.session.origin.inheritance.turns);assert.equal(f.provider.dispatchCount,5);
 await f.reopen();assert.equal((await f.read(again.session.id)).session.turns.length,1);assert.equal(f.provider.dispatchCount,0);
});


test('子路线 CG 与背景/BGM 完全继承已保存结果；刷新、重启和祖先回放不重新抽取',async t=>{
 const f=await fixture(t,{media:true});await f.send(f.parent,'one',opening);await f.send(f.parent,'two','继续');
 let seeds=0;const directory=join(f.directory,'display'),open=()=>TurnDisplayJournal.open({directory,resolveRuntime:f.resolveRuntime,resolveBranchSource:(id,n)=>f.host.branchDisplaySource(id,n),seed:()=>sha256('seed-'+(++seeds))});
 let journal=await open();try{
  const parent=(await f.read()).session;const one=(await journal.capture(parent,1)).record,two=(await journal.capture(parent,2)).record;
  const child=(await f.host.fork(await f.branch(f.parent,2))).session;
  for(const [n,original]of [[1,one],[2,two]]){const inherited=(await journal.capture(child,n)).record;
   assert.deepEqual(inherited.result,original.result);assert.equal(inherited.seed,original.seed);assert.equal(inherited.origin.sessionId,parent.id);assert.deepEqual(turnDisplayView(inherited,'mobile').plan,turnDisplayView(original,'mobile').plan);}
  assert.equal(seeds,2);await journal.close();journal=await open();await f.reopen();
  const reread=(await f.read(child.id)).session;assert.equal((await journal.capture(reread,2)).replayed,true);assert.equal(seeds,2);
  await f.select(child.id,'story');await f.send(child.id,'third','继续走向青灯港');const after=(await f.read(child.id)).session;
  const third=(await journal.capture(after,3)).record;assert.equal(third.state,'ready');assert.equal(seeds,3);assert.deepEqual((await journal.read(child.id,1)).result,one.result);
  assert.equal(third.result.bgm.alias,'quiet');assert.equal((await f.budget.status()).used,6);
 }finally{await journal.close();}
});

test('分支原回合展示尚未落盘时明确阻断，不能为子路线偷偷重抽',async t=>{
 const f=await fixture(t,{media:true});await f.send(f.parent,'one',opening);const child=(await f.host.fork(await f.branch())).session;let seeds=0;
 const journal=await TurnDisplayJournal.open({directory:join(f.directory,'display'),resolveRuntime:f.resolveRuntime,resolveBranchSource:(id,n)=>f.host.branchDisplaySource(id,n),seed:()=>{seeds++;return 'a'.repeat(64);}});
 try{await reject(()=>journal.capture(child,1),'TURN_DISPLAY_ORIGIN_REQUIRED');assert.equal(seeds,0);assert.equal(await journal.read(child.id,1),null);}
 finally{await journal.close();}
});


test('第五插件真实提案接入模型配置，重复操作只回读，子路线不继承选择或授权',async t=>{
 const f=await fixture(t),P='rpg.model-selector';await f.enable(f.parent,P);
 const before=await f.read();await f.select(f.parent,'story');const after=await f.read();
 const change=after.session.models.changes.at(-1);assert.equal(change.pluginReceipt.pluginId,P);
 assert.equal(change.pluginReceipt.sessionRevision,before.session.revision);assert.equal(f.provider.dispatchCount,0);
 const replay=await f.host.configureModel(change.intent);assert.equal(replay.replayed,true);assert.equal((await f.read()).session.revision,after.session.revision);
 await f.host.modelCatalog(f.parent);await f.send(f.parent,'plugin-first',opening);const fork=await f.host.fork(await f.branch());
 assert.equal((await f.service.sessionStatus(fork.session.id,P)).enabled,false);assert.equal(fork.session.models.current.story,null);
 await f.reopen();assert.equal((await f.read()).session.models.changes.at(-1).pluginReceipt.pluginId,P);
});

test('宿主模型插件提案后撤权不能保存迟到选择，既有模型保留',async t=>{
 let f;f=await fixture(t,{fault:async phase=>{if(phase==='modelPluginPrepared')await f.change(f.parent,'disable','rpg.model-selector');}});
 await f.enable(f.parent,'rpg.model-selector');const before=await f.read();
 await assert.rejects(f.select(f.parent,'story'),/PLUGIN_NOT_AUTHORIZED|PLUGIN_RESULT_STALE/);
 const after=await f.read();assert.deepEqual(after.session.models,before.session.models);assert.equal(after.session.revision,before.session.revision);assert.equal(f.provider.dispatchCount,0);
});


test('持续要求随分支点封存，子路线不读取父路线后来修订',async t=>{
 const f=await fixture(t),save=async(id,op,text)=>{const v=await f.read(id);return f.host.configureRequirements({sessionId:id,operationId:op,expectedRevision:v.session.revision,expectedRequirementsRevision:v.session.requirements.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,text});};
 await save(f.parent,'pref-before','小说关闭；冗长');await f.send(f.parent,'one',opening);
 await save(f.parent,'pref-after','未来修订，不可继承');
 const child=(await f.host.fork(await f.branch())).session;
 assert.equal(child.requirements.text,'小说关闭；冗长');assert.equal(child.requirements.revision,0);assert.deepEqual(child.requirements.changes,[]);
 await f.reopen();assert.equal((await f.read(child.id)).session.requirements.text,'小说关闭；冗长');
 await save(child.id,'child-pref','子路线独立要求');assert.equal((await f.read(f.parent)).session.requirements.text,'未来修订，不可继承');
});
