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
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6p-tests/',import.meta.url));await mkdir(root,{recursive:true});
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
 const doc=blankDocument();doc.metadata.name='独立作品 '+randomUUID();doc.metadata.audience='male';doc.sources.system=' FROZEN SYSTEM\r\n';doc.sources.prefix='PRE';doc.sources.suffix='POST';
 const work=await store.create({operationId:'work',expectedRevision:null,document:doc}),pub=await store.publish(work.workId,{operationId:'publish',expectedRevision:0}),reference={workId:work.workId,versionId:pub.result.versionId};
 const resolveRuntime=async ref=>{await options.beforeResolveRuntime?.();return openPublishedRuntime(store,ref);},modelControl=new OfflineModelControl(catalog);
 let host,control,provider,budget;
 async function open(faults=true){
  control=await createAuthoringHistoryControl({directory:join(directory,'plugins'),lookupSession:x=>host.lookupSession(x),resolveRuntime});
  provider=await OfflineFixtureProvider.open(join(directory,'provider'),{replies:replies(options),fault:faults?options.providerFault:undefined});
  budget=await JointBudget.open({directory:join(directory,'budget'),allowanceId:'shared',limit:options.limit??30,storageFault:faults?options.budgetFault:undefined});
  host=await JointSessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider,budget,modelControl,historyControl:control,fault:faults?options.fault:undefined,storageFault:faults?options.storageFault:undefined});
 }
 await open();await host.create({operationId:'session',expectedRevision:null,reference,openingText:opening,presetId:null});
 async function close(){await host.close();await control.close();await provider.close();await budget.close();}
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
 if(options.summary!==false)await f.enable(M2);if(options.memory!==false)await f.enable(M3);
 return f;
}
test('M3 原文更新落盘；全历史与单 system，独立模型和真实保留字段',async t=>{
 const f=await fixture(t,{summary:false});const estimate=await f.host.estimate(await f.input());
 assert.equal(estimate.plan.maximumCalls,2);assert.equal(f.provider.dispatchCount,0);
 await f.sent();const v=await f.read(),j=v.session.memory.jobs[0];
 assert.equal(v.session.turns.length,1);assert.equal(j.state,'applied');assert.equal(f.provider.dispatchCount,2);
 assert.equal(j.request.source.turns[0].user,opening);assert.ok(j.request.source.turns[0].assistant.includes('卡内总结'));
 assert.ok(!j.request.messages[1].content.includes('PRE'));assert.equal(j.request.modelChoice.entry.modelId,'fixture/memory');
 assert.equal(v.session.memory.snapshot.entries.length,1);assert.equal(v.budget.used,2);assert.equal(v.budget.reserved,0);
 const next=await f.sent('two','那封信呢？');assert.equal(next.operation.request.messages.filter(m=>m.role==='system').length,1);
 assert.ok(next.operation.request.messages[0].content.includes('港口承诺'));assert.equal(next.operation.request.messages.length,4);
});
test('M2 到 M3 顺序固定，原文分别覆盖；未使用压缩额度释放',async t=>{
 const order=[],f=await fixture(t,{providerFault:(phase,r)=>{if(phase==='afterAccepted')order.push(r.kind??'story');}});
 await f.sent();await f.sent('two','继续');
 assert.deepEqual(order,['story','memory','story','summary','memory']);
 const v=await f.read();assert.equal(v.session.summary.snapshot.versions[0].coveredThrough,1);assert.equal(v.session.memory.snapshot.processedThrough,2);
 assert.equal(v.budget.used,5);assert.equal(v.budget.remaining,25);assert.equal(v.budget.reserved,0);
 const memory=v.session.memory.jobs[1];assert.equal(memory.request.source.from,2);assert.equal(memory.request.source.through,2);
 assert.ok(!memory.request.messages[1].content.includes('两人有一个未兑现'));
});
test('M2 超长只压缩一次，然后 M3；同用途阶段有独立 ID',async t=>{
 const order=[],f=await fixture(t,{summaryText:'长'.repeat(10001),providerFault:(phase,r)=>{if(phase==='afterAccepted')order.push(r.kind??'story');}});
 await f.sent();const before=await f.host.estimate(await f.input('two','继续'));assert.equal(before.plan.maximumCalls,4);
 await f.sent('two','继续');assert.deepEqual(order,['story','memory','story','summary','compression','memory']);
 const v=await f.read();assert.equal(v.budget.used,6);assert.equal(v.session.summary.jobs[0].state,'applied');assert.equal(v.session.summary.jobs[0].receipt.result.text.length,10001);
});
test('共享预留不足时剧情也不开始，新建会话不能增额',async t=>{
 const f=await fixture(t,{limit:1});const r=await f.sent();assert.equal(r.run.state,'not_dispatched');assert.equal(r.run.diagnostic,'JOINT_BUDGET_EXHAUSTED');
 assert.equal(f.provider.dispatchCount,0);assert.equal((await f.read()).session.turns.length,0);assert.equal((await f.budget.status()).remaining,1);
 await f.host.create({operationId:'new-session',expectedRevision:null,reference:f.reference,openingText:opening,presetId:null});assert.equal((await f.budget.status()).limit,1);
});
test('记忆模型必须明确选择；更换剧情不会默认选择记忆',async t=>{
 const f=await fixture(t,{memoryModel:false});await reject(()=>f.send(),'MEMORY_MODEL_REQUIRED');assert.equal(f.provider.dispatchCount,0);
 await f.select('memory');await f.sent();assert.equal((await f.read()).session.memory.jobs[0].state,'applied');
});
test('原操作重复、重启和恢复不能再派发或改变抽取',async t=>{
 const f=await fixture(t);const i=await f.input();await f.host.send(i);await f.idle();const before=(await f.read()).session;
 assert.equal((await f.host.send(i)).replayed,true);assert.equal(f.provider.dispatchCount,2);
 await f.reopen();assert.deepEqual((await f.read()).session,before);assert.equal((await f.host.send(i)).replayed,true);assert.equal(f.provider.dispatchCount,0);
 assert.equal((await f.recover()).replayed,true);assert.equal(f.provider.dispatchCount,0);
});
test('M3 丢回执后未知保持计数；重启只查原 ID 并采用一次',async t=>{
 const f=await fixture(t,{providerFault:(phase,r)=>{if(phase==='afterReceipt'&&r.kind==='memory')throw Error('lost');}});
 await f.sent();let v=await f.read();assert.equal(v.session.memory.jobs[0].state,'unknown');assert.equal(v.budget.used,2);
 await reject(()=>f.send('two','继续'),'MEMORY_RESULT_PENDING');await f.reopen();const recovered=await f.recover();assert.equal(recovered.run.state,'paused');
 v=await f.read();assert.equal(v.session.memory.jobs[0].state,'applied');assert.equal(v.session.memory.snapshot.revision,1);assert.equal(v.budget.used,2);assert.equal(f.provider.dispatchCount,0);
 await f.recover();assert.equal(f.provider.dispatchCount,0);await f.sent('two','继续');assert.equal(f.provider.dispatchCount,3);
});
test('M2 未知暂停 M3；恢复摘要不启动下一阶段，明确更新才继续',async t=>{
 const f=await fixture(t,{providerFault:(phase,r)=>{if(phase==='afterReceipt'&&r.kind==='summary')throw Error('lost');}});
 await f.sent();await f.sent('two','继续');let v=await f.read();assert.equal(v.session.summary.jobs[0].state,'unknown');assert.equal(v.session.memory.jobs[1].state,'not_dispatched');
 assert.equal(v.budget.used,4);assert.equal(v.budget.reserved,0);await f.reopen();await f.recover('two');
 assert.equal(f.provider.dispatchCount,0);assert.equal((await f.read()).session.summary.jobs[0].state,'applied');
 await reject(()=>f.send('three','继续'),'MEMORY_UPDATE_FAILED');await f.update();assert.equal(f.provider.dispatchCount,1);assert.equal((await f.read()).session.memory.snapshot.processedThrough,2);
});
test('已扣额度尚未派发时崩溃，结果未知也不换 ID 重发',async t=>{
 let armed=true;const f=await fixture(t,{fault:(phase,r)=>{if(armed&&phase==='afterClaim'){armed=false;throw Error('crash');}}});
 await assert.rejects(f.send());await f.reopen();assert.equal(f.provider.dispatchCount,0);let r=await f.recover();assert.equal(r.run.state,'unknown');
 assert.equal((await f.read()).budget.used,1);assert.equal((await f.read()).budget.reserved,0);await f.recover();assert.equal(f.provider.dispatchCount,0);
});
test('预留前崩溃能确认为未派发，恢复不补预留',async t=>{
 const f=await fixture(t,{fault:phase=>{if(phase==='afterRunPrepared')throw Error('crash');}});
 await assert.rejects(f.send());await f.reopen();const r=await f.recover();assert.equal(r.run.state,'not_dispatched');assert.equal(r.run.diagnostic,'JOINT_NOT_RESERVED');assert.equal((await f.budget.status()).used,0);assert.equal(f.provider.dispatchCount,0);
});
test('M3 在途可撤权；迟到回执保留但不改条目，费用仍计数',async t=>{
 const entered=deferred(),release=deferred();t.after(()=>release.resolve());
 const f=await fixture(t,{providerFault:async(phase,r)=>{if(phase==='afterAccepted'&&r.kind==='memory'){entered.resolve();await release.promise;}}});
 await f.send();await entered.promise;await f.change('disable',M3);release.resolve();await f.idle();
 const v=await f.read();assert.equal(v.session.memory.jobs[0].state,'revoked');assert.ok(v.session.memory.jobs[0].receipt);assert.equal(v.session.memory.snapshot.revision,0);assert.equal(v.budget.used,2);
});
test('下一发送等待后台收束后重验 revision，关闭宿主不取消任务',async t=>{
 const entered=deferred(),release=deferred();t.after(()=>release.resolve());
 const f=await fixture(t,{providerFault:async(phase,r)=>{if(phase==='afterAccepted'&&r.kind==='memory'){entered.resolve();await release.promise;}}});
 const story=await f.send();await entered.promise;assert.equal(story.operation.state,'completed');
 let finished=false;const pending=f.host.send(await f.input('two','继续')).then(()=>{finished=true;},e=>{finished=true;assert.equal(e.code,'OFFLINE_REVISION_CONFLICT');});
 await new Promise(r=>setImmediate(r));assert.equal(finished,false);release.resolve();await pending;await f.idle();assert.equal(f.provider.dispatchCount,2);
});
test('M3 非法提案整批拒绝，旧条目与剧情保留；未知不能靠停用绕开',async t=>{
 const f=await fixture(t,{memoryText:'not JSON'});await f.sent();const v=await f.read();
 assert.equal(v.session.memory.jobs[0].state,'rejected');assert.equal(v.session.turns.length,1);assert.equal(v.session.memory.snapshot.revision,0);
 await reject(()=>f.send('two','继续'),'MEMORY_UPDATE_FAILED');await f.change('disable',M3);await f.sent('two','继续');assert.equal(f.provider.dispatchCount,4);
});
test('零插件路径不增加任务，消息前后置词只包裹一次',async t=>{
 const f=await fixture(t,{summary:false,memory:false});await f.sent();const r=await f.sent('two','继续');
 assert.equal(f.provider.dispatchCount,2);assert.equal((await f.read()).session.summary.jobs.length,0);assert.equal((await f.read()).session.memory.jobs.length,0);
 assert.equal(r.operation.request.messages.length,4);assert.equal((r.operation.request.messages[1].content.match(/PRE/g)??[]).length,1);assert.equal((await f.read()).budget.used,2);
});
test('停用后积累历史，再启用 M2+M3 最多预留六次',async t=>{
 const f=await fixture(t,{summaryText:'长'.repeat(10001)});await f.sent();await f.change('disable',M2);await f.change('disable',M3);
 await f.sent('two','第二');await f.sent('three','第三');await f.change('enable',M2);await f.change('enable',M3);
 const input=await f.input('four','第四'),quote=await f.host.estimate(input);assert.equal(quote.plan.maximumCalls,6);
 await f.host.send(input);await f.idle();const v=await f.read();assert.equal(v.session.turns.length,4);assert.equal(v.session.summary.snapshot.versions.length,2);assert.equal(v.session.memory.snapshot.processedThrough,4);assert.equal(v.budget.used,10);
 const before=v.session.summary.jobs[0],after=v.session.summary.jobs[1];assert.notEqual(before.operationId,after.operationId);assert.notEqual(before.compressionRequest.operationId,after.compressionRequest.operationId);
});
test('手工编辑保护且不调用模型，旧版本保持完整',async t=>{
 const f=await fixture(t,{summary:false});await f.sent();let v=await f.read();const entry=v.session.memory.snapshot.entries[0];
 await f.host.editMemory({sessionId:id,operationId:'edit',expectedRevision:v.session.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,
  change:{action:'update',id:entry.id,baseVersion:entry.version,value:{...entry.value,content:'人工核对的约定。'}}});
 v=await f.read();assert.equal(v.session.memory.snapshot.entries[0].protected,true);assert.equal(v.session.memory.snapshot.versions.length,2);assert.equal(f.provider.dispatchCount,2);await f.reopen();assert.equal((await f.read()).session.memory.snapshot.entries[0].protected,true);
});


