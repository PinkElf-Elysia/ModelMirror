import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile,readdir,link,symlink,copyFile} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {spawn} from 'node:child_process';
import {AuthoringStore} from './store.mjs';
import {blankDocument,canonical,sha256} from './contracts.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {SessionRecords} from './session-storage.mjs';
import {OfflineFixtureProvider,OfflineSessionHost,OFFLINE_MODE} from './offline-session.mjs';
import {createAuthoringPluginBridge} from './runtime-plugins.mjs';

const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6b-tests/',import.meta.url));
await mkdir(root,{recursive:true});
const reject=(action,code)=>assert.rejects(async()=>action(),error=>error.code===code);
const entry=(id,changes={})=>({id,name:id,enabled:true,categoryId:null,roles:['user'],rule:'or',regex:false,keywords:['灯塔'],targets:['system'],order:0,depth:0,chance:100,mode:'regular',body:id,contents:[],...changes});
const reply=(text='原始助手回复\r\n\n<details><summary>状态</summary>灯塔</details>  ',finishReason='stop')=>({status:'completed',text,finishReason});
const original='  灯塔🐟\r\n玩家原始开局\n';
function document() {
  const d=blankDocument();d.metadata.name='通用灯塔档案';d.metadata.audience='female';
  d.sources.system='  SYSTEM\r\n<archive>原始系统</archive>\n';d.sources.prefix='PRE';d.sources.suffix='SUFFIX';d.settings.defaultMemory=1;
  d.model={selection:{catalogId:'unqualified-catalog',modelId:'unqualified'},parameters:{temperature:0.7,topP:0.8,maxOutputTokens:16384}};
  return d;
}
async function fixture(t,options={}) {
  const directory=await mkdtemp(join(root,'run-')),store=await AuthoringStore.open(join(directory,'works'));
  const d=options.document??document(),create=await store.create({operationId:'work',expectedRevision:null,document:d});
  const version=await store.publish(create.workId,{operationId:'publish',expectedRevision:0});
  const reference={workId:create.workId,versionId:version.result.versionId};
  const resolveRuntime=ref=>openPublishedRuntime(store,ref);
  let provider=await OfflineFixtureProvider.open(join(directory,'provider'),{replies:{one:reply(),two:reply('第二份完整原文'),three:reply('第三份'),...options.replies},fault:options.providerFault,storageFault:options.providerStorageFault});
  let host=await OfflineSessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider,draw:options.draw,fault:options.fault,storageFault:options.storageFault});
  const creation={operationId:'session',expectedRevision:null,reference,openingText:options.opening??original,presetId:options.presetId??null};
  const created=await host.create(creation),id=created.session.id;
  t.after(async()=>{await host.close();await provider.close();await store.close();});
  return {directory,store,reference,d,id,creation,get host(){return host;},get provider(){return provider;},
    async reopen(){await host.close();await provider.close();provider=await OfflineFixtureProvider.open(join(directory,'provider'));host=await OfflineSessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider});},
    async input(operationId='one',input=creation.openingText){return {sessionId:id,operationId,expectedRevision:(await host.read(id)).session.revision,input};},
    async recover(operationId='one'){return host.recover({sessionId:id,operationId,expectedRevision:(await host.read(id)).session.revision});},
  };
}

test('任意作品会话持久化、重复创建和重开；预算与模型/插件授权没有自动生成',async t=>{
  const f=await fixture(t),before=await readFile(join(f.directory,'works','works',f.reference.workId+'.json'));
  const first=(await f.host.read(f.id)).session;
  assert.equal(first.binding.openingText,original);assert.deepEqual(first.turns,[]);
  assert.equal((await f.host.create(f.creation)).replayed,true);
  assert.equal(f.host.readiness.realProvider,false);assert.equal(f.host.readiness.runtimeReady,false);
  for(const key of ['budget','ledger','grants','modelState'])assert.equal(Object.hasOwn(first,key),false);
  await f.reopen();assert.deepEqual((await f.host.read(f.id)).session,first);assert.equal(f.provider.dispatchCount,0);
  assert.deepEqual(await readFile(join(f.directory,'works','works',f.reference.workId+'.json')),before);
  await reject(()=>f.host.create({...f.creation,openingText:'changed'}),'OFFLINE_OPERATION_COLLISION');
  const other=await f.host.create({...f.creation,operationId:'copy'});assert.notEqual(other.session.id,f.id);assert.deepEqual(other.session.operations,[]);
});

