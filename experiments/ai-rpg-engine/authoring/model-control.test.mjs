import test from 'node:test';
import http from 'node:http';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {blankDocument,validateDocument,canonical,sha256} from './contracts.mjs';
import {AuthoringStore} from './store.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {OfflineSessionHost,OfflineFixtureProvider,CONTROLLED_OFFLINE_SESSION_FORMAT} from './offline-session.mjs';
import {OfflineModelControl,ControlPlaneModelControl,CONTROL_CATALOG_FORMAT,fixedRpgCatalog,validateModelChoice,modelCapacity,requireOfflineModelControl} from './model-control.mjs';

const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6c-tests/',import.meta.url));await mkdir(root,{recursive:true});
const hash=value=>sha256(canonical(value));
const code=expected=>error=>error.code===expected;
const reject=(action,expected)=>assert.rejects(async()=>action(),code(expected));
const throws=(action,expected)=>assert.throws(action,code(expected));
const reply={status:'completed',text:'完整原始回复\r\n<details>灯塔</details>',finishReason:'stop'};
const field=(minimum,maximum,defaultValue,required,wireName)=>({minimum,maximum,defaultValue,required,wireName});
function row(id='route-a',changes={}) {
  return {selectionId:id,selectionRevision:sha256(id),modelId:'fixture/story-a',name:'离线能力样例 A',available:true,contextLength:128000,maxOutputTokens:32000,
    parameterPolicy:{kind:'editable',fields:{temperature:field(0,2,.7,false,'temperature'),topP:field(0,1,.8,false,'top_p'),maxOutputTokens:field(1,32000,4096,true,'max_tokens')},exclusive:[]},...changes};
}
function catalog() {
  const token=row('route-token',{modelId:'fixture/token-only',name:'仅输出长度的离线样例'});
  token.parameterPolicy.fields={maxOutputTokens:field(1,32000,4096,true,'max_completion_tokens')};
  return {catalogId:'offline-test-catalog',models:[row(),row('route-b'),token]};
}
function selection(control,id='route-a',parameters={temperature:.75,topP:.9,maxOutputTokens:4096}) {
  const c=control.catalog(),entry=c.models.find(e=>e.selectionId===id);
  return {catalogRevision:c.revision,selectionId:id,selectionRevision:entry.selectionRevision,parameters};
}
async function fixture(t,options={}) {
  const directory=await mkdtemp(join(root,'run-')),store=await AuthoringStore.open(join(directory,'works'));
  const document=blankDocument();document.metadata.name='离线模型配置验证';document.metadata.audience='female';
  document.sources.system='  frozen SYSTEM\r\n';document.sources.prefix='PRE';document.sources.suffix='POST';
  document.model={selection:{catalogId:'unqualified',modelId:'do-not-autoselect'},parameters:{maxOutputTokens:4096}};
  if(options.document)options.document(document);
  const work=await store.create({operationId:'work',expectedRevision:null,document});
  const published=await store.publish(work.workId,{operationId:'publish',expectedRevision:0});
  const reference={workId:work.workId,versionId:published.result.versionId},resolveRuntime=ref=>openPublishedRuntime(store,ref);
  const control=options.control??new OfflineModelControl(options.catalog??catalog());
  let provider=await OfflineFixtureProvider.open(join(directory,'provider'),{replies:{one:reply,two:reply,three:reply},fault:options.providerFault});
  let host=await OfflineSessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider,modelControl:control,draw:options.draw,fault:options.fault,storageFault:options.storageFault});
  const creation={operationId:'session',expectedRevision:null,reference,openingText:options.opening??'  原始开局🐟\r\n',presetId:null};
  const id=(await host.create(creation)).session.id;
  t.after(async()=>{await host.close();await provider.close();await store.close();});
  return {directory,store,reference,document,control,id,creation,get host(){return host;},get provider(){return provider;},
    async configureInput(operationId='select-story',purpose='story',id='route-a',parameters){const s=(await host.read(this.id)).session;return {sessionId:this.id,operationId,expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,choice:id===null?null:selection(control,id,parameters)};},
    async configure(...args){return host.configureModel(await this.configureInput(...args));},
    async sendInput(operationId='one',input=creation.openingText){const s=(await host.read(id)).session;return {sessionId:id,operationId,expectedRevision:s.revision,expectedModelRevision:s.models.revision,input};},
    async send(...args){return host.send(await this.sendInput(...args));},
    async recover(operationId='one'){return host.recover({sessionId:id,operationId,expectedRevision:(await host.read(id)).session.revision});},
    async reopen(){await host.close();await provider.close();provider=await OfflineFixtureProvider.open(join(directory,'provider'));host=await OfflineSessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider,modelControl:control});},
  };
}