test('historical story cannot be resealed with an older memory snapshot',async t=>{
 const f=await fixture(t,{summary:false});await f.sent();await f.sent('two','那封信呢？');
 const s=clone((await f.read()).session),op=s.operations[1],ctx=op.request.memoryContext;
 const prior={...s,revision:ctx.sessionRevision,models:ctx.models,turns:s.turns.slice(0,ctx.completedTurns)};
 const memory=memoryAt(s,s.memory.snapshot,0),base={system:op.request.worldbook.assembly.system,user:op.request.worldbook.assembly.user};
 const policy=memoryContextPolicy(prior,ctx.summarySnapshot,memory,{authorization:ctx.policy.authorization,historyConfig:ctx.policy.historyConfig});
 const forged=assembleMemoryContext({session:prior,summary:ctx.summarySnapshot,memory,authorization:policy.authorization,historyConfig:policy.historyConfig,base,input:op.request.rawInput,expectedPolicyRevision:policy.revision});
 op.request.memoryContext=forged.evidence;op.request.policy=forged.evidence.policy;op.request.messages=forged.messages;op.request.messagesHash=digest(forged.messages);op.request.capacity=modelCapacity(op.request.modelConfiguration.choice,forged.messages);
 op.intent.expectedContextPolicyRevision=policy.revision;op.intentHash=digest(op.intent);op.requestHash=digest(op.request);
 op.receipt.requestHash=op.requestHash;const {sha256:old,...body}=op.receipt;op.receipt.sha256=digest(body);s.turns[1].messagesHash=op.request.messagesHash;
 assert.throws(()=>validateJointSession(s,f.host.readiness.executionHash),e=>e.code==='JOINT_STORY_CORRUPT');
});

