import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile,symlink,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {AuthoringStore} from './store.mjs';
import {blankDocument,canonical,sha256} from './contracts.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {OfflineSessionHost,OfflineFixtureProvider,HISTORY_OFFLINE_SESSION_FORMAT} from './offline-session.mjs';
import {OfflineModelControl} from './model-control.mjs';
import {createAuthoringHistoryControl,requireHistoryControl,HISTORY_PLUGIN_ID as H,HISTORY_MODE} from './history-control.mjs';

const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6f-tests/',import.meta.url));await mkdir(root,{recursive:true});
const digest=value=>sha256(canonical(value));
const reject=(action,code)=>assert.rejects(async()=>action(),error=>error.code===code);
const reply=text=>({status:'completed',text,finishReason:'stop'});
const replies={one:reply('古老灯塔🐟\r\n<details>完整原文一</details>'),two:reply('完整原文二'),three:reply('完整原文三'),four:reply('完整原文四'),five:reply('完整原文五')};
const opening='  原始角色开局🐟\r\n';
const catalog={catalogId:'offline-history-fixture',models:[{selectionId:'route',selectionRevision:sha256('route'),modelId:'fixture/story',name:'M1离线样例',available:true,contextLength:128000,maxOutputTokens:32000,parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:32000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}}]};
const worldbook={id:'old-ai',name:'旧AI原文命中',enabled:true,categoryId:null,roles:['assistant'],rule:'or',regex:false,keywords:['古老灯塔'],targets:['system'],order:0,depth:20,chance:100,mode:'regular',body:'OLD_FACT',contents:[]};
async function fixture(t,options={}) {
  const directory=await mkdtemp(join(root,'run-'));
  let store=await AuthoringStore.open(join(directory,'works'));
  const document=blankDocument();document.metadata.name='自由作品 '+randomUUID();document.metadata.audience='male';
  document.sources.system=' FROZEN SYSTEM\r\n';document.sources.prefix='PRE';document.sources.suffix='POST';document.settings.defaultMemory=1;
  document.worldbook.entries=[structuredClone(worldbook)];if(options.document)options.document(document);
  const work=await store.create({operationId:'work',expectedRevision:null,document});
  const pub=await store.publish(work.workId,{operationId:'publish',expectedRevision:0});
  const reference={workId:work.workId,versionId:pub.result.versionId};
  const resolveRuntime=async ref=>{await options.onResolve?.();return openPublishedRuntime(store,ref);}, modelControl=new OfflineModelControl(options.catalog??catalog);
  let host,historyControl,provider;
  async function open(recoverDeadOwner=false, faults=true){
    historyControl=await createAuthoringHistoryControl({directory:join(directory,'plugins'),lookupSession:id=>host.lookupSession(id),resolveRuntime,recoverDeadOwner});
    provider=await OfflineFixtureProvider.open(join(directory,'provider'),{replies:options.replies??replies,recoverDeadOwner,fault:faults?options.providerFault:undefined});
    host=await OfflineSessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider,modelControl,historyControl,recoverDeadOwner,
      fault:faults?options.fault:undefined,storageFault:faults?options.storageFault:undefined,draw:options.draw});
  }
  await open();
  const creation={operationId:'session',expectedRevision:null,reference,openingText:opening,presetId:null};
  const id=(await host.create(creation)).session.id;
  async function close(){await host.close();await historyControl.close();await provider.close();}
  t.after(async()=>{await close();await store.close();});
  const f={directory,reference,creation,id,document,modelControl,resolveRuntime,get store(){return store;},get host(){return host;},get control(){return historyControl;},get service(){return historyControl.pluginService;},get provider(){return provider;},
    async select(){const s=(await host.read(id)).session,c=modelControl.catalog();return host.configureModel({sessionId:id,operationId:'model',expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose:'story',choice:{catalogRevision:c.revision,selectionId:'route',selectionRevision:sha256('route'),parameters:{maxOutputTokens:4096}}});},
    async historyInput(config={turns:1,includeInitialCharacter:false},operationId=randomUUID()){const v=await host.read(id);return {sessionId:id,operationId,expectedRevision:v.session.revision,expectedHistoryRevision:v.session.history.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,config};},
    async configure(...args){return host.configureHistory(await f.historyInput(...args));},
    async input(operationId='one',input=opening){const v=await host.read(id);return {sessionId:id,operationId,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,input};},
    async send(...args){return host.send(await f.input(...args));},
    async recover(operationId='one'){return host.recover({sessionId:id,operationId,expectedRevision:(await host.read(id)).session.revision});},
    async change(action,pluginId=H,sessionId=id){const c=await f.service.catalog(),p=c.plugins.find(p=>p.id===pluginId);return f.service.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(['enable','disable'].includes(action)?{sessionId,expectedSessionRevision:(await host.lookupSession(sessionId)).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},
    async enable(pluginId=H){await f.change('install',pluginId);await f.change('enable',pluginId);},
    async close(){await close();await store.close();},
    async reopen(recoverDeadOwner=false){await close();await store.close();store=await AuthoringStore.open(join(directory,'works'),{recoverDeadOwner});await open(recoverDeadOwner,false);},
  };
  if(options.select!==false)await f.select();return f;
}

test('受信控制器、新格式和默认完整历史；作品默认长度不是授权',async t=>{
  assert.throws(()=>requireHistoryControl({authorization:()=>({enabled:true})}),{code:'HISTORY_CONTROL_HOST_REQUIRED'});
  const f=await fixture(t),v=await f.host.read(f.id);
  assert.equal(v.session.format,HISTORY_OFFLINE_SESSION_FORMAT);assert.equal(v.effectivePolicy.history,'full');assert.equal(v.session.history.revision,0);
  await f.send();await f.send('two','继续');const r=(await f.send('three','再继续')).operation.request;
  assert.equal(r.messages.length,6);assert.deepEqual(r.policy.selection.selectedTurns,[1,2]);assert.equal(r.policy.pluginExecution,false);
  assert.equal(f.host.readiness.historyWindow,true);assert.equal(f.host.readiness.memoryExecution,false);assert.equal(f.host.readiness.runtimeReady,false);
  await reject(()=>OfflineSessionHost.open({directory:join(f.directory,'fake'),resolveRuntime:f.resolveRuntime,provider:f.provider,modelControl:f.modelControl,historyControl:{}}),'HISTORY_CONTROL_HOST_REQUIRED');
  await reject(()=>OfflineSessionHost.open({directory:join(f.directory,'no-model'),resolveRuntime:f.resolveRuntime,provider:f.provider,historyControl:f.control}),'MODEL_CONTROL_REQUIRED');
});

test('安装不等于会话授权；配置只消费真实插件提案且不调用模型',async t=>{
  const f=await fixture(t);await reject(async()=>f.configure(),'PLUGIN_NOT_INSTALLED');await f.change('install');
  await reject(async()=>f.configure(),'PLUGIN_NOT_AUTHORIZED');await f.change('enable');
  const configured=await f.configure(),v=await f.host.read(f.id);
  assert.equal(configured.change.receipt.pluginId,H);assert.equal(configured.change.receipt.proposal.turns,1);
  assert.equal(v.effectivePolicy.history,'window');assert.equal(f.provider.dispatchCount,0);
  assert.equal(Object.hasOwn(configured.change.receipt,'ticket'),false);
});

test('真实离线请求只留最近完整回合；世界书仍命中窗口外原文，前后置词只装配一次',async t=>{
  const f=await fixture(t);await f.send();await f.send('two','第二轮原始输入');await f.enable();await f.configure();
  const before=(await f.host.read(f.id)).session.turns,r=(await f.send('three','第三轮原始输入')).operation.request;
  assert.deepEqual(r.policy.selection.selectedTurns,[2]);assert.equal(r.messages.length,4);
  assert.ok(r.messages[0].content.includes('OLD_FACT'));assert.ok(!r.messages[0].content.includes('完整原文一'));
  assert.equal(r.messages[1].content,before[1].userMessage);assert.equal(r.messages[2].content,before[1].assistant);
  assert.equal((r.messages[1].content.match(/PRE/g)??[]).length,1);assert.equal((r.messages[1].content.match(/POST/g)??[]).length,1);
  assert.equal(r.rawInput,'第三轮原始输入');assert.equal(r.messagesHash,digest(r.messages));
  assert.deepEqual((await f.host.read(f.id)).session.turns.slice(0,2),before);
  await writeFile(join(f.directory,'captured-request.json'),JSON.stringify(r,null,2));
});

test('显式开局快照只添加到发送副本；关闭后完整历史恢复',async t=>{
  const f=await fixture(t);await f.send();await f.send('two','第二轮');await f.enable();await f.configure({turns:1,includeInitialCharacter:true});
  const before=(await f.host.read(f.id)).session.turns,r=(await f.send('three','第三轮')).operation.request;
  assert.equal(r.policy.selection.initialSnapshotAdded,true);assert.equal(r.messages[1].content,'开局角色资料（原始快照）\n'+opening+'\n\n'+before[1].userMessage);
  assert.deepEqual((await f.host.read(f.id)).session.turns.slice(0,2),before);
  await f.change('disable');const full=(await f.send('four','第四轮')).operation.request;
  assert.equal(full.policy.history,'full');assert.deepEqual(full.policy.selection.selectedTurns,[1,2,3]);assert.equal(full.policy.selection.initialSnapshotAdded,false);
  assert.equal((await f.host.read(f.id)).session.history.config.includeInitialCharacter,true);
});

test('卸载和重装保留配置但不恢复授权；新会话和复制作品都不继承授权',async t=>{
  const f=await fixture(t);await f.enable();await f.configure();await f.change('uninstall');
  assert.equal((await f.host.read(f.id)).effectivePolicy.history,'full');await f.change('install');
  assert.equal((await f.host.read(f.id)).effectivePolicy.history,'full');assert.equal((await f.host.read(f.id)).session.history.config.turns,1);
  const child=(await f.host.create({...f.creation,operationId:'new-session'})).session;
  assert.equal((await f.service.historyAuthorization(child.id)).enabled,false);assert.equal(child.history.revision,0);
  await f.change('enable');assert.equal((await f.host.read(f.id)).effectivePolicy.history,'window');
  const copied=await f.store.copy(f.reference.workId,{operationId:'copy-work',expectedRevision:1,name:'新副本'});
  const pub=await f.store.publish(copied.workId,{operationId:'copy-publish',expectedRevision:0});
  const copySession=(await f.host.create({...f.creation,operationId:'copy-session',reference:{workId:copied.workId,versionId:pub.result.versionId}})).session;
  assert.equal((await f.service.historyAuthorization(copySession.id)).enabled,false);assert.equal(copySession.history.revision,0);
  assert.equal(copySession.models.current.story,null);assert.equal(f.provider.dispatchCount,0);
});

for(const pluginId of ['rpg.rolling-summary','rpg.memory-palace'])test('未接执行的任务插件显式阻断：'+pluginId,async t=>{
  const f=await fixture(t);await f.change('install',pluginId);await f.send();await f.change('enable',pluginId);
  assert.equal((await f.host.read(f.id)).effectivePolicy.blocked,true);await reject(()=>f.send('two','继续'),'AUTHORING_PLUGIN_EXECUTION_UNAVAILABLE');
  assert.equal(f.provider.dispatchCount,1);assert.equal((await f.host.read(f.id)).session.operations.length,1);
  await f.change('disable',pluginId);await f.send('two','继续');assert.equal(f.provider.dispatchCount,2);
});

test('配置与发送均绑定最新上下文策略；旧标签不静默改变授权',async t=>{
  const f=await fixture(t);await f.enable();const stale=await f.historyInput(),send=await f.input();await f.change('disable');
  await reject(()=>f.host.send(send),'HISTORY_POLICY_CONFLICT');await f.change('enable');
  await reject(()=>f.host.configureHistory(stale),'HISTORY_POLICY_CONFLICT');assert.equal(f.provider.dispatchCount,0);
  const config=await f.historyInput();await f.host.configureHistory(config);
  await reject(()=>f.host.configureHistory({...config,operationId:'stale'}),'OFFLINE_REVISION_CONFLICT');
  const fresh=await f.historyInput();await reject(()=>f.host.configureHistory({...fresh,expectedHistoryRevision:0}),'HISTORY_REVISION_CONFLICT');
  for(const bad of [{turns:0,includeInitialCharacter:false},{turns:51,includeInitialCharacter:false},{turns:1.2,includeInitialCharacter:false},{turns:1,includeInitialCharacter:'true'}]){
    await reject(()=>f.host.configureHistory({...fresh,config:bad}),'HISTORY_CONFIGURATION_INVALID');
  }
});

test('配置、剧情、模型操作 ID 隔离；原操作恢复只返回原记录',async t=>{
  const f=await fixture(t);await f.enable();const input=await f.historyInput(undefined,'configure');await f.host.configureHistory(input);
  assert.equal((await f.host.configureHistory(input)).replayed,true);await f.change('disable');assert.equal((await f.host.configureHistory(input)).replayed,true);
  await reject(()=>f.host.configureHistory({...input,config:{turns:2,includeInitialCharacter:false}}),'OFFLINE_OPERATION_COLLISION');
  await reject(()=>f.send('configure'),'OFFLINE_OPERATION_COLLISION');await reject(()=>f.configure(undefined,'model'),'OFFLINE_OPERATION_COLLISION');
  await f.send();await reject(()=>f.configure(undefined,'one'),'OFFLINE_OPERATION_COLLISION');
  const v=await f.host.read(f.id);await reject(()=>f.host.configureModel({sessionId:f.id,operationId:'configure',expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,purpose:'story',choice:null}),'OFFLINE_OPERATION_COLLISION');
  const operation=v.session.operations[0];await f.change('enable');assert.equal((await f.host.send(operation.intent)).replayed,true);assert.equal(f.provider.dispatchCount,1);
});

for(const action of ['disable','uninstall'])test('派发前'+action+'：已冻结请求转为未派发，fixture 次数为零',async t=>{
  let f,armed=true;f=await fixture(t,{fault:async phase=>{if(phase==='beforeDispatch'&&armed){armed=false;await f.change(action);}}});
  await f.enable();await f.configure();const result=await f.send();
  assert.equal(result.operation.state,'not_dispatched');assert.equal(result.operation.diagnostic,'HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH');assert.equal(f.provider.dispatchCount,0);
  assert.equal(result.operation.request.policy.history,'window');assert.equal((await f.host.read(f.id)).effectivePolicy.history,'full');
});

test('已派发未知结果撤权后仍需核对；重启读取原回执不派发',async t=>{
  const f=await fixture(t,{providerFault:async phase=>{if(phase==='afterReceipt')throw Error('receipt ack lost');}});await f.enable();await f.configure();
  const sent=await f.send(),frozen=sent.operation.request;assert.equal(sent.operation.state,'unknown');await f.change('disable');
  await reject(()=>f.send('two','下一轮'),'OFFLINE_RESULT_PENDING');await reject(()=>f.configure(),'OFFLINE_RESULT_PENDING');
  await f.reopen();const r=await f.recover();assert.equal(r.operation.state,'completed');assert.deepEqual(r.operation.request,frozen);assert.equal(f.provider.dispatchCount,0);
  assert.equal((await f.host.read(f.id)).effectivePolicy.history,'full');assert.equal((await f.recover()).replayed,true);
});

test('供应商仍未知时保留原任务、草稿和策略，撤权不解除阻断',async t=>{
  const f=await fixture(t,{providerFault:async phase=>{if(phase==='afterAccepted')throw Error('unknown');}});await f.enable();await f.configure();
  const sent=await f.send();await f.change('disable');await f.reopen();const r=await f.recover();
  assert.equal(r.operation.state,'unknown');assert.deepEqual(r.operation.request,sent.operation.request);assert.equal((await f.host.read(f.id)).session.draft,opening);
  assert.equal(f.provider.dispatchCount,0);await reject(()=>f.send('two','继续'),'OFFLINE_RESULT_PENDING');
});

for(const phase of ['beforeCommit','afterCommit'])test('历史配置存储故障 '+phase+'：原子回执与同操作恢复',async t=>{
  let armed=true;const f=await fixture(t,{storageFault:async(p,{data})=>{if(p===phase&&data.history?.revision===1&&armed){armed=false;throw Error('disk failure');}}});
  await f.enable();const input=await f.historyInput(),before=(await f.host.read(f.id)).session;
  await reject(()=>f.host.configureHistory(input),phase==='beforeCommit'?'OFFLINE_WRITE_FAILED':'OFFLINE_WRITE_UNKNOWN');
  await f.host.reconcileStorage();const v=(await f.host.read(f.id)).session;
  if(phase==='beforeCommit')assert.deepEqual(v,before);else assert.equal(v.history.revision,1);
  const result=await f.host.configureHistory(input);assert.equal(result.replayed,phase==='afterCommit');assert.equal((await f.host.read(f.id)).session.history.revision,1);assert.equal(f.provider.dispatchCount,0);
});

test('生成期间允许撤权，但配置被 busy 拒绝，迟到剧情仍保留',async t=>{
  let release,accepted;const gate=new Promise(r=>release=r),started=new Promise(r=>accepted=r);
  const f=await fixture(t,{providerFault:async phase=>{if(phase==='afterAccepted'){accepted();await gate;}}});await f.enable();await f.configure();
  const input=await f.historyInput(),sending=f.send();await started;
  await reject(()=>f.host.configureHistory(input),'OFFLINE_SESSION_BUSY');await f.change('disable');release();
  assert.equal((await sending).operation.state,'completed');assert.equal((await f.host.read(f.id)).effectivePolicy.history,'full');
});

test('只有完整回合进入窗口；截断原文保留但不成为后续历史',async t=>{
  const f=await fixture(t,{replies:{...replies,two:{status:'completed',text:'TRUNCATED_RAW',finishReason:'length'}}});await f.enable();await f.configure();await f.send();
  const partial=await f.send('two','第二轮');assert.equal(partial.operation.state,'incomplete');
  const r=(await f.send('three','再继续')).operation.request;assert.deepEqual(r.policy.selection.selectedTurns,[1]);assert.equal(r.messages.some(m=>m.content.includes('TRUNCATED_RAW')),false);
  assert.equal((await f.host.read(f.id)).session.operations[1].receipt.result.text,'TRUNCATED_RAW');
});

test('持久化校验拒绝篡改配置、窗口和消息，即使外层 checksum 重算',async t=>{
  const f=await fixture(t);await f.send();await f.send('two','二');await f.enable();await f.configure();await f.send('three','三');
  const file=join(f.directory,'sessions','records',f.id+'.json'),original=await readFile(file,'utf8');
  const mutations=[s=>s.history.config.turns=50,s=>s.operations[2].request.policy.selection.selectedTurns=[1,2],s=>s.operations[2].request.messages[1].content='FORGED',s=>s.history.changes[0].receipt.epoch=-1,s=>s.history.changes[0].receipt.resourceHash=sha256('another-work')];
  for(const mutate of mutations){const envelope=JSON.parse(original);mutate(envelope.data);for(const op of envelope.data.operations){op.request.messagesHash=digest(op.request.messages);op.requestHash=digest(op.request);}envelope.sha256=digest(envelope.data);await writeFile(file,canonical(envelope));await assert.rejects(f.host.read(f.id));await writeFile(file,original);}
  assert.equal((await f.host.read(f.id)).session.turns.length,3);
});

test('重开保持已授权设置；同目录第二个宿主不能夺取活跃锁',async t=>{
  const f=await fixture(t);await f.enable();await f.configure();const before=await f.host.read(f.id);
  await reject(()=>createAuthoringHistoryControl({directory:join(f.directory,'plugins'),lookupSession:id=>f.host.lookupSession(id),resolveRuntime:f.resolveRuntime,recoverDeadOwner:true}),'OFFLINE_STORE_BUSY');
  await f.reopen();const after=await f.host.read(f.id);assert.deepEqual(after.session,before.session);assert.deepEqual(after.effectivePolicy,before.effectivePolicy);
});

async function child(code){return new Promise((resolve,reject)=>{const p=spawn(process.execPath,['--input-type=module','-e',code],{windowsHide:true,stdio:['ignore','pipe','pipe']});let output='';p.stdout.on('data',b=>output+=b);p.stderr.on('data',b=>output+=b);p.on('error',reject);p.on('exit',exitCode=>resolve({exitCode,output}));});}
for(const point of ['afterPrepared','beforeDispatch','afterProvider'])test('实际进程退出后显式恢复 '+point+'，插件授权和原请求不重放',async t=>{
  const f=await fixture(t);await f.enable();await f.configure();const intent=await f.input();await f.close();
  const module=name=>JSON.stringify(new URL('./'+name+'.mjs',import.meta.url).href);
  const result=await child(`
    import {AuthoringStore} from ${module('store')}; import {openPublishedRuntime} from ${module('runtime-binding')};
    import {OfflineSessionHost,OfflineFixtureProvider} from ${module('offline-session')}; import {OfflineModelControl} from ${module('model-control')};
    import {createAuthoringHistoryControl} from ${module('history-control')};
    const dir=${JSON.stringify(f.directory)},store=await AuthoringStore.open(dir+'/works'),resolveRuntime=r=>openPublishedRuntime(store,r);
    let host;const historyControl=await createAuthoringHistoryControl({directory:dir+'/plugins',resolveRuntime,lookupSession:id=>host.lookupSession(id)});
    const provider=await OfflineFixtureProvider.open(dir+'/provider',{replies:${JSON.stringify(replies)}});
    host=await OfflineSessionHost.open({directory:dir+'/sessions',resolveRuntime,provider,historyControl,modelControl:new OfflineModelControl(${JSON.stringify(catalog)}),fault:async phase=>{if(phase===${JSON.stringify(point)})process.exit(73);}});
    await host.send(${JSON.stringify(intent)});
  `);
  assert.equal(result.exitCode,73,result.output);await f.reopen(true);
  const before=await f.host.read(f.id),frozen=before.session.operations[0].request;assert.equal(before.effectivePolicy.history,'window');
  const recovered=await f.recover();assert.equal(recovered.operation.state,point==='afterPrepared'?'not_dispatched':point==='afterProvider'?'completed':'unknown');
  assert.deepEqual(recovered.operation.request,frozen);assert.equal(f.provider.dispatchCount,0);
});


test('授权 guard 读取期间模型资格变化：最后同步检查阻断，不能派发旧选择',async t=>{
  let f,armed=false,changed=false;
  f=await fixture(t,{fault:async phase=>{if(phase==='beforeDispatch')armed=true;},onResolve:async()=>{if(armed&&!changed){changed=true;f.modelControl.replace({catalogId:catalog.catalogId,models:[]});}}});
  await f.enable();await f.configure();const result=await f.send();
  assert.equal(changed,true);assert.equal(result.operation.state,'not_dispatched');assert.equal(result.operation.diagnostic,'MODEL_SELECTION_CHANGED');assert.equal(f.provider.dispatchCount,0);
});

test('派发后的错误码不能伪装成本地拒绝；仍以原操作核对未知结果',async t=>{
  const f=await fixture(t,{providerFault:async phase=>{if(phase==='afterReceipt')throw Object.assign(Error('fixture transport failure'),{code:'HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH'});}});
  await f.enable();await f.configure();assert.equal((await f.send()).operation.state,'unknown');assert.equal(f.provider.dispatchCount,1);
  assert.equal((await f.recover()).operation.state,'completed');assert.equal(f.provider.dispatchCount,1);
});

test('冻结的世界书概率在历史窗口恢复时不重抽，回执与消息保持一致',async t=>{
  let draws=0;const f=await fixture(t,{draw:()=>{draws++;return .1;},document:d=>{d.worldbook.entries[0].chance=50;d.worldbook.entries[0].roles=['user'];d.worldbook.entries[0].keywords=['原始角色'];},providerFault:async phase=>{if(phase==='afterReceipt')throw Error('lost');}});
  await f.enable();await f.configure();const first=await f.send(),before=draws;assert.equal(before,1);
  await f.reopen();const recovered=await f.recover();assert.deepEqual(recovered.operation.request,first.operation.request);assert.equal(draws,before);assert.equal(f.provider.dispatchCount,0);
});


test('拒绝注册表目录链接，不在外部目录创建 owner 或 registry',async()=>{
  const dir=await mkdtemp(join(root,'link-')),outside=join(dir,'outside'),directory=join(dir,'control');await mkdir(outside);await mkdir(directory);
  await symlink(outside,join(directory,'registry'),process.platform==='win32'?'junction':'dir');
  await reject(()=>createAuthoringHistoryControl({directory,lookupSession:()=>{},resolveRuntime:()=>{}}),'HISTORY_REGISTRY_UNSAFE');
  assert.deepEqual(await readdir(outside),[]);
});

test('死进程锁默认不接管；显式恢复后保留死 owner 证据',async()=>{
  const dir=await mkdtemp(join(root,'lease-')),module=JSON.stringify(new URL('./history-control.mjs',import.meta.url).href);
  const result=await child(`import {createAuthoringHistoryControl} from ${module};await createAuthoringHistoryControl({directory:${JSON.stringify(dir)},lookupSession:()=>{},resolveRuntime:()=>{}});process.exit(73);`);
  assert.equal(result.exitCode,73,result.output);
  await reject(()=>createAuthoringHistoryControl({directory:dir,lookupSession:()=>{},resolveRuntime:()=>{}}),'OFFLINE_OWNER_RECOVERY_REQUIRED');
  const c=await createAuthoringHistoryControl({directory:dir,lookupSession:()=>{},resolveRuntime:()=>{},recoverDeadOwner:true});
  try{assert.equal((await readdir(join(dir,'registry'))).filter(x=>x.startsWith('owner-dead-')).length,1);assert.equal((await readdir(join(dir,'ownership'))).filter(x=>x.startsWith('owner-dead-')).length,1);}finally{await c.close();}
});