test('受信目录边界、稳定 hash、拷贝隔离；作品不能提供 URL、凭据或伪造控制器',()=>{
  const data=catalog(),c=new OfflineModelControl(data),before=c.catalog();data.models[0].name='changed';
  assert.deepEqual(c.catalog(),before);const projected=c.list();projected.models[0].parameterPolicy.fields.temperature.maximum=999;
  assert.equal(c.catalog().models[0].parameterPolicy.fields.temperature.maximum,2);assert.equal(before.source,'offline-fixture');
  throws(()=>requireOfflineModelControl({catalog:()=>before}),'MODEL_CONTROL_HOST_REQUIRED');
  throws(()=>new OfflineModelControl({...catalog(),endpoint:'https://example.invalid'}),'AUTHORING_UNKNOWN_FIELD');
  throws(()=>new OfflineModelControl({catalogId:'x',models:[{...row(),apiKey:'not-a-secret'}]}),'AUTHORING_UNKNOWN_FIELD');
  throws(()=>new OfflineModelControl({catalogId:'x',models:[row(),row()]}),'MODEL_SELECTION_DUPLICATE');
  assert.equal(catalog().models[0].modelId,catalog().models[1].modelId);
  assert.notEqual(c.select(selection(c)).sha256,c.select(selection(c,'route-b')).sha256);
});

test('采样参数缺失不补默认值；服务端能力决定 wire 参数名',()=>{
  const c=new OfflineModelControl(catalog()),choice=c.select(selection(c,'route-token',{maxOutputTokens:1234}));
  assert.deepEqual(choice.wireParameters,{max_completion_tokens:1234});validateModelChoice(choice);
  throws(()=>c.select(selection(c,'route-token',{temperature:.7,maxOutputTokens:1234})),'MODEL_PARAMETER_UNSUPPORTED');
  const omitted=c.select(selection(c,'route-a',{maxOutputTokens:1234}));assert.deepEqual(omitted.parameters,{maxOutputTokens:1234});assert.deepEqual(omitted.wireParameters,{max_tokens:1234});
  throws(()=>c.select({...selection(c),parameters:{top_p:.5,maxOutputTokens:1234}}),'AUTHORING_UNKNOWN_FIELD');
});

test('参数范围、整数、必填、非有限数、无效规则与互斥组合拒绝',()=>{
  const c=new OfflineModelControl(catalog());
  for(const parameters of [{temperature:-.01,maxOutputTokens:1},{temperature:2.01,maxOutputTokens:1},{topP:1.01,maxOutputTokens:1},{maxOutputTokens:1.5},{maxOutputTokens:32001},{maxOutputTokens:true}])throws(()=>c.select(selection(c,'route-a',parameters)),'MODEL_PARAMETER_RANGE');
  throws(()=>c.select(selection(c,'route-a',{})),'MODEL_PARAMETER_REQUIRED');
  for(const value of [NaN,Infinity])assert.throws(()=>c.select(selection(c,'route-a',{maxOutputTokens:value})));
  const data=catalog();data.models[0].parameterPolicy.exclusive=[['temperature','topP']];c.replace(data);
  throws(()=>c.select(selection(c)),'MODEL_PARAMETER_CONFLICT');assert.ok(c.select(selection(c,'route-a',{temperature:0,maxOutputTokens:1})));
  data.models[0].parameterPolicy.fields.maxOutputTokens.wireName='endpoint';throws(()=>c.replace(data),'MODEL_CAPABILITY_INVALID');
});

test('缺失资格/容量证据不推测；未知能力仍可列出但不可选择',()=>{
  for(const changes of [{available:false},{contextLength:null},{maxOutputTokens:null},{parameterPolicy:null},{maxOutputTokens:8}]){
    const c=new OfflineModelControl({catalogId:'c',models:[row('route-a',changes)]}),item=c.list().models[0];
    assert.equal(item.eligible,false);throws(()=>c.select(selection(c)),changes.available===false?'MODEL_SELECTION_UNAVAILABLE':'MODEL_CAPABILITY_UNKNOWN');
  }
});

test('记录的旧控制面目录只投影为固定档；不扩张编辑范围或丢弃参数',()=>{
  const old={models:[{selectionId:'fixed',selectionRevision:sha256('fixed'),model:'fixture/fixed',name:'固定档',available:true,contextLength:128000,maxOutputTokens:32000,
    parameters:{temperature:.7,top_p:.8,max_tokens:16384}}]};
  const c=new OfflineModelControl(fixedRpgCatalog('old-cache',old));assert.equal(c.list().models[0].parameterPolicy.kind,'fixed');
  assert.deepEqual(c.select(selection(c,'fixed',{temperature:.7,topP:.8,maxOutputTokens:16384})).wireParameters,old.models[0].parameters);
  throws(()=>c.select(selection(c,'fixed',{temperature:.9,topP:.8,maxOutputTokens:16384})),'MODEL_PARAMETER_RANGE');
  throws(()=>c.select(selection(c,'fixed',{maxOutputTokens:16384})),'MODEL_PARAMETER_REQUIRED');
  old.models[0].available=false;old.models[0].maxOutputTokens=8000;assert.equal(new OfflineModelControl(fixedRpgCatalog('old-cache',old)).list().models[0].eligible,false);
  old.models[0].parameters={max_tokens:16384};old.models[0].maxOutputTokens=32000;old.models[0].available=true;
  assert.deepEqual(new OfflineModelControl(fixedRpgCatalog('old-cache',old)).select({catalogRevision:new OfflineModelControl(fixedRpgCatalog('old-cache',old)).catalog().revision,selectionId:'fixed',selectionRevision:sha256('fixed'),parameters:{maxOutputTokens:16384}}).wireParameters,{max_tokens:16384});
});