test('派发前完整请求落盘；单 system、开局/前后置词只进入 user，原始回复不经显示转换',async t=>{
  let f,inspected=0;
  f=await fixture(t,{providerFault:async(phase,request)=>{if(phase==='afterAccepted'){
    const disk=JSON.parse(await readFile(join(f.directory,'sessions','records',f.id+'.json'),'utf8')).data;
    const op=disk.operations.at(-1);assert.equal(op.state,'dispatching');assert.deepEqual(op.request,request);inspected++;
  }}});
  const input=await f.input(),done=await f.host.send(input),request=done.operation.request;
  assert.equal(inspected,1);assert.equal(done.operation.state,'completed');assert.equal(f.provider.dispatchCount,1);
  assert.equal(request.mode,OFFLINE_MODE);assert.equal(request.messages[0].content,f.d.sources.system);
  assert.equal(request.messages.at(-1).content,'PRE\n\n'+original+'\n\nSUFFIX');
  assert.equal(request.messages.filter(m=>m.role==='system').length,1);
  assert.equal(request.messagesHash,sha256(canonical(request.messages)));
  const s=(await f.host.read(f.id)).session;assert.equal(s.draft,'');assert.equal(s.turns[0].user,original);
  assert.equal(s.turns[0].assistant,reply().text);assert.equal(s.turns[0].rawHash,sha256(reply().text));
  const replay=await f.host.send(input);assert.equal(replay.replayed,true);assert.equal(f.provider.dispatchCount,1);
  await reject(()=>f.host.send({...input,input:'冲突内容'}),'OFFLINE_OPERATION_COLLISION');
});

test('旧历史不重复包裹；世界书扫描原始输入/回复，默认记忆建议未授权时仍保留完整历史',async t=>{
  const d=document();d.worldbook.entries=[entry('prefix-only',{roles:['user'],depth:1,keywords:['PRE'],targets:['prefix']}),entry('old-player',{roles:['user'],depth:1}),entry('old-ai',{roles:['assistant'],depth:1,keywords:['details']})];
  const f=await fixture(t,{document:d});await f.host.send(await f.input());
  const done=await f.host.send(await f.input('two','接下来')),r=done.operation.request;
  assert.equal(r.messages.length,4);assert.equal(r.messages[1].content,'PRE\n\n'+original+'\n\nSUFFIX');assert.equal(r.messages[2].content,reply().text);
  assert.equal(r.worldbook.decisions.find(x=>x.entryId==='prefix-only').targets.length,0);
  assert.ok(r.worldbook.decisions.find(x=>x.entryId==='old-player').targets.length);assert.ok(r.worldbook.decisions.find(x=>x.entryId==='old-ai').targets.length);
  await f.host.send(await f.input('three','再次推进'));assert.equal((await f.host.read(f.id)).session.operations[2].request.messages.length,6);
  assert.equal((await f.host.read(f.id)).effectivePolicy.history,'full');
});

test('异步正则/前后置世界书与概率冻结；未知恢复不重抽或读取后来草稿',async t=>{
  const d=document();d.worldbook.entries=[entry('pre',{targets:['prefix'],body:'relay'}),entry('weighted',{keywords:['relay'],regex:true,chance:60,mode:'weighted',contents:[{text:'A',weight:1},{text:'B',weight:3}]})];
  let draws=0;const f=await fixture(t,{document:d,draw:()=>{draws++;return .2;},providerFault:async phase=>{if(phase==='afterReceipt')throw Error('connection lost');}});
  const input=await f.input(),unknown=await f.host.send(input);assert.equal(unknown.operation.state,'unknown');assert.equal(draws,2);
  const frozen=unknown.operation.request;assert.match(frozen.messages[0].content,/A/);
  const work=await f.store.read(f.reference.workId);work.draft.sources.system='未来信息';await f.store.save(f.reference.workId,{operationId:'later',expectedRevision:1,document:work.draft});
  assert.equal((await f.recover()).operation.state,'completed');assert.equal(draws,2);
  assert.deepEqual((await f.host.read(f.id)).session.operations[0].request,frozen);assert.equal(f.provider.dispatchCount,1);
  await f.reopen();assert.equal((await f.recover()).replayed,true);assert.equal(f.provider.dispatchCount,0);
});

