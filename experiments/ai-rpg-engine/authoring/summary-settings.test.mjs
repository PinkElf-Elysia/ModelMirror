import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {startAuthoringServer} from './server.mjs';
import {OfflineFixtureProvider} from './offline-session.mjs';
import {ScenarioFixtureProvider} from './fixture-scenario.mjs';
import {OfflineModelControl} from './model-control.mjs';
import {summaryJobId} from './summary-jobs.mjs';
import {blankDocument,sha256,canonical} from './contracts.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6j-tests/',import.meta.url));await mkdir(root,{recursive:true});
const base='/rpg-app/api/authoring',sp='/summary-settings',mp='/model-settings';
const id='session-'+sha256('summary-session:session');
const catalog={catalogId:'m2-http',models:['story','summary'].map(k=>({selectionId:k,selectionRevision:sha256(k),modelId:'fixture/'+k,name:'离线 '+k,available:true,contextLength:128000,maxOutputTokens:16000,parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}}))};
const result=text=>({status:'completed',finishReason:'stop',text});
async function fixture(t,options={}){
 const directory=await mkdtemp(join(root,'case-')),control=new OfflineModelControl(catalog);let server,provider;
 const replies={one:result('首轮古老灯塔'),two:result('次轮守约'),three:result('第三轮剧情'),manual:result('补齐摘要'),[summaryJobId(id,'two')]:result('灯塔承诺尚未完成。'),[summaryJobId(id,'three')]:result('累计摘要。')};
 async function open(faults=true){provider=options.scenario?await ScenarioFixtureProvider.open(join(directory,'provider'),{replies:{story:result('固定剧情样例'),summary:result('固定摘要样例'),compression:result('固定压缩样例')},fault:faults?options.providerFault:undefined}):await OfflineFixtureProvider.open(join(directory,'provider'),{replies,fault:faults?options.providerFault:undefined});
  server=await startAuthoringServer({directory:join(directory,'works'),modelSettings:{directory:join(directory,'sessions'),provider,control},historySettings:{directory:join(directory,'history')},summarySettings:{directory:join(directory,'summary'),...(faults&&options.fault?{fault:options.fault}:{})}});}
 await open();t.after(async()=>{await server.close();await provider.close();});
 const req=async(path,body,headers={})=>{const r=await fetch(server.origin+base+path,body===undefined?{headers}:{method:'POST',headers:{Origin:server.origin,'Content-Type':'application/json','X-Authoring-Request':'1',...headers},body:JSON.stringify(body)});return {status:r.status,value:await r.json()};};
 const ok=async(path,body)=>{const r=await req(path,body);assert.equal(r.status,200,JSON.stringify(r.value));return r.value;};
 const doc=blankDocument();doc.metadata.name='通用 M2 接口';doc.metadata.audience='female';doc.sources.system='ORIGINAL';doc.sources.prefix='PRE';doc.sources.suffix='POST';
 const w=await ok('/works',{operationId:'work',expectedRevision:null,document:doc}),v=await ok('/works/'+w.workId+'/publish',{operationId:'publish',expectedRevision:0});
 const creation={operationId:'session',expectedRevision:null,reference:{workId:w.workId,versionId:v.result.versionId},openingText:'原始开局',presetId:null};await ok(mp+'/sessions',creation);
 const f={directory,req,ok,creation,get server(){return server;},get provider(){return provider;},view:()=>ok('/sessions/'+id),settings:()=>ok(sp+'/sessions/'+id),
  async intent(action,operationId=randomUUID()){const s=await f.settings();return {action,sessionId:id,operationId,expectedRevision:s.sessionRevision,expectedSummaryRevision:s.summaryRevision,expectedModelRevision:s.modelRevision,expectedContextPolicyRevision:s.effectivePolicy.revision,expectedRegistryRevision:s.registryRevision,...(action==='enable'?{permissions:s.plugin.permissions}:{})};},
  async change(action,operationId){return ok(sp+'/change',await f.intent(action,operationId));},
  async select(purpose){const s=(await f.view()).session;return ok(mp+'/configure',{sessionId:id,operationId:randomUUID(),expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,choice:{catalogRevision:control.catalog().revision,selectionId:purpose,selectionRevision:sha256(purpose),parameters:{maxOutputTokens:4096}}});},
  async send(operationId,input=operationId==='one'?'原始开局':'后续输入'){const v=await f.view();return ok('/sessions/send',{sessionId:id,operationId,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,input});},
  idle:()=>server.sessionHost.waitForIdle(id),
  async recover(operationId){const v=await f.view();return ok('/sessions/summary/recover',{sessionId:id,operationId,expectedRevision:v.session.revision,expectedSummaryRevision:v.session.summary.snapshot.revision});},
  async reopen(){await server.close();await provider.close();await open(false);},
 };
 return f;
}
const rejects=async(f,path,body,code)=>{const r=await f.req(path,body);assert.equal(r.value.error?.code,code,JSON.stringify(r));return r;};
test('默认关闭新接口，不开放宿主模块',async t=>{
 const d=await mkdtemp(join(root,'old-')),h=await startAuthoringServer({directory:d});t.after(()=>h.close());
 assert.deepEqual(await(await fetch(h.origin+base+sp)).json(),{enabled:false,realProvider:false});
 for(const path of [base+'/sessions/'+id,'/summary-settings.mjs','/summary-session.mjs'])assert.equal((await fetch(h.origin+path)).status,404);
});
test('安装/授权、独立模型、卸载重装、刷新全部零派发',async t=>{
 const f=await fixture(t);assert.equal((await f.settings()).plugin.installed,false);await f.change('install');
 await rejects(f,sp+'/change',await f.intent('enable'),'SUMMARY_MODEL_REQUIRED');await f.select('summary');await f.change('enable');
 assert.equal((await f.view()).effectivePolicy.mode,'rolling-summary');assert.equal(f.provider.dispatchCount,0);
 await f.change('uninstall');await f.change('install');assert.equal((await f.view()).effectivePolicy.mode,'full-history');
 const s=canonical(await f.settings());await f.reopen();assert.equal(canonical(await f.settings()),s);assert.equal(f.provider.dispatchCount,0);
 const recovered=await f.ok(mp+'/reconcile',{kind:'create',sessionId:null,operationId:'session'});assert.equal(recovered.found,true);assert.equal(recovered.view.session.id,id);
});
test('HTTP 发送实际持久化 M2；下一剧情 system 使用摘要与一回合',async t=>{
 const f=await fixture(t);await f.select('story');await f.select('summary');await f.change('install');await f.change('enable');
 await f.send('one');await f.idle();await f.send('two');await f.idle();const v=await f.view();
 assert.equal(v.session.turns.length,2);assert.equal(v.session.summary.jobs[0].state,'applied');assert.equal(f.provider.dispatchCount,3);
 assert.equal((await f.settings()).coveredThrough,1);
 const r=await f.send('three');await f.idle();assert.equal(r.operation.request.messages.length,4);assert.match(r.operation.request.messages[0].content,/ORIGINAL.*灯塔承诺/s);
 assert.equal(r.operation.request.messages.filter(m=>m.role==='system').length,1);assert.equal(r.operation.request.messages.at(-1).content,'PRE\n\n后续输入\n\nPOST');
 const job=(await f.view()).session.summary.jobs[0],source=JSON.parse(job.request.messages[1].content);assert.equal(source.turns[0].content,'原始开局');
});
test('跨源、信任字段注入、错误权限和旧 revision 拒绝，零派发',async t=>{
 const f=await fixture(t),input=await f.intent('install');
 assert.equal((await f.req(sp+'/change',input,{Origin:'https://foreign.invalid'})).status,403);
 for(const x of [{system:'injected'},{providerUrl:'https://foreign.invalid'},{history:[]}])await rejects(f,sp+'/change',{...input,...x},'AUTHORING_UNKNOWN_FIELD');
 await f.select('summary');await rejects(f,sp+'/change',input,'OFFLINE_REVISION_CONFLICT');await f.change('install');
 const enable=await f.intent('enable');await rejects(f,sp+'/change',{...enable,permissions:[]},'PLUGIN_PERMISSION_MISMATCH');
 await rejects(f,sp+'/change',{...enable,expectedSummaryRevision:99},'SUMMARY_REVISION_CONFLICT');
 await rejects(f,sp+'/change',{...enable,expectedContextPolicyRevision:'a'.repeat(64)},'SUMMARY_POLICY_CONFLICT');assert.equal(f.provider.dispatchCount,0);
});
test('同 ID 重复读取原回执，变更意图与跨用途占用被拒绝',async t=>{
 const f=await fixture(t),i=await f.intent('install','install');const a=await f.ok(sp+'/change',i),b=await f.ok(sp+'/change',i);assert.equal(b.replayed,true);assert.deepEqual(a.receipt,b.receipt);
 await rejects(f,sp+'/change',{...i,expectedRevision:9},'SUMMARY_SETTINGS_OPERATION_COLLISION');
 await f.select('story');const v=await f.view();await rejects(f,'/sessions/send',{sessionId:id,operationId:'install',expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,input:'原始开局'},'SUMMARY_SETTINGS_OPERATION_COLLISION');
 await rejects(f,sp+'/change',await f.intent('uninstall','session'),'OFFLINE_OPERATION_COLLISION');
});
for(const action of ['install','enable','disable','uninstall'])test(action+' 丢失配置回执后重启按原操作核对，不自动重放',async t=>{
 let armed=false;const f=await fixture(t,{fault:phase=>{if(armed&&phase==='afterChange'){armed=false;throw Error('lost');}}});await f.select('summary');
 if(action!=='install')await f.change('install');if(['disable','uninstall'].includes(action))await f.change('enable');
 const i=await f.intent(action);armed=true;await rejects(f,sp+'/change',i,'SUMMARY_SETTINGS_RESULT_UNKNOWN');
 await rejects(f,sp+'/change',i,'SUMMARY_SETTINGS_RESULT_UNKNOWN');await f.reopen();assert.deepEqual((await f.settings()).unconfirmed,i);
 const recovered=await f.ok(sp+'/reconcile',i);assert.equal(recovered.state,'applied');assert.equal(recovered.settings.unconfirmed,null);assert.equal(f.provider.dispatchCount,0);
});
test('提交前故障核对不执行，明确重试沿用原 ID；未收束阻止其它配置',async t=>{
 let armed=true;const f=await fixture(t,{fault:phase=>{if(armed&&phase==='beforeChange'){armed=false;throw Error('before');}}});
 const i=await f.intent('install');await rejects(f,sp+'/change',i,'SUMMARY_SETTINGS_RESULT_UNKNOWN');
 await rejects(f,mp+'/configure',{},'SUMMARY_SETTINGS_RESULT_PENDING');const r=await f.ok(sp+'/reconcile',i);assert.equal(r.state,'not_applied');assert.equal(r.settings.plugin.installed,false);
 assert.equal((await f.ok(sp+'/change',i)).state,'applied');assert.equal(f.provider.dispatchCount,0);
});
test('M2 未知结果在 HTTP 重启恢复，停用不丢原任务',async t=>{
 const f=await fixture(t,{providerFault:(phase,r)=>{if(phase==='afterReceipt'&&r.kind==='summary')throw Error('unknown');}});
 await f.select('story');await f.select('summary');await f.change('install');await f.change('enable');await f.send('one');await f.idle();await f.send('two');await f.idle();
 const old=await f.view(),job=old.session.summary.jobs[0];assert.equal(job.state,'unknown');await f.change('disable');assert.equal((await f.view()).pending,true);
 await f.reopen();assert.equal(f.provider.dispatchCount,0);const r=await f.recover(job.operationId);assert.equal(r.job.state,'revoked');assert.deepEqual((await f.view()).session.turns,old.session.turns);assert.equal(f.provider.dispatchCount,0);
});
test('M1 旧配置 receipt 与 M2 统一发送策略分离，停用 M2 回到 M1',async t=>{
 const f=await fixture(t);await f.select('summary');await f.change('install');await f.change('enable');
 async function history(action){const v=await f.ok('/history-settings/sessions/'+id),i={action,sessionId:id,operationId:randomUUID(),expectedRevision:v.sessionRevision,expectedHistoryRevision:v.history.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,expectedRegistryRevision:v.registryRevision,...(action==='enable'?{permissions:v.plugin.permissions}:{}),...(action==='configure'?{config:{turns:2,includeInitialCharacter:true}}:{})};return f.ok('/history-settings/change',i);}
 await history('install');await history('enable');await history('configure');assert.equal((await f.view()).effectivePolicy.mode,'rolling-summary');await f.change('disable');
 const v=await f.view();assert.equal(v.effectivePolicy.mode,'history-window');assert.equal(v.session.history.config.turns,2);assert.equal(f.provider.dispatchCount,0);
});

test('交互离线样例支持浏览器新操作 ID；剧情后自动摘要与重启回放均落盘',async t=>{
 const f=await fixture(t,{scenario:true});await f.select('story');await f.select('summary');await f.change('install');await f.change('enable');
 await f.send(randomUUID(),'原始开局');await f.idle();await f.send(randomUUID());await f.idle();
 const s=(await f.view()).session;assert.equal(s.turns.length,2);assert.equal(s.summary.snapshot.versions.at(-1).effectiveText,'固定摘要样例');assert.equal(f.provider.dispatchCount,3);
 await f.reopen();assert.deepEqual((await f.view()).session,s);assert.equal(f.provider.dispatchCount,0);
});