test('目录和能力版本变化拒绝旧选择；未相关模型变化不使已保存选择失效',()=>{
  const data=catalog(),c=new OfflineModelControl(data),input=selection(c),selected=c.select(input);
  data.models[1].name='other';c.replace(data);throws(()=>c.select(input),'MODEL_CATALOG_CHANGED');assert.equal(c.assertCurrent(selected),true);
  data.models[0].parameterPolicy.fields.temperature.maximum=1;c.replace(data);throws(()=>c.assertCurrent(selected),'MODEL_SELECTION_CHANGED');
  const fresh=selection(c);throws(()=>c.select({...fresh,selectionRevision:sha256('wrong')}),'MODEL_SELECTION_CHANGED');
});

test('容量包含完整 UTF-8 消息、开销与输出保留，不裁剪文本',()=>{
  const c=new OfflineModelControl(catalog()),choice=c.select(selection(c,'route-a',{maxOutputTokens:4096}));
  const messages=[{role:'system',content:'系统🐟'},{role:'user',content:'玩家'}],frozen=structuredClone(messages);
  const capacity=modelCapacity(choice,messages);assert.equal(capacity.inputEstimate,256+64+Buffer.byteLength('系统🐟玩家'));assert.deepEqual(messages,frozen);
  throws(()=>modelCapacity(choice,[{role:'user',content:'中'.repeat(50000)}]),'MODEL_CONTEXT_CAPACITY');
});

test('作品只保存参数建议，支持仅输出长度的配置 JSON 往返',async t=>{
  const f=await fixture(t),config=await f.store.exportConfiguration(f.reference.workId,f.reference.versionId);
  assert.deepEqual(config.document.model.parameters,{maxOutputTokens:4096});
  assert.deepEqual(validateDocument(JSON.parse(JSON.stringify(config.document))).model,config.document.model);
  const invalid=structuredClone(config.document);invalid.model.parameters.temperature=null;throws(()=>validateDocument(invalid),'AUTHORING_NUMBER_INVALID');
  invalid.model.parameters={maxOutputTokens:4096,top_p:.7};throws(()=>validateDocument(invalid),'AUTHORING_UNKNOWN_FIELD');
});

test('新会话不自动采用作品建议、选模型、授权插件或增加预算',async t=>{
  const f=await fixture(t),s=(await f.host.read(f.id)).session;
  assert.equal(s.format,CONTROLLED_OFFLINE_SESSION_FORMAT);assert.deepEqual(s.models,{revision:0,current:{story:null,summary:null,memory:null},changes:[]});
  const list=await f.host.modelCatalog(f.id);assert.deepEqual(list.authoredSuggestion,f.document.model);assert.equal(list.realProvider,false);
  await reject(()=>f.send(),'MODEL_SELECTION_REQUIRED');assert.equal(f.provider.dispatchCount,0);
  for(const key of ['budget','grants','ledger'])assert.equal(Object.hasOwn(s,key),false);
  assert.equal(f.host.readiness.controlledModels,true);assert.equal(f.host.readiness.runtimeReady,false);
});

test('剧情/摘要/记忆独立配置；实际离线派发封存所选模型、精确参数和能力',async t=>{
  let captured=null;const f=await fixture(t,{providerFault:async(phase,request)=>{if(phase==='afterAccepted')captured=request;}});
  await f.configure();await f.configure('select-summary','summary','route-token',{maxOutputTokens:321});await f.configure('select-memory','memory','route-b',{topP:.5,maxOutputTokens:654});
  const before=(await f.host.read(f.id)).session.models;await f.configure('change-story','story','route-token',{maxOutputTokens:4321});
  const after=(await f.host.read(f.id)).session.models;assert.deepEqual(after.current.summary,before.current.summary);assert.deepEqual(after.current.memory,before.current.memory);
  assert.equal(f.provider.dispatchCount,0);const done=await f.send(),r=done.operation.request;
  assert.deepEqual(captured,r);assert.equal(r.model,'fixture/token-only');assert.deepEqual(r.parameters,{max_completion_tokens:4321});
  assert.equal(r.modelConfiguration.revision,4);assert.deepEqual(r.modelConfiguration.choice,after.current.story);
  assert.equal(r.messages[0].content,f.document.sources.system);assert.equal(r.messages.at(-1).content,'PRE\n\n'+f.creation.openingText+'\n\nPOST');
  assert.equal(r.messagesHash,hash(r.messages));assert.equal(done.operation.receipt.requestHash,hash(r));assert.equal(r.policy.pluginExecution,false);assert.equal(r.policy.billedCalls,0);
  await f.reopen();assert.deepEqual((await f.host.read(f.id)).session.models,after);assert.equal(f.provider.dispatchCount,0);
});