test('预设对话必须显式选择、原样保存为例子；不伪造成真实来源回合',async t=>{
  const d=document();d.enhancements.dialogue={mode:'presets',opening:'',presets:[{id:'p',title:'示例',messages:[{user:'  示例提问',assistant:'示例回答\n'}],openings:['继续']}]};
  d.worldbook.entries=[entry('example-scan',{keywords:['示例'],roles:['assistant'],depth:20})];
  const f=await fixture(t,{document:d,presetId:'p'}),done=await f.host.send(await f.input());
  assert.deepEqual(done.operation.request.messages.slice(1,3),[{role:'user',content:'  示例提问'},{role:'assistant',content:'示例回答\n'}]);
  assert.equal(done.operation.request.worldbook.decisions[0].targets.length,0);assert.equal((await f.host.read(f.id)).session.turns.length,1);
  await reject(()=>f.host.create({...f.creation,operationId:'bad-preset',presetId:'missing'}),'OFFLINE_PRESET_INVALID');
});

test('同一发送并发只派发一次；另一标签的旧 revision 被拒绝',async t=>{
  const f=await fixture(t),input=await f.input();
  const [a,b]=await Promise.all([f.host.send(input),f.host.send(input)]);assert.equal(a.replayed,false);assert.equal(b.replayed,true);assert.equal(f.provider.dispatchCount,1);
  await reject(()=>f.host.send({...input,operationId:'two',input:'新输入'}),'OFFLINE_REVISION_CONFLICT');
});

test('调用抛错且无完成回执：未知阻断后续发送，保存原请求/草稿，重开与重复恢复均零重发',async t=>{
  const f=await fixture(t,{providerFault:async phase=>{if(phase==='afterAccepted')throw Error('unknown');}}),input=await f.input();
  const result=await f.host.send(input);assert.equal(result.operation.state,'unknown');
  assert.equal((await f.host.read(f.id)).session.draft,original);
  await reject(()=>f.host.send(awaitInput()),'OFFLINE_RESULT_PENDING');
  function awaitInput(){return {sessionId:f.id,operationId:'two',expectedRevision:result.revision,input:'后来输入'};}
  assert.equal((await f.host.send(input)).replayed,true);await f.recover();assert.equal(f.provider.dispatchCount,1);
  await f.reopen();assert.equal((await f.recover()).operation.state,'unknown');assert.equal(f.provider.dispatchCount,0);
  assert.deepEqual((await f.host.read(f.id)).session.turns,[]);
});

test('fixture 完成回执已落盘但应答丢失：按原 ID 恢复且只追加一次完整回合',async t=>{
  const f=await fixture(t,{providerFault:async phase=>{if(phase==='afterReceipt')throw Error('lost');}}),unknown=await f.host.send(await f.input());
  assert.equal(unknown.operation.state,'unknown');const frozen=unknown.operation.request;
  const recovered=await f.recover();assert.equal(recovered.operation.state,'completed');assert.deepEqual(recovered.operation.request,frozen);
  await f.recover();assert.equal((await f.host.read(f.id)).session.turns.length,1);assert.equal(f.provider.dispatchCount,1);
});

test('已派发标记写入失败时不会调用 fixture；显式恢复 prepared 为未派发',async t=>{
  let failOnce=true;const f=await fixture(t,{storageFault:async(phase,{data})=>{if(phase==='beforeCommit'&&data.operations?.at(-1)?.state==='dispatching'&&failOnce){failOnce=false;throw Error('disk failure');}}});
  await reject(()=>f.host.send(awaitInput()),'OFFLINE_WRITE_FAILED');
  function awaitInput(){return {sessionId:f.id,operationId:'one',expectedRevision:0,input:original};}
  assert.equal(f.provider.dispatchCount,0);assert.equal((await f.host.read(f.id)).session.operations[0].state,'prepared');
  assert.equal((await f.recover()).operation.state,'not_dispatched');assert.equal((await f.host.read(f.id)).session.draft,original);
  assert.equal((await f.host.send(await f.input('two'))).operation.state,'completed');
});

