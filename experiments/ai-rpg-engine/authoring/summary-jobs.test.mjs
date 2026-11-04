import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {AuthoringStore} from './store.mjs';
import {blankDocument,canonical,sha256} from './contracts.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {OfflineFixtureProvider} from './offline-session.mjs';
import {SummarySessionHost,SUMMARY_SESSION_FORMAT} from './summary-session.mjs';
import {summaryJobId,compressionJobId,validateSummaryJobs,freezeSummaryJob,SUMMARY_RECEIPT_RESERVE} from './summary-jobs.mjs';
import {OfflineModelControl} from './model-control.mjs';
import {createAuthoringHistoryControl} from './history-control.mjs';

const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6i-tests/',import.meta.url));await mkdir(root,{recursive:true});
const digest=value=>sha256(canonical(value)),clone=value=>structuredClone(value);
const reject=(action,code)=>assert.rejects(async()=>action(),error=>{assert.equal(error.code,code,error.stack);return true;});
const reply=(text,finishReason='stop',status='completed')=>({status,text,finishReason});
const opening='  原始角色开局🐟\r\n';
const sessionId='session-'+sha256('summary-session:session');
const H='rpg.history-window',M2='rpg.rolling-summary',M3='rpg.memory-palace';
const deferred=()=>{let resolve;return {promise:new Promise(r=>{resolve=r;}),resolve:v=>resolve(v)};};
const makeCatalog=(contextLength=128000)=>({catalogId:'offline-summary-fixture',models:['story','summary','other'].map(id=>({
  selectionId:id,selectionRevision:sha256(id),modelId:'fixture/'+id,name:'离线 '+id,available:true,contextLength,maxOutputTokens:16000,
  parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]},
}))});
function makeReplies(summaryText='旧承诺仍待完成。') {
  const replies={one:reply('古老灯塔🐟\r\n<details>卡内记忆：第一回合</details>'),two:reply('完整原文二'),three:reply('完整原文三'),four:reply('完整原文四')};
  for(const id of ['one','two','three','four']){const task=summaryJobId(sessionId,id);replies[task]=reply(summaryText);replies[compressionJobId(task)]=reply('压缩摘要：承诺尚未兑现。');}
  for(const id of ['manual','retry']){replies[id]=reply(summaryText);replies[compressionJobId(id)]=reply('压缩后的人工重试摘要。');}
  return replies;
}
async function fixture(t,options={}) {
  const directory=await mkdtemp(join(root,'run-'));
  let store=await AuthoringStore.open(join(directory,'works'));
  const document=blankDocument();document.metadata.name='非卡片绑定 '+randomUUID();document.metadata.audience='male';
  document.sources.system=' FROZEN SYSTEM\r\n';document.sources.prefix='PRE';document.sources.suffix='POST';document.settings.defaultMemory=1;
  document.worldbook.entries=[{id:'old-ai',name:'原始历史命中',enabled:true,categoryId:null,roles:['assistant'],rule:'or',regex:false,
    keywords:['古老灯塔'],targets:['system'],order:0,depth:20,chance:100,mode:'regular',body:'OLD_FACT',contents:[]}];
  options.document?.(document);
  const work=await store.create({operationId:'work',expectedRevision:null,document});
  const pub=await store.publish(work.workId,{operationId:'publish',expectedRevision:0}),reference={workId:work.workId,versionId:pub.result.versionId};
  const resolveRuntime=ref=>openPublishedRuntime(store,ref),modelControl=new OfflineModelControl(options.catalog??makeCatalog());
  let host,control,provider;
  async function open(recoverDeadOwner=false,faults=true) {
    control=await createAuthoringHistoryControl({directory:join(directory,'plugins'),lookupSession:id=>host.lookupSession(id),resolveRuntime,recoverDeadOwner});
    provider=await OfflineFixtureProvider.open(join(directory,'provider'),{replies:options.replies??makeReplies(options.summaryText),recoverDeadOwner,fault:faults?options.providerFault:undefined});
    host=await SummarySessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider,modelControl,historyControl:control,recoverDeadOwner,
      fault:faults?options.fault:undefined,storageFault:faults?options.storageFault:undefined});
  }
  await open();
  const creation={operationId:'session',expectedRevision:null,reference,openingText:opening,presetId:null};
  const id=(await host.create(creation)).session.id;assert.equal(id,sessionId);
  async function close(){await host.close();await control.close();await provider.close();}
  t.after(async()=>{await close();await store.close();});
  const f={directory,id,reference,creation,resolveRuntime,modelControl,get store(){return store;},get host(){return host;},get control(){return control;},get service(){return control.pluginService;},get provider(){return provider;},
    async read(){return host.read(id);},async idle(){return host.waitForIdle(id);},
    async select(purpose='story',selectionId=purpose){const s=(await f.read()).session,c=modelControl.catalog();return host.configureModel({sessionId:id,operationId:randomUUID(),expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,
      choice:selectionId===null?null:{catalogRevision:c.revision,selectionId,selectionRevision:sha256(selectionId),parameters:{maxOutputTokens:4096}}});},
    async input(operationId='one',input=opening){const v=await f.read();return {sessionId:id,operationId,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,input};},
    async send(...args){return host.send(await f.input(...args));},
    async sendIdle(...args){const result=await f.send(...args);await f.idle();return result;},
    async recovery(operationId=summaryJobId(id,'two')){const s=(await f.read()).session;return {sessionId:id,operationId,expectedRevision:s.revision,expectedSummaryRevision:s.summary.snapshot.revision};},
    async recover(...args){return host.recoverSummary(await f.recovery(...args));},
    async summaryInput(operationId='manual'){const v=await f.read();return {sessionId:id,operationId,expectedRevision:v.session.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision};},
    async update(...args){return host.requestSummary(await f.summaryInput(...args));},
    async configureHistory(){const v=await f.read();return host.configureHistory({sessionId:id,operationId:randomUUID(),expectedRevision:v.session.revision,expectedHistoryRevision:v.session.history.revision,expectedContextPolicyRevision:v.historyConfigurationPolicyRevision,config:{turns:1,includeInitialCharacter:false}});},
    async change(action,pluginId=M2,otherId=id){const c=await f.service.catalog(),p=c.plugins.find(p=>p.id===pluginId);return f.service.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,
      ...(['enable','disable'].includes(action)?{sessionId:otherId,expectedSessionRevision:(await host.lookupSession(otherId)).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},
    async enable(pluginId=M2){await f.change('install',pluginId);await f.change('enable',pluginId);},
    async reopen(recoverDeadOwner=false){await close();await store.close();store=await AuthoringStore.open(join(directory,'works'),{recoverDeadOwner});await open(recoverDeadOwner,false);},
    async close(){await close();await store.close();},
  };
  if(options.select!==false)await f.select();if(options.summaryModel!==false)await f.select('summary');if(options.enabled!==false)await f.enable();return f;
}

test('新宿主要求受信 fixture/模型/授权控制器；安装与授权独立',async t=>{
  const f=await fixture(t,{enabled:false});const v=await f.read();assert.equal(v.session.format,SUMMARY_SESSION_FORMAT);
  assert.equal(v.effectivePolicy.mode,'full-history');assert.equal(f.host.readiness.realProvider,false);assert.equal(f.host.readiness.runtimeReady,false);
  await f.change('install');assert.equal((await f.read()).effectivePolicy.mode,'full-history');
  await reject(()=>SummarySessionHost.open({directory:join(f.directory,'fake'),resolveRuntime:f.resolveRuntime,provider:{dispatch:()=>{}},modelControl:f.modelControl,historyControl:f.control}),'OFFLINE_HOST_REQUIRED');
  await f.sendIdle();await f.sendIdle('two','继续');assert.equal(f.provider.dispatchCount,2);assert.equal((await f.read()).session.summary.jobs.length,0);
});

test('独立摘要模型必须选择；首回合不需要摘要任务',async t=>{
  const f=await fixture(t,{summaryModel:false});await reject(()=>f.send(),'SUMMARY_MODEL_REQUIRED');assert.equal(f.provider.dispatchCount,0);
  await f.select('summary');await f.sendIdle();const v=await f.read();assert.equal(v.session.summary.jobs.length,0);assert.equal(v.session.turns.length,1);
  await f.select('story','other');assert.equal((await f.read()).session.models.current.summary.entry.modelId,'fixture/summary');
});

test('剧情原文落盘后更新；下一剧情使用单 system 摘要加最近一回合；世界书扫描原文',async t=>{
  const f=await fixture(t);await f.sendIdle();await f.sendIdle('two','第二轮原文');
  const v=await f.read(),job=v.session.summary.jobs[0];assert.equal(job.state,'applied');assert.equal(f.provider.dispatchCount,3);
  assert.equal(v.session.summary.snapshot.versions[0].coveredThrough,1);assert.equal(job.request.modelChoice.entry.modelId,'fixture/summary');
  const payload=JSON.parse(job.request.messages[1].content);assert.deepEqual(payload.turns,[{role:'user',content:opening},{role:'assistant',content:v.session.turns[0].assistant}]);
  assert.ok(!job.request.messages[1].content.includes('PRE'));assert.ok(job.request.messages[1].content.includes('卡内记忆'));
  const frozen=clone(v.session.turns),r=(await f.sendIdle('three','第三轮')).operation.request;
  assert.equal(r.messages.filter(m=>m.role==='system').length,1);assert.ok(r.messages[0].content.includes('旧承诺'));assert.ok(r.messages[0].content.includes('OLD_FACT'));
  assert.equal(r.messages.length,4);assert.equal(r.messages[1].content,frozen[1].userMessage);assert.equal(r.messages[2].content,frozen[1].assistant);
  assert.equal((r.messages[1].content.match(/PRE/g)??[]).length,1);assert.deepEqual((await f.read()).session.turns.slice(0,2),frozen);
  await writeFile(join(f.directory,'captured-story-request.json'),JSON.stringify(r,null,2));
});

test('剧情响应先返回；下一发送等待后台并重验 revision；关闭宿主等待已派发任务',async t=>{
  const entered=deferred(),release=deferred();t.after(()=>release.resolve());
  const f=await fixture(t,{providerFault:async(phase,r)=>{if(phase==='afterAccepted'&&r.kind==='summary'){entered.resolve();await release.promise;}}});
  await f.sendIdle();const plot=await f.send('two','第二轮');await entered.promise;
  assert.equal(plot.operation.state,'completed');const v=await f.read();assert.equal(v.session.turns.length,2);assert.equal(v.busy,true);assert.equal(v.pending,true);assert.equal(v.session.summary.snapshot.revision,0);
  let finished=false;const queued=f.host.send(await f.input('three','下一轮')).then(()=>{finished=true;},error=>{finished=true;assert.equal(error.code,'OFFLINE_REVISION_CONFLICT');});
  await new Promise(r=>setImmediate(r));assert.equal(finished,false);
  release.resolve();await queued;await f.idle();assert.equal(f.provider.dispatchCount,3);assert.equal((await f.read()).session.summary.jobs[0].state,'applied');
});

test('原剧情操作重复与重启不重启后台任务',async t=>{
  const f=await fixture(t);await f.sendIdle();const input=await f.input('two','继续'),sent=await f.host.send(input);await f.idle();
  const before=(await f.read()).session;assert.equal((await f.host.send(input)).replayed,true);assert.equal(f.provider.dispatchCount,3);
  await f.reopen();assert.equal((await f.host.send(input)).replayed,true);assert.equal(f.provider.dispatchCount,0);assert.deepEqual((await f.read()).session,before);
  await reject(()=>f.host.send({...input,input:'被改写'}),'OFFLINE_OPERATION_COLLISION');assert.equal(sent.operation.state,'completed');
});

test('M2 回执丢失：暂停下一发送，重启仅按原请求查回，不新派发',async t=>{
  const f=await fixture(t,{providerFault:async(phase,r)=>{if(phase==='afterReceipt'&&r.kind==='summary')throw Error('lost receipt');}});
  await f.sendIdle();await f.sendIdle('two','继续');const before=(await f.read()).session,job=before.summary.jobs[0];assert.equal(job.state,'unknown');
  await reject(()=>f.send('three','下一轮'),'SUMMARY_RESULT_PENDING');await reject(()=>f.select('summary','other'),'SUMMARY_RESULT_PENDING');
  await f.reopen();assert.equal(f.provider.dispatchCount,0);const recovered=await f.recover();assert.equal(recovered.job.state,'applied');assert.deepEqual(recovered.job.request,job.request);
  assert.equal((await f.recover()).replayed,true);assert.equal(f.provider.dispatchCount,0);assert.deepEqual((await f.read()).session.turns,before.turns);
});

for(const action of ['disable','uninstall'])test('未知任务'+action+'后仍需收束；迟到原结果不应用',async t=>{
  const f=await fixture(t,{providerFault:async(phase,r)=>{if(phase==='afterReceipt'&&r.kind==='summary')throw Error('lost');}});
  await f.sendIdle();await f.sendIdle('two','继续');await f.change(action);await reject(()=>f.send('three','下一轮'),'SUMMARY_RESULT_PENDING');
  await f.reopen();const r=await f.recover();assert.equal(r.job.state,'revoked');assert.ok(r.job.receipt);assert.equal((await f.read()).session.summary.snapshot.revision,0);assert.equal(f.provider.dispatchCount,0);
  assert.equal((await f.read()).effectivePolicy.mode,'full-history');await f.sendIdle('three','下一轮');assert.equal(f.provider.dispatchCount,1);
});

test('超长结果只压缩一次，两个独立 ID 与原文回执完整落盘',async t=>{
  const f=await fixture(t,{summaryText:'长'.repeat(10001)});await f.sendIdle();await f.sendIdle('two','继续');const v=await f.read(),j=v.session.summary.jobs[0];
  assert.equal(j.state,'applied');assert.equal(f.provider.dispatchCount,4);assert.equal(j.receipt.result.text.length,10001);
  assert.equal(j.compressionRequest.operationId,compressionJobId(j.operationId));assert.notEqual(j.request.id,j.compressionRequest.id);
  assert.equal(v.session.summary.snapshot.versions[0].effectiveText,'压缩摘要：承诺尚未兑现。');
});

test('M1 与 M2 组合、各自停用以及卸载重装：历史原文和摘要版本保留',async t=>{
  const f=await fixture(t);await f.enable(H);await f.configureHistory();await f.sendIdle();await f.sendIdle('two','二');
  const v=await f.read();await f.change('disable',H);assert.equal((await f.read()).effectivePolicy.mode,'rolling-summary');
  await f.change('disable',M2);assert.equal((await f.read()).effectivePolicy.mode,'full-history');
  await f.change('enable',H);const r=(await f.sendIdle('three','三')).operation.request;assert.equal(r.policy.mode,'history-window');assert.equal(r.messages.length,4);
  assert.deepEqual((await f.read()).session.summary.snapshot,v.session.summary.snapshot);
  await f.change('uninstall',M2);await f.change('install',M2);assert.equal((await f.read()).effectivePolicy.authorization.summary.enabled,false);
  await f.change('enable',M2);assert.equal((await f.read()).effectivePolicy.reason,'SUMMARY_UPDATE_REQUIRED');
  const updated=await f.update();assert.equal(updated.job.state,'applied');assert.deepEqual(updated.job.request.source.newTurns,[2]);
});

test('启用不调用；欠处理原文需明确更新；更新幂等且不替换独立模型',async t=>{
  const f=await fixture(t,{enabled:false});await f.sendIdle();await f.sendIdle('two','二');await f.sendIdle('three','三');
  await f.enable();assert.equal(f.provider.dispatchCount,3);await reject(()=>f.send('four','四'),'SUMMARY_UPDATE_REQUIRED');
  const input=await f.summaryInput(),result=await f.host.requestSummary(input);assert.equal(result.job.state,'applied');assert.deepEqual(result.job.request.source.newTurns,[1,2]);
  assert.equal((await f.host.requestSummary(input)).replayed,true);assert.equal(f.provider.dispatchCount,4);
  await reject(()=>f.update('retry'),'SUMMARY_ALREADY_CURRENT');assert.equal(f.provider.dispatchCount,4);
  await reject(()=>f.send('manual','重用ID'),'OFFLINE_OPERATION_COLLISION');
});

for(const [name,out,diagnostic] of [
  ['空白',reply('   '),'SUMMARY_OUTPUT_EMPTY'],['截断',reply('部分摘要','length'),'SUMMARY_OUTPUT_TRUNCATED'],
  ['供应商失败',reply('fixture failure','fixture_failure','failed'),'SUMMARY_PROVIDER_FAILED'],
])test(name+'保留原回执与旧摘要；明确重试才能再派发',async t=>{
  const replies=makeReplies();replies[summaryJobId(sessionId,'three')]=out;
  const f=await fixture(t,{replies});await f.sendIdle();await f.sendIdle('two','二');const before=clone((await f.read()).session.summary.snapshot);
  await f.sendIdle('three','三');const v=await f.read(),j=v.session.summary.jobs.at(-1);assert.equal(j.state,'rejected');assert.equal(j.diagnostic,diagnostic);assert.deepEqual(j.receipt.result,out);
  assert.deepEqual(v.session.summary.snapshot,before);assert.equal(v.session.turns.length,3);await reject(()=>f.send('four','四'),'SUMMARY_UPDATE_REQUIRED');
  const n=f.provider.dispatchCount;await f.recover(j.operationId);assert.equal(f.provider.dispatchCount,n);
  const retry=await f.update('retry');assert.equal(retry.job.state,'applied');assert.equal(f.provider.dispatchCount,n+1);assert.equal((await f.read()).session.summary.jobs.length,3);
});

test('压缩仍超限：没有第三次调用，保留两份原文并暂停',async t=>{
  const replies=makeReplies('长'.repeat(10001));replies[compressionJobId(summaryJobId(sessionId,'two'))]=reply('压'.repeat(3001));
  const f=await fixture(t,{replies});await f.sendIdle();await f.sendIdle('two','二');const v=await f.read(),j=v.session.summary.jobs[0];
  assert.equal(j.state,'rejected');assert.equal(j.diagnostic,'SUMMARY_COMPRESSION_TOO_LONG');assert.equal(f.provider.dispatchCount,4);
  assert.equal(j.receipt.result.text.length,10001);assert.equal(j.compressionReceipt.result.text.length,3001);assert.equal(v.session.summary.snapshot.revision,0);
  await reject(()=>f.send('three','三'),'SUMMARY_UPDATE_REQUIRED');
});

test('原摘要回执恢复需要压缩时只冻结，明确继续才派发原压缩任务',async t=>{
  const f=await fixture(t,{summaryText:'长'.repeat(10001),providerFault:async(phase,r)=>{if(phase==='afterReceipt'&&r.kind==='summary')throw Error('lost');}});
  await f.sendIdle();await f.sendIdle('two','二');await f.reopen();const r=await f.recover();assert.equal(r.job.state,'awaiting_compression');assert.equal(f.provider.dispatchCount,0);
  const frozen=clone(r.job.compressionRequest);await f.recover();assert.equal(f.provider.dispatchCount,0);await reject(()=>f.update('retry'),'SUMMARY_RESULT_PENDING');
  const intent=await f.recovery();const done=await f.host.continueSummary(intent);assert.equal(done.job.state,'applied');assert.deepEqual(done.job.compressionRequest,frozen);assert.equal(f.provider.dispatchCount,1);
  assert.equal((await f.host.continueSummary(intent)).replayed,true);assert.equal(f.provider.dispatchCount,1);
});

test('压缩回执未知：重启只读取原压缩回执',async t=>{
  const f=await fixture(t,{summaryText:'长'.repeat(10001),providerFault:async(phase,r)=>{if(phase==='afterReceipt'&&r.kind==='compression')throw Error('lost');}});
  await f.sendIdle();await f.sendIdle('two','二');const j=(await f.read()).session.summary.jobs[0];assert.equal(j.state,'compression_unknown');
  await f.reopen();const recovered=await f.recover();assert.equal(recovered.job.state,'applied');assert.equal(f.provider.dispatchCount,0);assert.deepEqual(recovered.job.compressionRequest,j.compressionRequest);
});

test('已接受但没有结果：停用及重启不能解除未知阻断或生成新 ID',async t=>{
  const f=await fixture(t,{providerFault:async(phase,r)=>{if(phase==='afterAccepted'&&r.kind==='summary')throw Object.assign(Error('unknown'),{code:'SUMMARY_AUTHORIZATION_CHANGED'});}});
  await f.sendIdle();await f.sendIdle('two','二');const j=(await f.read()).session.summary.jobs[0];assert.equal(j.state,'unknown');await f.change('disable');await f.reopen();
  for(let i=0;i<2;i++){const r=await f.recover();assert.equal(r.job.state,'unknown');assert.deepEqual(r.job.request,j.request);}
  await reject(()=>f.update('retry'),'SUMMARY_RESULT_PENDING');await reject(()=>f.send('three','三'),'SUMMARY_RESULT_PENDING');
  assert.equal(f.provider.dispatchCount,0);assert.equal((await f.read()).session.turns.length,2);
});

for(const phase of ['summaryBeforeDispatch','summaryAfterProvider'])test('在 '+phase+' 撤权，原任务不再生效',async t=>{
  let f,once=true;f=await fixture(t,{fault:async p=>{if(p===phase&&once){once=false;await f.change('disable');}}});
  await f.sendIdle();await f.sendIdle('two','二');const s=(await f.read()).session,j=s.summary.jobs[0];assert.equal(j.state,'revoked');
  assert.equal(j.receipt!==null,phase==='summaryAfterProvider');assert.equal(s.summary.snapshot.revision,0);assert.equal(s.turns.length,2);
  assert.equal(f.provider.dispatchCount,phase==='summaryBeforeDispatch'?2:3);
});

test('摘要派发前资格改变或 fixture 缺失：明确未派发，剧情保存',async t=>{
  let f;f=await fixture(t,{fault:async p=>{if(p==='summaryBeforeDispatch')f.modelControl.replace({catalogId:'offline-summary-fixture',models:[]});}});
  await f.sendIdle();await f.sendIdle('two','二');const j=(await f.read()).session.summary.jobs[0];assert.equal(j.state,'not_dispatched');assert.equal(j.diagnostic,'MODEL_SELECTION_CHANGED');assert.equal(f.provider.dispatchCount,2);
  const replies=makeReplies();delete replies[summaryJobId(sessionId,'two')];const g=await fixture(t,{replies});await g.sendIdle();await g.sendIdle('two','二');
  const missing=(await g.read()).session.summary.jobs[0];assert.equal(missing.state,'not_dispatched');assert.equal(missing.diagnostic,'OFFLINE_FIXTURE_MISSING');assert.equal(g.provider.dispatchCount,2);
});

for(const phase of ['beforeCommit','afterCommit'])test('摘要应用 '+phase+' 写盘故障：原回执恢复恰好一版',async t=>{
  let armed=true;const f=await fixture(t,{storageFault:async(p,{data})=>{if(p===phase&&data.summary?.snapshot.revision===1&&armed){armed=false;throw Error('storage');}}});
  await f.sendIdle();await f.sendIdle('two','二');const prior=await f.read();assert.equal(prior.session.turns.length,2);
  assert.equal(prior.session.summary.snapshot.revision,phase==='afterCommit'?1:0);
  await f.reopen();const r=await f.recover();assert.equal(r.job.state,'applied');assert.equal((await f.read()).session.summary.snapshot.revision,1);assert.equal(f.provider.dispatchCount,0);
  await f.recover();assert.equal((await f.read()).session.summary.snapshot.versions.length,1);
});

for(const phase of ['beforeCommit','afterCommit'])test('剧情与后台任务 '+phase+' 原子保存故障；恢复剧情不启动摘要',async t=>{
  let armed=true;const f=await fixture(t,{storageFault:async(p,{data})=>{if(p===phase&&data.turns?.length===2&&data.summary?.jobs[0]?.state==='prepared'&&armed){armed=false;throw Error('storage');}}});
  await f.sendIdle();const input=await f.input('two','二');await reject(()=>f.host.send(input),phase==='afterCommit'?'OFFLINE_WRITE_UNKNOWN':'OFFLINE_WRITE_FAILED');
  await f.reopen();const v=await f.read();const r=await f.host.recover({sessionId:f.id,operationId:'two',expectedRevision:v.session.revision});assert.equal(r.operation.state,'completed');
  const s=(await f.read()).session;assert.equal(s.turns.length,2);assert.equal(s.summary.jobs[0].state,'prepared');assert.equal(f.provider.dispatchCount,0);
  const noDispatch=await f.recover();assert.equal(noDispatch.job.state,'not_dispatched');assert.equal(f.provider.dispatchCount,0);
});

test('M2 输入容量不足暂停，不截断、补压缩或丢已完成剧情',async t=>{
  const catalog=makeCatalog();catalog.models.find(m=>m.selectionId==='summary').contextLength=8000;
  const replies=makeReplies();replies.one=reply('原'.repeat(6000));const f=await fixture(t,{catalog,replies});await f.sendIdle();await f.sendIdle('two','二');
  const s=(await f.read()).session,j=s.summary.jobs[0];assert.equal(j.state,'blocked');assert.equal(j.diagnostic,'MODEL_CONTEXT_CAPACITY');assert.equal(j.request,null);assert.equal(s.turns[0].assistant.length,6000);assert.equal(s.turns.length,2);assert.equal(f.provider.dispatchCount,2);
  await reject(()=>f.send('three','三'),'SUMMARY_UPDATE_REQUIRED');
});

test('任务存储空间预留不足产生可核对的 blocked 记录',async t=>{
  const f=await fixture(t,{fault:async p=>{if(p==='afterStorySaved')throw Error('stop before background');}});
  await f.sendIdle();await f.sendIdle('two','二');const s=clone((await f.read()).session),original=s.summary.jobs[0];
  s.revision=original.intent.expectedRevision;s.summary.jobs=[];s.draft='x'.repeat(32*1024*1024-SUMMARY_RECEIPT_RESERVE);
  const job=freezeSummaryJob(s,original.intent,original.authorization,original.trigger);assert.equal(job.state,'blocked');assert.equal(job.diagnostic,'SUMMARY_RECORD_CAPACITY');assert.equal(job.request,null);
});

test('M3 没有接执行时明确阻断；启停/新会话不继承授权或产生次数',async t=>{
  const f=await fixture(t);await f.enable(M3);await reject(()=>f.send(),'AUTHORING_MEMORY_EXECUTION_UNAVAILABLE');assert.equal(f.provider.dispatchCount,0);
  await f.change('disable',M3);const fresh=(await f.host.create({...f.creation,operationId:'new-independent'})).session;
  const view=await f.host.read(fresh.id);assert.equal(view.effectivePolicy.authorization.summary.enabled,false);assert.equal(fresh.models.current.summary,null);assert.equal(fresh.summary.jobs.length,0);
});

test('活跃目录锁、未知模型配置和陈旧标签不能覆盖当前记录',async t=>{
  const f=await fixture(t);await reject(()=>SummarySessionHost.open({directory:join(f.directory,'sessions'),resolveRuntime:f.resolveRuntime,provider:f.provider,modelControl:f.modelControl,historyControl:f.control,recoverDeadOwner:true}),'OFFLINE_STORE_BUSY');
  const stale=await f.input();await f.select('story','other');await reject(()=>f.host.send(stale),'OFFLINE_REVISION_CONFLICT');
  const fresh=await f.input();await f.change('disable');await reject(()=>f.host.send(fresh),'SUMMARY_POLICY_CONFLICT');assert.equal(f.provider.dispatchCount,0);
});

test('任务与模型/剧情/压缩操作 ID 共享隔离；校验拒绝改写来源、原回执或已应用版本',async t=>{
  const f=await fixture(t);await f.sendIdle();await f.sendIdle('two','二');const s=(await f.read()).session,j=s.summary.jobs[0];
  await reject(()=>f.send(compressionJobId(j.operationId),'冲突'),'OFFLINE_OPERATION_COLLISION');
  const v=await f.read();await reject(()=>f.host.configureModel({sessionId:f.id,operationId:j.operationId,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,purpose:'summary',choice:null}),'OFFLINE_OPERATION_COLLISION');
  for(const modify of [x=>x.summary.jobs[0].completedTurns=1,x=>x.summary.jobs[0].receipt.result.text='篡改',x=>x.summary.jobs[0].appliedVersionId=sha256('fake'),x=>x.summary.jobs=[]]){
    const bad=clone(s);modify(bad);assert.throws(()=>validateSummaryJobs(bad));
  }
  const missing=clone(s);missing.summary.jobs=[];missing.summary.snapshot={...missing.summary.snapshot,revision:0,versions:[],activeVersionId:null};const {sha256:oldHash,...emptyBody}=missing.summary.snapshot;missing.summary.snapshot.sha256=digest(emptyBody);assert.throws(()=>validateSummaryJobs(missing));
  const file=join(f.directory,'sessions','records',f.id+'.json'),saved=JSON.parse(await readFile(file,'utf8'));saved.data.summary.jobs[0].request.messages[1].content='篡改';saved.sha256=digest(saved.data);
  await writeFile(file,canonical(saved)+'\n');await assert.rejects(()=>f.read());assert.equal(f.provider.dispatchCount,3);
});

async function child(code) {
  const dir=await mkdtemp(join(root,'child-')),script=join(dir,'probe.mjs');await writeFile(script,code);
  return new Promise((resolve,reject)=>{
    const p=spawn(process.execPath,[script],{windowsHide:true,stdio:['ignore','pipe','pipe']});let output='';
    const timeout=setTimeout(()=>{p.kill();reject(Error('offline child timeout'));},45000);
    p.stdout.on('data',b=>output+=b);p.stderr.on('data',b=>output+=b);p.on('error',e=>{clearTimeout(timeout);reject(e);});
    p.on('exit',exitCode=>{clearTimeout(timeout);resolve({exitCode,output});});
  });
}
for(const [point,expected,overflow=false] of [
  ['afterStorySaved','not_dispatched'],['summaryBeforeDispatch','unknown'],['summaryAfterProvider','applied'],
  ['summaryAfterSaved','applied'],['summaryAfterSaved','awaiting_compression',true],['compressionAfterProvider','applied',true],
])test('实际进程退出 '+point+(overflow?' / 压缩':'')+'：新进程只恢复原记录',async t=>{
  const f=await fixture(t,{summaryText:overflow?'长'.repeat(10001):undefined});await f.sendIdle();const intent=await f.input('two','二');await f.close();
  const module=name=>JSON.stringify(new URL('./'+name+'.mjs',import.meta.url).href);
  const result=await child(`
    import {AuthoringStore} from ${module('store')};import {openPublishedRuntime} from ${module('runtime-binding')};
    import {OfflineFixtureProvider} from ${module('offline-session')};import {SummarySessionHost} from ${module('summary-session')};
    import {OfflineModelControl} from ${module('model-control')};import {createAuthoringHistoryControl} from ${module('history-control')};
    const dir=${JSON.stringify(f.directory)},store=await AuthoringStore.open(dir+'/works'),resolveRuntime=r=>openPublishedRuntime(store,r);
    let host;const historyControl=await createAuthoringHistoryControl({directory:dir+'/plugins',resolveRuntime,lookupSession:id=>host.lookupSession(id)});
    const provider=await OfflineFixtureProvider.open(dir+'/provider',{replies:${JSON.stringify(makeReplies(overflow?'长'.repeat(10001):undefined))}});
    host=await SummarySessionHost.open({directory:dir+'/sessions',resolveRuntime,provider,historyControl,modelControl:new OfflineModelControl(${JSON.stringify(makeCatalog())}),
      fault:async (phase,value)=>{if(${JSON.stringify(point)}==='compressionAfterProvider'?phase==='summaryAfterProvider'&&value.kind==='compression':phase===${JSON.stringify(point)})process.exit(73);}});
    await host.send(${JSON.stringify(intent)});await host.waitForIdle(${JSON.stringify(f.id)});
    process.exit(74);
  `);
  assert.equal(result.exitCode,73,result.output);
  await reject(()=>SummarySessionHost.open({directory:join(f.directory,'sessions'),resolveRuntime:f.resolveRuntime,provider:f.provider,modelControl:f.modelControl,historyControl:f.control}),'OFFLINE_OWNER_RECOVERY_REQUIRED');
  await f.reopen(true);const prior=(await f.read()).session,request=clone(prior.summary.jobs[0].request);assert.equal(prior.turns.length,2);assert.equal(f.provider.dispatchCount,0);
  const recovered=await f.recover();assert.equal(recovered.job.state,expected);assert.deepEqual(recovered.job.request,request);assert.equal(f.provider.dispatchCount,0);
  const again=await f.recover();assert.equal(again.job.state,expected);assert.equal(f.provider.dispatchCount,0);
  assert.ok((await readdir(join(f.directory,'sessions'))).some(n=>n.startsWith('owner-dead-')));
  await writeFile(join(f.directory,'process-recovery-evidence.json'),JSON.stringify({point,overflow,exitCode:result.exitCode,requestId:request.id,state:expected,newDispatches:f.provider.dispatchCount},null,2));
});

test('关闭会等待已派发后台任务；重新读取无需剧情客户端继续在线',async t=>{
  const entered=deferred(),release=deferred();t.after(()=>release.resolve());
  const f=await fixture(t,{providerFault:async(p,r)=>{if(p==='afterAccepted'&&r.kind==='summary'){entered.resolve();await release.promise;}}});
  await f.sendIdle();await f.send('two','二');await entered.promise;
  let closed=false;const closing=f.host.close().then(()=>{closed=true;});await new Promise(r=>setImmediate(r));assert.equal(closed,false);
  release.resolve();await closing;assert.equal(closed,true);assert.equal(f.provider.dispatchCount,3);
  await f.reopen();const s=(await f.read()).session;assert.equal(s.summary.jobs[0].state,'applied');assert.equal(s.turns.length,2);assert.equal(f.provider.dispatchCount,0);
});

test('写盘前 Prepared 未发送：明确恢复为未派发，新任务必须使用独立操作',async t=>{
  let armed=true;const f=await fixture(t,{enabled:false,fault:async p=>{if(p==='summaryAfterPrepared'&&armed){armed=false;throw Error('stop');}}});
  await f.sendIdle();await f.sendIdle('two','二');await f.enable();const input=await f.summaryInput();await assert.rejects(()=>f.host.requestSummary(input));
  assert.equal((await f.read()).session.summary.jobs[0].state,'prepared');assert.equal(f.provider.dispatchCount,2);
  const r=await f.recover('manual');assert.equal(r.job.state,'not_dispatched');assert.equal(f.provider.dispatchCount,2);
  assert.equal((await f.host.requestSummary(input)).replayed,true);assert.equal(f.provider.dispatchCount,2);
  assert.equal((await f.update('retry')).job.state,'applied');assert.equal(f.provider.dispatchCount,3);
});