test('配置双 revision、跨标签冲突与幂等，不重复选择也不调用 fixture',async t=>{
  const f=await fixture(t),input=await f.configureInput();
  const [a,b]=await Promise.all([f.host.configureModel(input),f.host.configureModel(input)]);assert.equal(a.replayed,false);assert.equal(b.replayed,true);
  await reject(()=>f.host.configureModel({...input,operationId:'another'}),'OFFLINE_REVISION_CONFLICT');
  const fresh=await f.configureInput('another');await reject(()=>f.host.configureModel({...fresh,expectedModelRevision:0}),'MODEL_REVISION_CONFLICT');
  await reject(()=>f.host.configureModel({...input,purpose:'memory'}),'OFFLINE_OPERATION_COLLISION');
  const send=await f.sendInput();await reject(()=>f.host.send({...send,expectedModelRevision:0}),'MODEL_REVISION_CONFLICT');
  await reject(()=>f.host.send({...send,operationId:input.operationId}),'OFFLINE_OPERATION_COLLISION');
  await f.send();await reject(()=>f.configure('one'),'OFFLINE_OPERATION_COLLISION');assert.equal(f.provider.dispatchCount,1);
});

test('配置输入在异步等待前复制，调用方后续篡改不影响落盘',async t=>{
  const f=await fixture(t),input=await f.configureInput(),pending=f.host.configureModel(input);
  input.choice.parameters.temperature=1.9;input.purpose='memory';await pending;
  const s=(await f.host.read(f.id)).session;assert.equal(s.models.current.story.parameters.temperature,.75);assert.equal(s.models.current.memory,null);
});

test('清除选择不回退默认；复制会话不继承模型及授权',async t=>{
  const f=await fixture(t);await f.configure();const selected=(await f.host.read(f.id)).session.models;
  const copied=await f.host.create({...f.creation,operationId:'second-session'});assert.deepEqual(copied.session.models.current,{story:null,summary:null,memory:null});
  await f.configure('clear-story','story',null);assert.equal((await f.host.read(f.id)).session.models.current.story,null);
  await reject(()=>f.send(),'MODEL_SELECTION_REQUIRED');assert.equal(f.provider.dispatchCount,0);assert.ok(selected.current.story);
});

test('非法能力/参数与客户端注入配置、system 或供应商地址全部在保存/派发前拒绝',async t=>{
  const f=await fixture(t);const input=await f.configureInput();
  await reject(()=>f.host.configureModel({...input,endpoint:'https://example.invalid'}),'AUTHORING_UNKNOWN_FIELD');
  await reject(()=>f.host.configureModel({...input,choice:{...input.choice,modelId:'spoof'}}),'AUTHORING_UNKNOWN_FIELD');
  await reject(()=>f.host.configureModel({...input,choice:{...input.choice,parameters:{maxOutputTokens:40000}}}),'MODEL_PARAMETER_RANGE');
  assert.equal((await f.host.read(f.id)).session.revision,0);await f.configure();
  const send=await f.sendInput();for(const [key,value] of [['model','spoof'],['parameters',{}],['messages',[]],['system','spoof']])await reject(()=>f.host.send({...send,[key]:value}),'AUTHORING_UNKNOWN_FIELD');
  assert.equal(f.provider.dispatchCount,0);
});

test('选择失效阻断发送，不重新认证、不替换同名模型',async t=>{
  const f=await fixture(t);await f.configure();const data=catalog();data.models[0].available=false;f.control.replace(data);
  await reject(()=>f.send(),'MODEL_SELECTION_CHANGED');assert.equal(f.provider.dispatchCount,0);assert.equal((await f.host.read(f.id)).session.operations.length,0);
  const fresh=await f.configureInput('reselect');await reject(()=>f.host.configureModel(fresh),'MODEL_SELECTION_UNAVAILABLE');
});

test('落盘后临派发时目录变化产生明确未派发记录，不误报未知',async t=>{
  let f;f=await fixture(t,{fault:async phase=>{if(phase==='beforeDispatch')f.control.replace({catalogId:'offline-test-catalog',models:[]});}});
  await f.configure();const input=await f.sendInput(),result=await f.host.send(input);
  assert.equal(result.operation.state,'not_dispatched');assert.equal(result.operation.diagnostic,'MODEL_SELECTION_CHANGED');assert.equal(f.provider.dispatchCount,0);
  assert.equal((await f.recover()).replayed,true);assert.equal((await f.host.send(input)).replayed,true);assert.equal(f.provider.dispatchCount,0);
});

test('长输入容量失败不保留半个请求、不截断；独立模型输出上限有效',async t=>{
  const data=catalog();data.models[0].contextLength=4300;
  const f=await fixture(t,{catalog:data,opening:'中'.repeat(120)});await f.configure();await reject(()=>f.send(),'MODEL_CONTEXT_CAPACITY');
  const s=(await f.host.read(f.id)).session;assert.equal(s.operations.length,0);assert.equal(s.draft,f.creation.openingText);assert.equal(f.provider.dispatchCount,0);
  await f.configure('reduce','story','route-a',{maxOutputTokens:10});assert.equal((await f.send()).operation.state,'completed');
});

test('未知结果阻止换模型；重开且目录已删除仍按原配置恢复，无重发',async t=>{
  const f=await fixture(t,{providerFault:async phase=>{if(phase==='afterReceipt')throw Error('lost acknowledgment');}});await f.configure();
  const input=await f.sendInput(),unknown=await f.host.send(input),frozen=unknown.operation.request;
  assert.equal(unknown.operation.state,'unknown');await reject(()=>f.configure('change'),'OFFLINE_RESULT_PENDING');
  f.control.replace({catalogId:'gone',models:[]});await f.reopen();assert.equal(f.provider.dispatchCount,0);
  const recovered=await f.recover();assert.equal(recovered.operation.state,'completed');assert.deepEqual(recovered.operation.request,frozen);
  assert.equal((await f.host.send(input)).replayed,true);assert.equal(f.provider.dispatchCount,0);
  await reject(()=>f.send('two','下一轮'),'MODEL_SELECTION_CHANGED');
});