test('prepared 写入前失败：会话与 revision 不变，未派发，同一 ID 可明确重试',async t=>{
  let armed=true;const f=await fixture(t,{storageFault:async(phase,{data})=>{if(phase==='beforeCommit'&&data.operations?.at(-1)?.state==='prepared'&&armed){armed=false;throw Error('disk full');}}});
  const before=(await f.host.read(f.id)).session,input=await f.input();await reject(()=>f.host.send(input),'OFFLINE_WRITE_FAILED');
  assert.deepEqual((await f.host.read(f.id)).session,before);assert.equal(f.provider.dispatchCount,0);
  await f.host.send(input);assert.equal(f.provider.dispatchCount,1);
});

test('结果落盘前失败保留原始 fixture 回执；重开核对后恢复，不再次发送',async t=>{
  let armed=true;const f=await fixture(t,{storageFault:async(phase,{data})=>{if(phase==='beforeCommit'&&data.operations?.at(-1)?.state==='completed'&&armed){armed=false;throw Error('disk full');}}});
  const input=await f.input();await reject(()=>f.host.send(input),'OFFLINE_WRITE_FAILED');assert.equal((await f.host.read(f.id)).status,'unknown');
  await f.reopen();assert.equal((await f.recover()).operation.state,'completed');assert.equal(f.provider.dispatchCount,0);
});

test('结果原子替换后返回失败：本地 journal 恢复识别已提交，重复操作不重发',async t=>{
  let armed=true;const f=await fixture(t,{storageFault:async(phase,{data})=>{if(phase==='afterCommit'&&data.operations?.at(-1)?.state==='completed'&&armed){armed=false;throw Error('ack lost');}}});
  const input=await f.input();await reject(()=>f.host.send(input),'OFFLINE_WRITE_UNKNOWN');
  const recovered=await f.recover();assert.equal(recovered.operation.state,'completed');assert.equal(recovered.replayed,true);
  assert.equal((await f.host.send(input)).replayed,true);assert.equal(f.provider.dispatchCount,1);assert.equal((await f.host.read(f.id)).session.turns.length,1);
});

test('截断、空输出与已知失败保留原文，不进入完整历史或自动重试',async t=>{
  const f=await fixture(t,{replies:{one:reply('<details>未结束','length'),two:reply('  \n'),three:{status:'failed',text:'fixture failure raw',finishReason:'fixture_failure'}}});
  for(const [id,state,text] of [['one','incomplete','<details>未结束'],['two','invalid','  \n'],['three','failed','fixture failure raw']]){
    const outcome=await f.host.send(await f.input(id));assert.equal(outcome.operation.state,state);assert.equal(outcome.operation.receipt.result.text,text);
    assert.equal((await f.host.read(f.id)).pending,false);assert.equal((await f.host.read(f.id)).session.draft,original);
  }
  assert.equal((await f.host.read(f.id)).session.turns.length,0);assert.equal(f.provider.dispatchCount,3);
});

test('屏蔽、容量、未知字段、未配置 fixture 与非法开局在派发前拒绝',async t=>{
  const d=document();d.enhancements.blockedWords=['灯塔'];const f=await fixture(t,{document:d});
  await reject(()=>f.host.send(awaitInput()),'OFFLINE_INPUT_BLOCKED');
  function awaitInput(){return {sessionId:f.id,operationId:'one',expectedRevision:0,input:original};}
  assert.throws(()=>f.host.send({...awaitInput(),system:'injected'}),{code:'AUTHORING_UNKNOWN_FIELD'});
  await reject(()=>f.host.send({...awaitInput(),input:'不同开局'}),'OFFLINE_OPENING_MISMATCH');assert.equal(f.provider.dispatchCount,0);
  const g=await fixture(t);await reject(async ()=>g.host.send(await g.input('missing')),'OFFLINE_FIXTURE_MISSING');
  const h=await fixture(t,{opening:'字'.repeat(120001)});await reject(async ()=>h.host.send(await h.input()),'OFFLINE_INPUT_CAPACITY');
  assert.equal((await h.host.read(h.id)).session.operations.length,0);
});