test('M1 selects one turn while M3 still recalls from full raw history',async t=>{
 const f=await fixture(t,{summary:false});await f.enable(H);const v=await f.read();
 await f.host.configureHistory({sessionId:id,operationId:'history-setting',expectedRevision:v.session.revision,expectedHistoryRevision:v.session.history.revision,
  expectedContextPolicyRevision:v.historyConfigurationPolicyRevision,config:{turns:1,includeInitialCharacter:false}});
 await f.sent();await f.sent('two','整理行李');const r=await f.sent('three','我去寻找那封信。');
 assert.equal(r.operation.request.messages.length,4);assert.ok(r.operation.request.messages[0].content.includes('港口承诺'));
 assert.equal(r.operation.request.messages[1].content,'PRE\n\n整理行李\n\nPOST');assert.ok(!r.operation.request.messages[2].content.includes('古老灯塔'));
 assert.equal(r.operation.request.memoryContext.recall.results.filter(item=>item.selected).length,1);
});
for(const action of ['disable','uninstall'])test('M3 unknown '+action+' still requires original recovery; no late application',async t=>{
 const f=await fixture(t,{providerFault:(phase,r)=>{if(phase==='afterReceipt'&&r.kind==='memory')throw Error('lost');}});
 await f.sent();await f.change(action,M3);await reject(()=>f.send('two','继续'),'MEMORY_RESULT_PENDING');
 await f.reopen();await f.recover();const v=await f.read();assert.equal(v.session.memory.jobs[0].state,'revoked');assert.equal(v.session.memory.snapshot.revision,0);
 assert.equal(v.budget.used,2);assert.equal(f.provider.dispatchCount,0);await f.sent('two','继续');assert.equal(f.provider.dispatchCount,2);
});
for(const phaseName of ['beforeCommit','afterCommit'])test('memory CAS '+phaseName+' failure recovers one original receipt without replay',async t=>{
 let armed=true;const f=await fixture(t,{storageFault:(phase,record)=>{
  if(armed&&phase===phaseName&&record.data.memory?.jobs.at(-1)?.state==='applied'){armed=false;throw Error('disk');}
 }});
 await f.sent();assert.equal(f.provider.dispatchCount,2);await f.reopen();await f.recover();
 const v=await f.read();assert.equal(v.session.memory.snapshot.revision,1);assert.equal(v.session.memory.jobs[0].state,'applied');assert.equal(v.budget.used,2);assert.equal(f.provider.dispatchCount,0);
});
test('budget result acknowledgement loss reconciles before adopting memory',async t=>{
 let armed=true;const f=await fixture(t,{budgetFault:(phase,record)=>{
  if(armed&&phase==='afterCommit'&&record.data.groups?.some(g=>g.stages.some(s=>s.purpose==='memory'&&s.state==='completed'))){armed=false;throw Error('ack');}
 }});
 await f.sent();await f.reopen();await f.recover();const v=await f.read();assert.equal(v.session.memory.snapshot.revision,1);assert.equal(v.budget.used,2);assert.equal(v.budget.reserved,0);assert.equal(f.provider.dispatchCount,0);
});
test('unknown compression resolves original result without dispatching M3',async t=>{
 const f=await fixture(t,{summaryText:'长'.repeat(10001),providerFault:(phase,r)=>{if(phase==='afterReceipt'&&r.kind==='compression')throw Error('lost');}});
 await f.sent();await f.sent('two','继续');const before=await f.read();assert.equal(before.session.summary.jobs[0].state,'compression_unknown');assert.equal(before.budget.used,5);
 await f.reopen();await f.recover('two');const v=await f.read();assert.equal(v.session.summary.jobs[0].state,'applied');assert.equal(v.session.summary.snapshot.versions[0].effectiveText,'压缩后：承诺尚未兑现。');
 assert.equal(v.session.memory.jobs[1].state,'not_dispatched');assert.equal(f.provider.dispatchCount,0);assert.equal(v.budget.used,5);
});
test('story durability failure retains raw result and recovers without starting background',async t=>{
 let armed=true;const f=await fixture(t,{storageFault:(phase,record)=>{
  if(armed&&phase==='beforeCommit'&&record.data.turns?.length===1){armed=false;throw Error('disk');}
 }});
 await assert.rejects(f.send(),e=>e.code==='OFFLINE_WRITE_FAILED');assert.equal(f.provider.dispatchCount,1);
 await f.reopen();await f.recover();const v=await f.read();assert.equal(v.session.turns.length,1);assert.equal(v.session.memory.jobs[0].state,'not_dispatched');assert.equal(v.budget.used,1);assert.equal(v.budget.reserved,0);assert.equal(f.provider.dispatchCount,0);
});
test('explicit stale memory revision is rejected before reservation or dispatch',async t=>{
 const f=await fixture(t);const input=await f.input();await reject(()=>f.host.send({...input,expectedMemoryRevision:1}),'MEMORY_REVISION_CONFLICT');assert.equal((await f.budget.status()).reserved,0);assert.equal(f.provider.dispatchCount,0);
});