test('接受后没有完成回执，目录切换/重启不能解除未知或放行新配置',async t=>{
  const f=await fixture(t,{providerFault:async phase=>{if(phase==='afterAccepted')throw Error('unknown');}});await f.configure();await f.send();
  await f.reopen();f.control.replace(catalog());assert.equal((await f.recover()).operation.state,'unknown');
  await reject(()=>f.configure('clear','story',null),'OFFLINE_RESULT_PENDING');assert.equal(f.provider.dispatchCount,0);
});

test('完成后的模型切换只影响新请求；原请求与原文永久绑定旧选择',async t=>{
  const f=await fixture(t);await f.configure();const first=await f.send(),request=first.operation.request;
  await f.configure('next-model','story','route-token',{maxOutputTokens:2048});const second=await f.send('two','新输入');
  assert.deepEqual((await f.host.read(f.id)).session.operations[0].request,request);assert.equal(second.operation.request.model,'fixture/token-only');
  assert.deepEqual(second.operation.request.messages.slice(1,3),[{role:'user',content:request.messages.at(-1).content},{role:'assistant',content:reply.text}]);
  await f.reopen();assert.deepEqual((await f.host.read(f.id)).session.operations[0].request,request);
});

test('进行中的发送拒绝配置修改，不排队偷偷更换模型',async t=>{
  let entered,release;const started=new Promise(r=>entered=r),gate=new Promise(r=>release=r);
  const f=await fixture(t,{providerFault:async phase=>{if(phase==='afterAccepted'){entered();await gate;}}});await f.configure();
  const input=await f.configureInput('change'),pending=f.send();await started;
  try{await reject(()=>f.host.configureModel(input),'OFFLINE_SESSION_BUSY');}finally{release();}await pending;assert.equal((await f.host.read(f.id)).session.models.revision,1);
});

for(const phase of ['beforeCommit','afterCommit'])test('配置落盘故障 '+phase+' 保留 CAS 和同 ID 核对，不生成调用',async t=>{
  let armed=false;const f=await fixture(t,{storageFault:async p=>{if(armed&&p===phase){armed=false;throw Error('injected write fault');}}});
  const input=await f.configureInput();armed=true;await reject(()=>f.host.configureModel(input),phase==='beforeCommit'?'OFFLINE_WRITE_FAILED':'OFFLINE_WRITE_UNKNOWN');
  await f.host.reconcileStorage();const result=await f.host.configureModel(input);assert.equal(result.replayed,phase==='afterCommit');
  assert.equal((await f.host.read(f.id)).session.models.revision,1);assert.equal(f.provider.dispatchCount,0);
});

test('模式不同不静默升级或降级会话，移除控制器不能绕过选择门禁',async t=>{
  const f=await fixture(t);await f.host.close();
  const legacy=await OfflineSessionHost.open({directory:join(f.directory,'sessions'),provider:f.provider,resolveRuntime:ref=>openPublishedRuntime(f.store,ref)});
  try{await reject(()=>legacy.read(f.id),'OFFLINE_MODEL_MODE_MISMATCH');}finally{await legacy.close();}
});

test('篡改保存的模型选择或请求参数即使重算外层 hash 也不能读取',async t=>{
  const f=await fixture(t);await f.configure();await f.send();
  const path=join(f.directory,'sessions','records',f.id+'.json'),envelope=JSON.parse(await readFile(path,'utf8'));
  const original=structuredClone(envelope);
  envelope.data.models.current.story.parameters.temperature=1.9;envelope.sha256=hash(envelope.data);await writeFile(path,canonical(envelope));
  await reject(()=>f.host.read(f.id),'MODEL_STATE_CORRUPT');
  const op=original.data.operations[0];op.request.parameters.temperature=1.9;op.requestHash=hash(op.request);
  original.sha256=hash(original.data);await writeFile(path,canonical(original));
  await reject(()=>f.host.read(f.id),'MODEL_REQUEST_CORRUPT');
});