test('拒绝自定义供应商、外部历史与 latest；异步前冻结输入',async t=>{
  const f=await fixture(t);
  await reject(()=>OfflineSessionHost.open({directory:join(f.directory,'fake'),resolveRuntime:()=>{},provider:{dispatch(){assert.fail();}}}),'OFFLINE_HOST_REQUIRED');
  await reject(()=>f.host.create({...f.creation,operationId:'latest',reference:{...f.reference,versionId:'latest'}}),'AUTHORING_VERSION_ID_INVALID');
  const input=await f.input(),pending=f.host.send(input);input.input='later mutation';input.operationId='other';
  const result=await pending;assert.equal(result.operation.operationId,'one');assert.equal(result.operation.request.rawInput,original);
});

test('持久化会话投影接入五插件桥；未知请求阻断提案，重新创建会话不继承授权',async t=>{
  const f=await fixture(t,{providerFault:async phase=>{if(phase==='afterAccepted')throw Error('unknown');}});
  const bridge=await createAuthoringPluginBridge({directory:join(f.directory,'plugins'),lookupSession:id=>f.host.lookupSession(id),resolveRuntime:ref=>openPublishedRuntime(f.store,ref)});
  t.after(()=>bridge.pluginService.close());const service=bridge.pluginService,pluginId='rpg.memory-palace';
  async function change(action,id=f.id){const c=await service.catalog(),p=c.plugins.find(x=>x.id===pluginId);return service.change(action,{operationId:action+'-memory',pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(action==='enable'?{sessionId:id,expectedSessionRevision:(await f.host.read(id)).session.revision,permissions:p.permissions}:{})});}
  await change('install');await change('enable');assert.equal((await service.sessionStatus(f.id,pluginId)).enabled,true);
  await f.host.send(await f.input());
  await assert.rejects(service.invoke({pluginId,sessionId:f.id,capability:'session.memory.request',input:{}}),/PENDING|BUSY/);
  const created=await f.host.create({...f.creation,operationId:'new-session'});assert.equal((await service.sessionStatus(created.session.id,pluginId)).enabled,false);
  assert.equal((await f.host.lookupSession(f.id)).pending,true);assert.equal(bridge.runtimeReady,false);
});

test('派发期间 busy 投影、关闭等待当前任务收束，不取消已接受的操作',async t=>{
  let signal,release;const accepted=new Promise(r=>signal=r),gate=new Promise(r=>release=r);
  const f=await fixture(t,{providerFault:async phase=>{if(phase==='afterAccepted'){signal();await gate;}}});
  const sending=f.host.send(await f.input());await accepted;assert.equal((await f.host.lookupSession(f.id)).busy,true);
  let closed=false;const closing=f.host.close().then(()=>closed=true);await new Promise(r=>setTimeout(r,15));assert.equal(closed,false);
  release();assert.equal((await sending).operation.state,'completed');await closing;assert.equal(closed,true);
  await f.reopen();assert.equal((await f.host.read(f.id)).session.turns.length,1);
});

