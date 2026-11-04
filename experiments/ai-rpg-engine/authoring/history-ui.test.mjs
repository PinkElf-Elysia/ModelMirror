import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {spawn} from 'node:child_process';
import {startAuthoringServer} from './server.mjs';
import {OfflineFixtureProvider} from './offline-session.mjs';
import {OfflineModelControl} from './model-control.mjs';
import {blankDocument,sha256,canonical} from './contracts.mjs';
import {historyIntent,historyConfig,historyReceiptMatches,pendingHistoryOperation} from './history-ui.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6g/tests/',import.meta.url));await mkdir(root,{recursive:true});
const api='/rpg-app/api/authoring',hp='/history-settings',mp='/model-settings';
const catalog={catalogId:'history-settings',models:[{selectionId:'route',selectionRevision:sha256('route'),modelId:'fixture/story',name:'离线模型',available:true,contextLength:128000,maxOutputTokens:32000,parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:32000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}}]};
async function fixture(t,options={}){
  const directory=await mkdtemp(join(root,'run-')),control=new OfflineModelControl(catalog);
  let host,provider;
  async function open(faults=true){
    provider=await OfflineFixtureProvider.open(join(directory,'provider'),{replies:Object.fromEntries(['one','two','three','four'].map(id=>[id,{status:'completed',text:id==='one'?'古老灯塔第一轮':id,finishReason:'stop'}])),fault:faults?options.providerFault:undefined});
    host=await startAuthoringServer({directory:join(directory,'works'),modelSettings:{directory:join(directory,'sessions'),provider,control},historySettings:{directory:join(directory,'history'),...(faults&&options.fault?{fault:options.fault}: {})}});
  }
  await open();t.after(async()=>{await host.close();await provider.close();});
  const request=async(path,body,headers={})=>{const r=await fetch(host.origin+api+path,body===undefined?{headers}:{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1',...headers},body:JSON.stringify(body)});return {status:r.status,value:await r.json()};};
  const ok=async(path,body)=>{const r=await request(path,body);assert.equal(r.status,200,JSON.stringify(r.value));return r.value;};
  const document=blankDocument();document.metadata.name='任意作品 '+randomUUID();document.metadata.audience='female';document.sources.system='SYSTEM';document.sources.prefix='PRE';document.sources.suffix='POST';document.settings.defaultMemory=1;
  document.worldbook.entries=[{id:'old-ai',name:'早期原文',enabled:true,categoryId:null,roles:['assistant'],rule:'or',regex:false,keywords:['古老灯塔'],targets:['system'],order:0,depth:20,chance:100,mode:'regular',body:'OLD_FACT',contents:[]}];
  const work=await ok('/works',{operationId:'work',expectedRevision:null,document});
  const version=(await ok('/works/'+work.workId+'/publish',{operationId:'publish',expectedRevision:0})).result.versionId;
  const creation={operationId:'session',expectedRevision:null,reference:{workId:work.workId,versionId:version},openingText:'原始开局🐟',presetId:null};
  const session=(await ok(mp+'/sessions',creation)).session;
  const f={directory,document,work,creation,id:session.id,request,ok,get host(){return host;},get provider(){return provider;},
    read:()=>ok(hp+'/sessions/'+session.id),
    async intent(action,config={turns:1,includeInitialCharacter:false},op=randomUUID()){return historyIntent(await f.read(),action,op,config);},
    async change(action,config,op){return ok(hp+'/change',await f.intent(action,config,op));},
    async enable(){await f.change('install');await f.change('enable');},
    async select(){const s=(await host.sessionHost.read(session.id)).session,c=control.catalog();return ok(mp+'/configure',{sessionId:session.id,operationId:'model',expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose:'story',choice:{catalogRevision:c.revision,selectionId:'route',selectionRevision:sha256('route'),parameters:{maxOutputTokens:4096}}});},
    async send(operationId){const v=await host.sessionHost.read(session.id);return host.sessionHost.send({sessionId:session.id,operationId,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,input:operationId==='one'?'原始开局🐟':'继续 '+operationId});},
    async stop(){await host.close();await provider.close();},
    async reopen(){await host.close();await provider.close();await open(false);},
  };return f;
}
const error=async(f,intent,code)=>{const r=await f.request(hp+'/change',intent);assert.equal(r.value.error?.code,code,JSON.stringify(r));return r;};

test('默认入口关闭；静态前端不能读取宿主或发送请求',async t=>{
  const dir=await mkdtemp(join(root,'disabled-')),host=await startAuthoringServer({directory:dir});t.after(()=>host.close());
  assert.deepEqual(await(await fetch(host.origin+api+hp)).json(),{enabled:false,realProvider:false});
  assert.equal((await fetch(host.origin+'/history-ui.mjs')).status,200);
  for(const p of ['/history-settings.mjs','/history-control.mjs','/session-storage.mjs'])assert.equal((await fetch(host.origin+p)).status,404);
  assert.equal((await fetch(host.origin+api+hp+'/sessions/session-'+ 'a'.repeat(64))).status,404);
});
test('安装与授权分开；作品建议不自动生效；授权保存均零调用',async t=>{
  const f=await fixture(t),before=await f.read();assert.equal(before.suggestion,1);assert.equal(before.history.config.turns,3);assert.equal(before.plugin.installed,false);assert.equal(before.effectivePolicy.history,'full');
  await error(f,await f.intent('configure'),'PLUGIN_NOT_INSTALLED');
  await f.change('install');assert.equal((await f.read()).plugin.installed,true);assert.equal((await f.read()).effectivePolicy.history,'full');
  await error(f,await f.intent('configure'),'PLUGIN_NOT_AUTHORIZED');
  await f.change('enable');assert.equal((await f.read()).history.config.turns,3);
  const saved=await f.change('configure');assert.equal(saved.settings.history.config.turns,1);assert.equal(saved.settings.effectivePolicy.history,'window');assert.equal(saved.settings.history.revision,1);assert.equal(saved.settings.sessionRevision,1);assert.equal(f.provider.dispatchCount,0);
});
test('拒绝跨源、额外受信字段、伪造权限、非法长度',async t=>{
  const f=await fixture(t),input=await f.intent('install');
  assert.equal((await f.request(hp+'/change',input,{Origin:'https://foreign.invalid'})).status,403);
  assert.equal((await f.request(hp+'/sessions/'+f.id,undefined,{Origin:'https://foreign.invalid'})).status,403);
  for(const extra of [{system:'forged'},{history:[]},{providerUrl:'https://foreign.invalid'}])await error(f,{...input,...extra},'AUTHORING_UNKNOWN_FIELD');
  await f.change('install');await error(f,{...await f.intent('enable'),permissions:[]},'PLUGIN_PERMISSION_MISMATCH');await f.change('enable');
  for(const turns of [0,51,1.2])await error(f,{...await f.intent('configure'),config:{turns,includeInitialCharacter:false}},'HISTORY_CONFIGURATION_INVALID');
  assert.equal((await f.read()).registryRevision,2);assert.equal(f.provider.dispatchCount,0);
});
test('重复操作不重复授权；同 ID 的完整意图变化被拒绝',async t=>{
  const f=await fixture(t),input=await f.intent('install');const first=await f.ok(hp+'/change',input),second=await f.ok(hp+'/change',input);
  assert.equal(second.replayed,true);assert.deepEqual(first.receipt,second.receipt);assert.equal(second.settings.registryRevision,1);
  await error(f,{...input,expectedHistoryRevision:1},'HISTORY_OPERATION_COLLISION');
  await f.change('enable');const config=await f.intent('configure');await f.ok(hp+'/change',config);assert.equal((await f.ok(hp+'/change',config)).settings.history.revision,1);
  await error(f,{...config,config:{turns:2,includeInitialCharacter:false}},'HISTORY_OPERATION_COLLISION');
});
test('多标签冻结会话、配置、授权和注册表版本；不静默套用新版本',async t=>{
  const f=await fixture(t);await f.enable();const a=await f.intent('configure'),b=await f.intent('configure');await f.ok(hp+'/change',a);
  await error(f,b,'OFFLINE_REVISION_CONFLICT');
  const c=await f.intent('configure');await error(f,{...c,expectedHistoryRevision:0},'HISTORY_REVISION_CONFLICT');
  await error(f,{...c,expectedContextPolicyRevision:'a'.repeat(64)},'HISTORY_POLICY_CONFLICT');
  await error(f,{...c,expectedRegistryRevision:0},'PLUGIN_REGISTRY_CONFLICT');
});
test('HTTP 配置实际改变宿主请求窗口；世界书仍读窗口外完整原文',async t=>{
  const f=await fixture(t);await f.select();await f.enable();await f.change('configure');await f.send('one');await f.send('two');
  const third=await f.send('three'),messages=third.operation.request.messages;
  assert.equal(messages.length,4);assert.match(messages[0].content,/OLD_FACT/);assert.equal(messages[2].content,'two');assert.doesNotMatch(messages[1].content,/原始开局/);
  const view=await f.host.sessionHost.read(f.id);assert.match(view.session.turns[0].assistant,/古老灯塔/);assert.equal(view.session.turns.length,3);
  await f.change('disable');assert.equal((await f.read()).effectivePolicy.history,'full');assert.equal((await f.read()).history.config.turns,1);
  assert.equal((await f.send('four')).operation.request.messages.length,8);
});
test('卸载重装保留设置与历史，不恢复旧授权；新会话不继承授权',async t=>{
  const f=await fixture(t);await f.enable();await f.change('configure');await f.change('uninstall');assert.equal((await f.read()).history.revision,1);
  await f.change('install');assert.equal((await f.read()).effectivePolicy.history,'full');
  const next=(await f.ok(mp+'/sessions',{...f.creation,operationId:'second-session'})).session;
  const view=await f.ok(hp+'/sessions/'+next.id);assert.equal(view.plugin.installed,true);assert.equal(view.effectivePolicy.history,'full');assert.equal(view.history.config.turns,3);
  const bytes=canonical(await f.read());await f.reopen();assert.equal(canonical(await f.read()),bytes);assert.equal(f.provider.dispatchCount,0);
});
for(const action of ['install','enable','disable','uninstall','configure'])test(action+' 写入后回执丢失，原操作只读核对且重启不重放',async t=>{
  let failNext=false;
  const f=await fixture(t,{fault:phase=>{if(failNext&&phase==='afterChange'){failNext=false;throw Error('offline lost receipt');}}});
  if(action!=='install')await f.change('install');
  if(['disable','configure','uninstall'].includes(action))await f.change('enable');
  const input=await f.intent(action);failNext=true;await error(f,input,'HISTORY_SETTINGS_RESULT_UNKNOWN');
  const before=await f.read();assert.deepEqual(before.unconfirmed,input);
  await error(f,input,'HISTORY_SETTINGS_RESULT_UNKNOWN');
  await f.reopen();const after=await f.read();assert.deepEqual(after.unconfirmed,input);
  const recovered=await f.ok(hp+'/reconcile',input);assert.equal(historyReceiptMatches(recovered,input),true);
  assert.equal(recovered.settings.registryRevision,before.registryRevision);assert.equal(recovered.settings.history.revision,before.history.revision);assert.equal(recovered.settings.unconfirmed,null);assert.equal(f.provider.dispatchCount,0);
  assert.equal(historyReceiptMatches(await f.ok(hp+'/reconcile',input),input),true);
});
test('提交前故障：恢复不执行；明确重试沿用原 ID；未知配置阻断其他配置',async t=>{
  let once=true;const f=await fixture(t,{fault:phase=>{if(once&&phase==='beforeChange'){once=false;throw Error('offline before change');}}});
  const input=await f.intent('install');await error(f,input,'HISTORY_SETTINGS_RESULT_UNKNOWN');
  assert.equal((await f.read()).plugin.installed,false);await error(f,await f.intent('install'),'HISTORY_SETTINGS_RESULT_PENDING');
  const s=(await f.host.sessionHost.read(f.id)).session;
  const model=await f.request(mp+'/configure',{sessionId:f.id,operationId:'model',expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose:'story',choice:{catalogRevision:'a'.repeat(64),selectionId:'route',selectionRevision:sha256('route'),parameters:{maxOutputTokens:4096}}});assert.equal(model.value.error.code,'HISTORY_SETTINGS_RESULT_PENDING');
  const r=await f.ok(hp+'/reconcile',input);assert.equal(r.state,'not_applied');assert.equal(r.settings.plugin.installed,false);
  const retry=await f.ok(hp+'/change',input);assert.equal(retry.state,'applied');assert.equal(retry.settings.registryRevision,1);assert.equal(f.provider.dispatchCount,0);
});
test('未提交操作恢复不创建回执；历史/模型操作编号不能改作授权',async t=>{
  const f=await fixture(t),input=await f.intent('install');const r=await f.ok(hp+'/reconcile',input);assert.equal(r.state,'not_submitted');assert.equal(r.settings.registryRevision,0);
  await error(f,{...input,operationId:'session'},'HISTORY_OPERATION_COLLISION');await f.select();await error(f,await f.intent('install',undefined,'model'),'HISTORY_OPERATION_COLLISION');
});
test('剧情未知时禁止授权或改配置，撤权可行且不抹去未知结果',async t=>{
  const f=await fixture(t,{providerFault:phase=>{if(phase==='afterAccepted')throw Error('offline unknown');}});await f.select();await f.enable();await f.send('one');
  const before=await f.host.sessionHost.read(f.id);assert.equal(before.pending,true);
  await error(f,await f.intent('configure'),'OFFLINE_RESULT_PENDING');await f.change('disable');
  assert.equal((await f.read()).effectivePolicy.history,'full');assert.equal((await f.host.sessionHost.read(f.id)).pending,true);
  await error(f,await f.intent('enable'),'OFFLINE_RESULT_PENDING');assert.equal(f.provider.dispatchCount,1);
});
test('表单校验与回执比对不把布尔或分数当回合；本地待核对记录按会话隔离',()=>{
  assert.deepEqual(historyConfig({turns:'50',includeInitialCharacter:true}),{turns:50,includeInitialCharacter:true});
  for(const turns of ['',true,0,51,'1.2'])assert.throws(()=>historyConfig({turns,includeInitialCharacter:false}));
  const input={operationId:'one',sessionId:'session-'+'a'.repeat(64)},storage={};
  Object.defineProperty(storage,'getItem',{value:k=>storage[k]});storage['modelmirror.authoring.history-pending/'+input.sessionId+'/one']=JSON.stringify(input);
  assert.deepEqual(pendingHistoryOperation(storage,input.sessionId),input);assert.equal(pendingHistoryOperation(storage,'other'),null);
  assert.equal(historyReceiptMatches({state:'applied',intent:input,receipt:{operationId:'one'},settings:{sessionId:input.sessionId}},input),true);
  assert.equal(historyReceiptMatches({state:'applied',intent:{...input,action:'enable'},receipt:{operationId:'one'},settings:{sessionId:input.sessionId}},input),false);
});

for(const state of ['prepared','applied'])test('操作日志 '+state+' 落盘回执未知，核对本地存储不重复执行',async t=>{
  let once=true;const f=await fixture(t,{fault:(phase,record)=>{if(once&&phase==='journal:afterCommit'&&record.data.operations.at(-1).state===state){once=false;throw Error('journal commit receipt lost');}}});
  const input=await f.intent('install'),r=await f.request(hp+'/change',input);
  assert.notEqual(r.status,200);assert.equal((await f.read()).plugin.installed,state==='applied');
  const recovered=await f.ok(hp+'/reconcile',input);assert.equal(recovered.state,state==='applied'?'applied':'not_applied');
  assert.equal(f.provider.dispatchCount,0);assert.equal(recovered.settings.registryRevision,state==='applied'?1:0);
});
for(const phase of ['beforeChange','afterChange'])test('真实进程退出 '+phase+'；新进程只恢复原操作，不自动重新授权',async t=>{
  const f=await fixture(t),input=await f.intent('install');await f.stop();
  const script=join(f.directory,'crash.mjs');
  await writeFile(script,`import {startAuthoringServer} from ${JSON.stringify(new URL('./server.mjs',import.meta.url).href)};
import {OfflineFixtureProvider} from ${JSON.stringify(new URL('./offline-session.mjs',import.meta.url).href)};
import {OfflineModelControl} from ${JSON.stringify(new URL('./model-control.mjs',import.meta.url).href)};
import {join} from 'node:path';
const dir=${JSON.stringify(f.directory)};
const provider=await OfflineFixtureProvider.open(join(dir,'provider'));
const host=await startAuthoringServer({directory:join(dir,'works'),modelSettings:{directory:join(dir,'sessions'),provider,control:new OfflineModelControl(${JSON.stringify(catalog)})},historySettings:{directory:join(dir,'history'),fault:phase=>{if(phase===${JSON.stringify(phase)})process.exit(42);}}});
await fetch(host.origin+${JSON.stringify(api+hp+'/change')},{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1'},body:${JSON.stringify(JSON.stringify(input))}});
process.exit(3);`);
  const result=await new Promise((resolve,reject)=>{const child=spawn(process.execPath,[script],{windowsHide:true,stdio:['ignore','pipe','pipe']});let out='';child.stderr.on('data',b=>{out+=b;});child.on('error',reject);child.on('close',code=>resolve({code,out}));});
  assert.equal(result.code,42,result.out);
  const provider=await OfflineFixtureProvider.open(join(f.directory,'provider'),{recoverDeadOwner:true});
  const host=await startAuthoringServer({directory:join(f.directory,'works'),recoverDeadOwner:true,modelSettings:{directory:join(f.directory,'sessions'),provider,control:new OfflineModelControl(catalog)},historySettings:{directory:join(f.directory,'history')}});
  try{
    const url=host.origin+api+hp;
    const before=await(await fetch(url+'/sessions/'+f.id)).json();assert.equal(before.plugin.installed,phase==='afterChange');assert.deepEqual(before.unconfirmed,input);
    const r=await fetch(url+'/reconcile',{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(input)}),v=await r.json();assert.equal(r.status,200,JSON.stringify(v));
    assert.equal(v.state,phase==='afterChange'?'applied':'not_applied');assert.equal(provider.dispatchCount,0);
  }finally{await host.close();await provider.close();}
});