test('真实子进程退出后保持原模型配置；空目录重启只核对原回执',async t=>{
  const directory=await mkdtemp(join(root,'crash-'));
  const script=`
    import {AuthoringStore} from './authoring/store.mjs';
    import {blankDocument} from './authoring/contracts.mjs';
    import {openPublishedRuntime} from './authoring/runtime-binding.mjs';
    import {OfflineSessionHost,OfflineFixtureProvider} from './authoring/offline-session.mjs';
    import {OfflineModelControl} from './authoring/model-control.mjs';
    import {writeFile} from 'node:fs/promises';
    const dir=${JSON.stringify(directory)},store=await AuthoringStore.open(dir+'/works');
    const d=blankDocument();d.metadata.name='crash';d.metadata.audience='male';d.sources.system='SYS';
    const w=await store.create({operationId:'work',expectedRevision:null,document:d}),v=await store.publish(w.workId,{operationId:'publish',expectedRevision:0});
    const reference={workId:w.workId,versionId:v.result.versionId},control=new OfflineModelControl(${JSON.stringify(catalog())});
    const provider=await OfflineFixtureProvider.open(dir+'/provider',{replies:{one:${JSON.stringify(reply)}}});
    const host=await OfflineSessionHost.open({directory:dir+'/sessions',resolveRuntime:r=>openPublishedRuntime(store,r),provider,modelControl:control,fault:async phase=>{if(phase==='afterProvider')process.exit(73);}});
    const s=(await host.create({operationId:'session',expectedRevision:null,reference,openingText:'START',presetId:null})).session;
    await host.configureModel({sessionId:s.id,operationId:'choose',expectedRevision:0,expectedModelRevision:0,purpose:'story',choice:${JSON.stringify(selection(new OfflineModelControl(catalog())))}});
    await writeFile(dir+'/identity.json',JSON.stringify({id:s.id,reference}));
    await host.send({sessionId:s.id,operationId:'one',expectedRevision:1,expectedModelRevision:1,input:'START'});
  `;
  const result=await new Promise((resolve,reject)=>{const child=spawn(process.execPath,['--input-type=module','-e',script],{cwd:fileURLToPath(new URL('../',import.meta.url)),windowsHide:true});let err='';child.stderr.on('data',chunk=>err+=chunk);child.on('error',reject);child.on('exit',exit=>resolve({exit,err}));});
  assert.equal(result.exit,73,result.err);
  const identity=JSON.parse(await readFile(join(directory,'identity.json'),'utf8')),store=await AuthoringStore.open(join(directory,'works'),{recoverDeadOwner:true});
  const provider=await OfflineFixtureProvider.open(join(directory,'provider'),{recoverDeadOwner:true}),control=new OfflineModelControl({catalogId:'empty',models:[]});
  const host=await OfflineSessionHost.open({directory:join(directory,'sessions'),resolveRuntime:r=>openPublishedRuntime(store,r),provider,modelControl:control,recoverDeadOwner:true});
  t.after(async()=>{await host.close();await provider.close();await store.close();});
  const before=await host.read(identity.id);assert.equal(before.status,'unknown');assert.equal(provider.dispatchCount,0);
  const recovered=await host.recover({sessionId:identity.id,operationId:'one',expectedRevision:before.session.revision});
  assert.equal(recovered.operation.state,'completed');assert.equal(recovered.operation.request.model,'fixture/story-a');assert.deepEqual(recovered.operation.request.parameters,{temperature:.75,top_p:.9,max_tokens:4096});assert.equal(provider.dispatchCount,0);
});


test('只有模型名称没有目录身份不能选择；能力范围变化即使同名同 revision 也必须重选',async t=>{
  const f=await fixture(t);const input=await f.configureInput();
  await reject(()=>f.host.configureModel({...input,choice:{...input.choice,selectionId:'fixture/story-a'}}),'MODEL_SETTING_INVALID');
  await f.configure();const data=catalog();data.models[0].parameterPolicy.fields.maxOutputTokens.maximum=16000;f.control.replace(data);
  await reject(()=>f.send(),'MODEL_SELECTION_CHANGED');assert.equal(f.provider.dispatchCount,0);
});

test('目录仅增加其他模型时原配置可继续；参数恰好上下界保持原值',async t=>{
  const f=await fixture(t);await f.configure('bounds','story','route-a',{temperature:0,topP:1,maxOutputTokens:1});
  const data=catalog();data.models.push(row('unrelated'));f.control.replace(data);
  const r=(await f.send()).operation.request;assert.deepEqual(r.parameters,{temperature:0,top_p:1,max_tokens:1});
});

test('世界书装配期间的资格变化在请求落盘前阻断',async t=>{
  let f,draws=0;
  f=await fixture(t,{document:d=>{d.worldbook.entries=[{id:'weighted',name:'test',enabled:true,categoryId:null,roles:['user'],rule:'or',regex:false,keywords:['原始'],targets:['system'],order:0,depth:0,chance:50,mode:'regular',body:'hit',contents:[]}];},
    draw:()=>{draws++;f.control.replace({catalogId:'offline-test-catalog',models:[]});return .1;}});
  await f.configure();await reject(()=>f.send(),'MODEL_SELECTION_CHANGED');
  assert.equal(draws,1);assert.equal(f.provider.dispatchCount,0);assert.equal((await f.host.read(f.id)).session.operations.length,0);
});