async function child(code) {
  return new Promise((resolve,reject)=>{
    const p=spawn(process.execPath,['--input-type=module','-e',code],{stdio:['ignore','pipe','pipe'],windowsHide:true});let output='';
    p.stdout.on('data',b=>output+=b);p.stderr.on('data',b=>output+=b);p.on('error',reject);p.on('exit',exitCode=>resolve({exitCode,output}));
  });
}
for(const point of ['afterPrepared','beforeDispatch','afterProvider'])test('实际子进程退出并恢复：'+point+'，重启不重发',async t=>{
  const f=await fixture(t);await f.host.close();await f.provider.close();await f.store.close();
  const modules=Object.fromEntries(['store','runtime-binding','offline-session'].map(name=>[name,new URL('./'+name+'.mjs',import.meta.url).href]));
  const result=await child(`
    import {AuthoringStore} from ${JSON.stringify(modules.store)};
    import {openPublishedRuntime} from ${JSON.stringify(modules['runtime-binding'])};
    import {OfflineFixtureProvider,OfflineSessionHost} from ${JSON.stringify(modules['offline-session'])};
    const directory=${JSON.stringify(f.directory)};
    const store=await AuthoringStore.open(directory+'/works');
    const provider=await OfflineFixtureProvider.open(directory+'/provider',{replies:{one:${JSON.stringify(reply())}}});
    const host=await OfflineSessionHost.open({directory:directory+'/sessions',resolveRuntime:r=>openPublishedRuntime(store,r),provider,fault:async phase=>{if(phase===${JSON.stringify(point)})process.exit(73);}});
    await host.send({sessionId:${JSON.stringify(f.id)},operationId:'one',expectedRevision:0,input:${JSON.stringify(original)}});
  `);
  assert.equal(result.exitCode,73,result.output);
  await reject(()=>SessionRecords.open(join(f.directory,'sessions')),'OFFLINE_OWNER_RECOVERY_REQUIRED');
  const store=await AuthoringStore.open(join(f.directory,'works'),{recoverDeadOwner:true});
  const provider=await OfflineFixtureProvider.open(join(f.directory,'provider'),{recoverDeadOwner:true});
  const host=await OfflineSessionHost.open({directory:join(f.directory,'sessions'),resolveRuntime:r=>openPublishedRuntime(store,r),provider,recoverDeadOwner:true});
  t.after(async()=>{await host.close();await provider.close();await store.close();});
  const before=await host.read(f.id);assert.equal(provider.dispatchCount,0);assert.equal(before.session.turns.length,0);
  const recovered=await host.recover({sessionId:f.id,operationId:'one',expectedRevision:before.session.revision});
  assert.equal(recovered.operation.state,{afterPrepared:'not_dispatched',beforeDispatch:'unknown',afterProvider:'completed'}[point]);
  assert.equal(provider.dispatchCount,0);
  assert.equal((await host.read(f.id)).session.turns.length,point==='afterProvider'?1:0);
  assert.ok((await readdir(join(f.directory,'sessions'))).some(name=>name.startsWith('owner-dead-')));
});

test('独占锁、CAS、路径穿越、损坏文件与跨会话回执严格拒绝',async t=>{
  const directory=await mkdtemp(join(root,'records-')),store=await SessionRecords.open(directory);t.after(()=>store.close());
  await reject(()=>SessionRecords.open(directory),'OFFLINE_STORE_BUSY');assert.throws(()=>store.get('../escape'),{code:'OFFLINE_RECORD_ID_INVALID'});
  await store.put('one',null,{value:1});await reject(()=>store.put('one',null,{value:2}),'OFFLINE_STORAGE_CONFLICT');
  const target=join(directory,'records','one.json');await writeFile(target,'{"format":"broken"}');await assert.rejects(store.get('one'));
  const f=await fixture(t,{providerFault:async phase=>{if(phase==='afterReceipt')throw Error('lost');}});await f.host.send(await f.input());
  const op=(await f.host.read(f.id)).session.operations[0],path=join(f.directory,'provider','records',op.request.id+'.json');
  const envelope=JSON.parse(await readFile(path,'utf8'));envelope.data.receipt.requestId='request-'+'0'.repeat(64);envelope.sha256=sha256(canonical(envelope.data));await writeFile(path,canonical(envelope));
  await reject(()=>f.recover(),'OFFLINE_RECEIPT_MISMATCH');assert.equal((await f.host.read(f.id)).pending,true);
});