test('manual memory duplicate binds the complete context-policy intent',async t=>{
 const f=await fixture(t,{summary:false});await f.sent();const v=await f.read(),entry=v.session.memory.snapshot.entries[0];
 const intent={sessionId:id,operationId:'edit-policy',expectedRevision:v.session.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,
  change:{action:'update',id:entry.id,baseVersion:entry.version,value:{...entry.value,content:'人工修订'}}};
 await f.host.editMemory(intent);assert.equal((await f.host.editMemory(intent)).replayed,true);
 await reject(()=>f.host.editMemory({...intent,expectedContextPolicyRevision:'b'.repeat(64)}),'MEMORY_OPERATION_COLLISION');
});


test('read crossing background completion stays busy instead of inventing unknown outcome',async t=>{
 const entered=deferred(),release=deferred(),readEntered=deferred(),readRelease=deferred();let blockRead=false;
 t.after(()=>{release.resolve();readRelease.resolve();});
 const f=await fixture(t,{summary:false,providerFault:async(phase,r)=>{if(phase==='afterAccepted'&&r.kind==='memory'){entered.resolve();await release.promise;}},
  beforeResolveRuntime:async()=>{if(blockRead){blockRead=false;readEntered.resolve();await readRelease.promise;}}});
 await f.send();await entered.promise;blockRead=true;const reading=f.read();await readEntered.promise;
 release.resolve();await f.idle();readRelease.resolve();const stale=await reading;
 assert.equal(stale.pending,true);assert.equal(stale.busy,true);assert.notEqual(stale.status,'unknown');
 const settled=await f.read();assert.equal(settled.busy,false);assert.equal(settled.pending,false);assert.equal(settled.session.runs[0].state,'completed');
 assert.equal(f.provider.dispatchCount,2);
});