const serviceToken='synthetic-control-token-for-offline-tests';
function formalCatalog() {
  const body={format:CONTROL_CATALOG_FORMAT,source:'control-plane-cache',...catalog()};
  body.models=body.models.map(e=>({...e,parameterEvidence:'provider-declared'}));
  return {...body,revision:hash(body)};
}
async function controlServer(t,options={}) {
  const state={catalog:formalCatalog(),status:200,contentType:'application/json',raw:null,delay:0,checked:null,requests:[],headers:[],...options};
  const server=http.createServer(async(req,res)=>{
    let text='';for await(const chunk of req)text+=chunk;
    state.requests.push({method:req.method,path:req.url,body:text?JSON.parse(text):null});state.headers.push({...req.headers});
    if(state.delay)await delay(state.delay);
    res.statusCode=state.status;res.setHeader('content-type',state.contentType);
    if(state.status===302)res.setHeader('location','http://127.0.0.1:1/forbidden');
    if(options.respond)return options.respond(req,res,state);
    const value=text?JSON.parse(text):null;
    const entry=value?state.catalog.models.find(e=>e.selectionId===value.selectionId):null;
    const body=value?(state.checked??{format:'modelmirror.rpg.parameter-check/1',catalogRevision:state.catalog.revision,selectionId:entry.selectionId,selectionRevision:entry.selectionRevision,
      wireParameters:Object.fromEntries(Object.entries(value.parameters).map(([k,v])=>[entry.parameterPolicy.fields[k].wireName,v])),dispatched:false}):state.catalog;
    res.end(state.raw??JSON.stringify(body));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  const origin='http://127.0.0.1:'+server.address().port;
  return {state,origin,server,make:settings=>new ControlPlaneModelControl({origin,serviceToken,...settings})};
}

test('正式目录仅显式刷新访问两个固定只读端点，凭据不进入公开结果或作品',async t=>{
  const f=await controlServer(t),c=f.make();assert.equal(f.state.requests.length,0);
  throws(()=>c.catalog(),'MODEL_CONTROL_REFRESH_REQUIRED');await c.refresh();const chosen=c.select(selection(c));
  assert.equal(chosen.source,'control-plane-cache');assert.equal(chosen.format,'modelmirror.rpg.control-model-choice/1');
  assert.equal(chosen.entry.parameterEvidence,'provider-declared');validateModelChoice(chosen);requireOfflineModelControl(c);
  c.list();c.assertCurrent(chosen);assert.equal(f.state.requests.length,1);
  await c.validate(chosen);assert.equal(f.state.requests.length,2);
  assert.deepEqual(f.state.requests.map(r=>[r.method,r.path]),[['GET','/api/rpg/v1/authoring/models'],['POST','/api/rpg/v1/authoring/parameters/validate']]);
  assert.equal(f.state.headers[0].authorization,'Bearer '+serviceToken);assert.equal(f.state.headers[0].origin,undefined);
  assert.equal(JSON.stringify({catalog:c.list(),chosen,controller:c}).includes(serviceToken),false);
  assert.equal(typeof c.send,'undefined');assert.equal(typeof c.replace,'undefined');
});

test('控制面端点不能来自外站、DNS、路径、userinfo、查询或重定向',async t=>{
  for(const origin of ['https://example.invalid','http://localhost:9000','http://0x7f000001:9000','http://127.0.0.1:9000/path','http://127.0.0.1:9000?x=1','http://user@127.0.0.1:9000','http://127.0.0.1:99999'])
    throws(()=>new ControlPlaneModelControl({origin,serviceToken}),'MODEL_CONTROL_ENDPOINT_INVALID');
  throws(()=>new ControlPlaneModelControl({origin:'http://127.0.0.1:9000',serviceToken:'short'}),'MODEL_CONTROL_CREDENTIAL_REQUIRED');
  const f=await controlServer(t,{status:302}),c=f.make();await reject(()=>c.refresh(),'MODEL_CONTROL_RESPONSE_INVALID');assert.equal(f.state.requests.length,1);
});

test('并发刷新只读一次，失败使旧缓存不可用且不自动重试',async t=>{
  const f=await controlServer(t,{delay:20}),c=f.make();await Promise.all([c.refresh(),c.refresh(),c.refresh()]);assert.equal(f.state.requests.length,1);
  const old=c.select(selection(c));f.state.status=503;await reject(()=>c.refresh(),'MODEL_CONTROL_UNAVAILABLE');
  throws(()=>c.list(),'MODEL_CONTROL_REFRESH_REQUIRED');throws(()=>c.assertCurrent(old),'MODEL_SELECTION_UNAVAILABLE');assert.equal(f.state.requests.length,2);
  f.state.status=200;await c.refresh();c.assertCurrent(old);assert.equal(f.state.requests.length,3);
});

test('缓存到期后选择和派发前检查阻断，刷新不会自动改选或重派',async t=>{
  const f=await controlServer(t),c=f.make({ttlMs:25});await c.refresh();const old=c.select(selection(c));await delay(40);
  throws(()=>c.select(selection({catalog:()=>formalCatalog()})),'MODEL_CONTROL_REFRESH_REQUIRED');throws(()=>c.assertCurrent(old),'MODEL_SELECTION_UNAVAILABLE');
  assert.equal(f.state.requests.length,1);await c.refresh();c.assertCurrent(old);
});

for(const [status,expected] of [[401,'MODEL_CONTROL_UNAUTHORIZED'],[403,'MODEL_CONTROL_UNAUTHORIZED'],[404,'MODEL_CONTROL_UNSUPPORTED'],[409,'MODEL_CONTROL_CHANGED'],[422,'MODEL_PARAMETERS_REJECTED']])
 test('控制面 HTTP '+status+' 不泄露响应内容或凭据',async t=>{
  const f=await controlServer(t,{status,raw:serviceToken}),c=f.make();await reject(()=>c.refresh(),expected);assert.equal(f.state.requests.length,1);
 });

test('响应协议、重复键、非 JSON、凭据回显和字节上限均失败关闭',async t=>{
  const f=await controlServer(t),c=f.make();
  const cases=[['{"models":[],"models":[]}','MODEL_CONTROL_RESPONSE_INVALID'],['{}','MODEL_CONTROL_RESPONSE_INVALID'],[JSON.stringify({secret:serviceToken}),'MODEL_CONTROL_CREDENTIAL_ECHO'],['x'.repeat(4*1024*1024+1),'MODEL_CONTROL_RESPONSE_LIMIT']];
  for(const [raw,expected] of cases){f.state.raw=raw;await reject(()=>c.refresh(),expected);throws(()=>c.catalog(),'MODEL_CONTROL_REFRESH_REQUIRED');}
  f.state.raw=null;f.state.contentType='text/html';await reject(()=>c.refresh(),'MODEL_CONTROL_RESPONSE_INVALID');
  f.state.contentType='application/json';f.state.catalog.models[0].parameterEvidence='unknown';await reject(()=>c.refresh(),'MODEL_CONTROL_RESPONSE_INVALID');
});

test('慢响应头和持续响应正文受总超时约束，无后台重试',async t=>{
  const slow=await controlServer(t,{delay:100}),c=slow.make({timeoutMs:25});await reject(()=>c.refresh(),'MODEL_CONTROL_TIMEOUT');assert.equal(slow.state.requests.length,1);
  const body=await controlServer(t,{respond:(_req,res)=>{res.write('{"partial":');}}),b=body.make({timeoutMs:30});await reject(()=>b.refresh(),'MODEL_CONTROL_TIMEOUT');assert.equal(body.state.requests.length,1);
});

test('流式响应没有 Content-Length 也有字节限额',async t=>{
  const f=await controlServer(t,{respond:(_req,res)=>{res.write(' '.repeat(3*1024*1024));res.end(' '.repeat(2*1024*1024));}});
  await reject(()=>f.make().refresh(),'MODEL_CONTROL_RESPONSE_LIMIT');
});

test('目录或参数校验变化拒绝旧选项；不能接收另一模型的校验结果',async t=>{
  const f=await controlServer(t),c=f.make();await c.refresh();const old=c.select(selection(c));
  f.state.catalog.models[0].selectionRevision=hash('new-qualification');f.state.catalog.revision=hash('new-catalog');await c.refresh();throws(()=>c.assertCurrent(old),'MODEL_SELECTION_CHANGED');
  const chosen=c.select(selection(c));f.state.checked={format:'modelmirror.rpg.parameter-check/1',catalogRevision:c.catalog().revision,selectionId:'different',selectionRevision:chosen.entry.selectionRevision,wireParameters:chosen.wireParameters,dispatched:false};
  await reject(()=>c.validate(chosen),'MODEL_CONTROL_RESPONSE_INVALID');throws(()=>c.list(),'MODEL_CONTROL_REFRESH_REQUIRED');
});

test('正式缓存接入实际离线请求，重启与未知结果恢复不需要控制面可用',async t=>{
  const bridge=await controlServer(t),control=bridge.make();await control.refresh();let once=true;
  const f=await fixture(t,{control,fault:async phase=>{if(phase==='afterProvider'&&once){once=false;throw Error('mock failure after receipt');}}});
  await f.configure('story','story','route-token',{maxOutputTokens:4096});await assert.rejects(()=>f.send(),/mock failure after receipt/);assert.equal((await f.host.read(f.id)).status,'unknown');
  const request=(await f.host.read(f.id)).session.operations[0].request;assert.equal(request.model,'fixture/token-only');assert.deepEqual(request.parameters,{max_completion_tokens:4096});assert.equal(request.modelConfiguration.choice.source,'control-plane-cache');
  bridge.state.status=503;await reject(()=>control.refresh(),'MODEL_CONTROL_UNAVAILABLE');const count=bridge.state.requests.length;
  await f.reopen();const recovered=await f.recover();assert.equal(recovered.operation.state,'completed');assert.deepEqual(recovered.operation.request,request);assert.equal(bridge.state.requests.length,count);assert.equal(f.provider.dispatchCount,0);
});

test('最后派发检查遇到控制缓存失效保存为未派发，不误报未知请求',async t=>{
  const bridge=await controlServer(t),control=bridge.make();await control.refresh();
  const f=await fixture(t,{control,fault:async phase=>{if(phase==='beforeDispatch'){bridge.state.status=503;await reject(()=>control.refresh(),'MODEL_CONTROL_UNAVAILABLE');}}});
  await f.configure();const result=await f.send();assert.equal(result.operation.state,'not_dispatched');assert.equal(result.operation.diagnostic,'MODEL_SELECTION_UNAVAILABLE');assert.equal(f.provider.dispatchCount,0);await f.host.read(f.id);
});


test('校验等待期间新目录到达不会接受旧结果，也不清除较新的缓存',async t=>{
  const bridge=await controlServer(t),c=bridge.make();await c.refresh();const choice=c.select(selection(c));
  bridge.state.delay=40;const pending=reject(()=>c.validate(choice),'MODEL_CATALOG_CHANGED');await delay(10);await c.refresh();
  await pending;assert.equal(c.catalog().source,'control-plane-cache');
});