test('拒绝硬链接和符号链接目录，保留外部文件',async t=>{
  const directory=await mkdtemp(join(root,'unsafe-')),store=await SessionRecords.open(join(directory,'safe'));t.after(()=>store.close());
  const external=join(directory,'external.json');await writeFile(external,'{}');await link(external,join(directory,'safe','records','linked.json'));
  await reject(()=>store.get('linked'),'OFFLINE_PATH_UNSAFE');assert.equal(await readFile(external,'utf8'),'{}');
  await mkdir(join(directory,'outside'));await symlink(join(directory,'outside'),join(directory,'junction'),process.platform==='win32'?'junction':'dir');
  await reject(()=>SessionRecords.open(join(directory,'junction','data')),'OFFLINE_PATH_UNSAFE');
});

test('prepared 原子替换后不确定：storage journal 可核对，但不会隐式继续派发',async t=>{
  let armed=true;const f=await fixture(t,{storageFault:async(phase,{data})=>{if(phase==='afterCommit'&&data.operations?.at(-1)?.state==='prepared'&&armed){armed=false;throw Error('lost');}}});
  await reject(async ()=>f.host.send(await f.input()),'OFFLINE_WRITE_UNKNOWN');assert.equal(f.provider.dispatchCount,0);
  assert.equal((await f.recover()).operation.state,'not_dispatched');assert.equal(f.provider.dispatchCount,0);
});

test('同名作品新版和资源变化不能替换已有会话的精确版本',async t=>{
  const f=await fixture(t),old=(await f.host.read(f.id)).session.binding;
  const w=await f.store.read(f.reference.workId);w.draft.sources.system='NEW VERSION';await f.store.save(f.reference.workId,{operationId:'edit',expectedRevision:1,document:w.draft});
  const v=await f.store.publish(f.reference.workId,{operationId:'new-version',expectedRevision:2});
  const result=await f.host.send(await f.input());assert.equal(result.operation.request.messages[0].content,f.d.sources.system);
  const newer=await f.host.create({...f.creation,operationId:'newer',reference:{...f.reference,versionId:v.result.versionId}});
  assert.notEqual(newer.session.binding.runtimeHash,old.runtimeHash);assert.deepEqual(newer.session.turns,[]);
});

test('存储目录用途不能混用；不能自行构造或改写离线 fixture 为真实传输器',async t=>{
  const f=await fixture(t);assert.throws(()=>new OfflineFixtureProvider({}, {}, ()=>{}),{code:'OFFLINE_HOST_REQUIRED'});
  assert.throws(()=>{f.provider.dispatch=()=>assert.fail('transport replaced');},TypeError);
  await f.host.close();await reject(()=>OfflineFixtureProvider.open(join(f.directory,'sessions')),'OFFLINE_STORE_VERSION');
});

test('派发标记已替换但确认丢失：即使 fixture 未收到也保持未知，不能推断可重试',async t=>{
  let once=true;const f=await fixture(t,{storageFault:async(phase,{data})=>{if(once&&phase==='afterCommit'&&data.operations?.at(-1)?.state==='dispatching'){once=false;throw Error('lost');}}});
  const input=await f.input();await reject(()=>f.host.send(input),'OFFLINE_WRITE_UNKNOWN');assert.equal(f.provider.dispatchCount,0);
  assert.equal((await f.recover()).operation.state,'unknown');assert.equal((await f.host.send(input)).replayed,true);assert.equal(f.provider.dispatchCount,0);
});

test('fixture 回执原子替换后的确认丢失，可独立核对其 journal 再收束会话',async t=>{
  let once=true;const f=await fixture(t,{providerStorageFault:async(phase,{data})=>{if(once&&phase==='afterCommit'&&data.state==='completed'){once=false;throw Error('receipt ack lost');}}});
  assert.equal((await f.host.send(await f.input())).operation.state,'unknown');
  assert.equal((await f.recover()).operation.state,'completed');assert.equal(f.provider.dispatchCount,1);assert.equal((await f.host.read(f.id)).session.turns.length,1);
});

test('世界书注入容量不足明确阻断，既不截断正文也不派发',async t=>{
  const d=document();d.worldbook.entries=Array.from({length:40},(_,i)=>entry('large-'+i,{body:'字'.repeat(30000)}));
  const f=await fixture(t,{document:d}),input=await f.input();
  await reject(()=>f.host.send(input),'AUTHORING_WORLDBOOK_INJECTION_CAPACITY');assert.equal(f.provider.dispatchCount,0);assert.equal((await f.host.read(f.id)).session.operations.length,0);
});