async function requirementsIntent(f,operationId,text){const v=await f.read();return {sessionId:f.id,operationId,expectedRevision:v.session.revision,expectedRequirementsRevision:v.session.requirements.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,text};}
test('持续要求显式保存、版本冲突、重复操作和重启，不调用模型',async t=>{
 const f=await fixture(t,{summary:false,memory:false}),before=await f.read();
 const i=await requirementsIntent(f,'preferences','  输出详尽；保持二人称 🧭\n');
 const a=await f.host.configureRequirements(i);assert.equal(a.change.revision,1);assert.equal(f.provider.dispatchCount,0);
 assert.equal((await f.host.configureRequirements(i)).replayed,true);
 await reject(()=>f.host.configureRequirements({...i,text:'改写'}),'OFFLINE_OPERATION_COLLISION');
 await reject(()=>f.host.configureRequirements({...i,operationId:'stale'}),'OFFLINE_REVISION_CONFLICT');
 const saved=await f.read();assert.equal(saved.session.requirements.text,i.text);assert.notEqual(saved.effectivePolicy.revision,before.effectivePolicy.revision);
 await f.reopen();assert.deepEqual((await f.read()).session.requirements,saved.session.requirements);
 const bad=await requirementsIntent(f,'too-long','🧭'.repeat(8001));await reject(()=>f.host.configureRequirements(bad),'REQUIREMENTS_TEXT_LIMIT');
 assert.equal(f.provider.dispatchCount,0);assert.equal((await f.read()).budget.used,0);
});
test('M1裁剪不丢持续要求；历史不重复携带，M3仍读取原文，旧请求可重放校验',async t=>{
 const f=await fixture(t,{summary:false});await f.enable(H);let v=await f.read();
 await f.host.configureHistory({sessionId:f.id,operationId:'window',expectedRevision:v.session.revision,expectedHistoryRevision:v.session.history.revision,expectedContextPolicyRevision:v.historyConfigurationPolicyRevision,config:{turns:1,includeInitialCharacter:false}});
 await f.host.configureRequirements(await requirementsIntent(f,'requirements-1','小说模式关闭；回复冗长'));
 await f.sent();await f.sent('two','继续修书');
 await f.host.configureRequirements(await requirementsIntent(f,'requirements-2','小说模式关闭；回复冗长；美化开启'));
 await f.sent('three','查看钥匙约定');v=await f.read();
 const r=v.session.operations[2].request;
 assert.equal(r.messages.length,4);assert.match(r.messages.at(-1).content,/小说模式关闭；回复冗长；美化开启/);
 assert.equal(r.messages.slice(0,-1).some(m=>m.content.includes('【持续要求】')),false);
 assert.equal(r.rawInput,'查看钥匙约定');assert.equal(v.session.turns[2].user,'查看钥匙约定');
 assert.equal(v.session.memory.jobs[2].request.source.turns[0].user,'查看钥匙约定');
 assert.equal(v.session.operations[0].request.memoryContext.requirementsSnapshot.text,'小说模式关闭；回复冗长');
 assert.doesNotThrow(()=>validateJointSession(v.session,v.session.executionHash));
 const changed=clone(v.session);changed.operations[0].request.memoryContext.requirementsSnapshot.text='未来信息';assert.throws(()=>validateJointSession(changed,changed.executionHash));
 await f.reopen();assert.equal((await f.read()).session.requirements.revision,2);
});