test('会话内容损坏不能隐藏在合法外层 hash 后：回合与请求错配拒绝读取',async t=>{
  const f=await fixture(t);await f.host.send(await f.input());
  const path=join(f.directory,'sessions','records',f.id+'.json'),envelope=JSON.parse(await readFile(path,'utf8'));
  envelope.data.turns[0].assistant='different';envelope.sha256=sha256(canonical(envelope.data));await writeFile(path,canonical(envelope));
  await reject(()=>f.host.read(f.id),'OFFLINE_SESSION_CORRUPT');assert.equal(f.provider.dispatchCount,1);
});

test('离线实现源码漂移在独立副本上验证，拒绝新建，不改当前预览或已有源码',async t=>{
  const f=await fixture(t),runtime=await openPublishedRuntime(f.store,f.reference),directory=join(f.directory,'isolated-source');
  const paths=new Set([...Object.keys(runtime.descriptor.artifactHashes),...Object.keys(f.host.readiness.artifactHashes).map(p=>'authoring/'+p)]);
  const moduleRoot=fileURLToPath(new URL('../',import.meta.url));
  for(const path of paths){const target=join(directory,path);await mkdir(dirname(target),{recursive:true});await copyFile(join(moduleRoot,path),target);}
  const copied=await import(pathToFileURL(join(directory,'authoring','offline-session.mjs')).href);
  const provider=await copied.OfflineFixtureProvider.open(join(f.directory,'copy-provider'));
  const host=await copied.OfflineSessionHost.open({directory:join(f.directory,'copy-sessions'),provider,resolveRuntime:()=>assert.fail('must stop before resolution')});
  t.after(async()=>{await host.close();await provider.close();});
  const source=join(directory,'authoring','model-plugin.mjs');await writeFile(source,(await readFile(source,'utf8'))+'\n// deliberate isolated drift\n');
  await reject(()=>host.create(f.creation),'OFFLINE_FILES_CHANGED_RESTART_REQUIRED');assert.equal(provider.dispatchCount,0);
});

test('后续请求未知不丢失已完成回合和当前草稿，重启后两者原样可查',async t=>{
  const f=await fixture(t,{providerFault:async(phase,request)=>{if(phase==='afterAccepted'&&request.operationId==='two')throw Error('unknown');}});
  await f.host.send(await f.input());const earlier=(await f.host.read(f.id)).session.turns;
  const input=await f.input('two','  下一轮原始草稿\r\n');await f.host.send(input);
  await f.reopen();await f.recover('two');const s=(await f.host.read(f.id)).session;
  assert.deepEqual(s.turns,earlier);assert.equal(s.draft,input.input);assert.equal(s.operations[1].request.rawInput,input.input);
  assert.equal(f.provider.dispatchCount,0);
});

test('same-record reads reuse only the successful content check; disk tampering and changed hashes still fail',async t=>{
 const directory=await mkdtemp(join(root,'checked-read-')), records=await SessionRecords.open(directory);
 t.after(()=>records.close());
 let calls=0;
 const verify=value=>{calls++;assert.equal(value.checked,true);};
 const id='test-record';const saved=await records.put(id,null,{checked:true,revision:1});
 const first=await records.get(id,verify);first.data.checked=false;
 assert.equal((await records.get(id,verify)).data.checked,true);assert.equal(calls,1);
 await records.put(id,saved.hash,{checked:true,revision:2});await records.get(id,verify);assert.equal(calls,2);
 const file=join(directory,'records',id+'.json'),envelope=JSON.parse(await readFile(file,'utf8'));
 envelope.data.checked=false;await writeFile(file,JSON.stringify(envelope));
 await reject(()=>records.get(id,verify),'OFFLINE_STORE_CORRUPT');assert.equal(calls,2);
 envelope.sha256=sha256(canonical(envelope.data));await writeFile(file,JSON.stringify(envelope));
 await assert.rejects(()=>records.get(id,verify));await assert.rejects(()=>records.get(id,verify));assert.equal(calls,4);
});