for(const phase of ['beforeCommit','afterCommit'])test('持续要求落盘 '+phase+' 故障按原操作核对，无模型派发',async t=>{
 let armed=false;const f=await fixture(t,{summary:false,memory:false,storageFault:(point,{data})=>{if(armed&&point===phase&&data.requirements?.revision===1){armed=false;throw Error('fixture disk failure');}}});
 const i=await requirementsIntent(f,'req-disk','原文');armed=true;await assert.rejects(f.host.configureRequirements(i));
 await f.host.reconcileStorage();const v=await f.read();
 assert.equal(v.session.requirements.revision,phase==='afterCommit'?1:0);
 if(phase==='afterCommit')assert.equal((await f.host.configureRequirements(i)).replayed,true);
 assert.equal(f.provider.dispatchCount,0);assert.equal(v.budget.used,0);
});


test('持续要求与 M2+M3 独立装配，清空只影响以后请求',async t=>{
 const f=await fixture(t);
 await f.host.configureRequirements(await requirementsIntent(f,'req-combined-one','REQ_ONLY_CURRENT 保持第二人称'));
 await f.sent();await f.sent('two','继续修书');
 await f.host.configureRequirements(await requirementsIntent(f,'req-combined-two','REQ_ONLY_CURRENT 改为第三人称'));
 await f.sent('three','检查地图');let v=await f.read();
 const request=v.session.operations[2].request;
 assert.ok(request.messages[0].content.includes('历史摘要'));assert.ok(request.messages.at(-1).content.includes('REQ_ONLY_CURRENT 改为第三人称'));
 assert.equal(request.messages.slice(0,-1).some(m=>m.content.includes('REQ_ONLY_CURRENT')),false);
 for(const job of [...v.session.summary.jobs,...v.session.memory.jobs])if(job.request)assert.equal(JSON.stringify(job.request.messages).includes('REQ_ONLY_CURRENT'),false);
 await f.host.configureRequirements(await requirementsIntent(f,'req-combined-clear',''));
 await f.sent('four','保持等待');v=await f.read();
 assert.equal(v.session.operations[3].request.messages.at(-1).content,'PRE\n\n保持等待\n\nPOST');
 assert.equal(v.session.operations[2].request.memoryContext.requirementsSnapshot.text,'REQ_ONLY_CURRENT 改为第三人称');
 await f.reopen();assert.equal((await f.read()).session.requirements.text,'');
});
