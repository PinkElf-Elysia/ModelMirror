import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,readdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {SummarySessionStore} from './summary-session-store.mjs';
import {HistorySessionStore} from './history-session-store.mjs';
import {summaryRuntime} from './summary-runtime.mjs';
import {requireSummaryState,modelSummaryVersion,withSummaryVersion,summaryUpdateSource,sealSummary,SUMMARY_INSTRUCTION_HASH,SUMMARY_WRAPPER} from './summary-state.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {loadReviewedPlugins,ROLLING_SUMMARY_ID as R,HISTORY_WINDOW_ID as H,sha,canonical} from '../plugins/catalog.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/rolling-summary-b1/store/',import.meta.url));await mkdir(root,{recursive:true});
const character={characterText:'姓名：虚构测试人\n原始开局资料',world:'表世界',params:{max_tokens:16384},mode:'offline'};
const item={selectionId:'summary-gemini',selectionRevision:'catalog-v1',model:'google/gemini-3.8-flash',available:true};
async function fixture(t){
 const directory=await mkdtemp(join(root,'state-')),control={status:async()=>({enabled:false}),catalog:async()=>[item]};
 const store=new SummarySessionStore(join(directory,'sessions'),()=>assert.fail('B1 must never dispatch'),null,{control});await store.init();
 const plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:id=>store.pluginSession(id),loadCatalog:()=>loadReviewedPlugins({rollingSummary:true})});t.after(()=>plugins.close());
 const f={store,plugins,control,directory,async create(){return store.create(character);},async change(action,s,pluginId=R){const c=await plugins.catalog(),p=c.plugins.find(x=>x.id===pluginId);return plugins.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},async grant(s,p=R){if(!(await plugins.catalog()).plugins.find(x=>x.id===p).installed)await f.change('install',null,p);await f.change('enable',s,p);}};return f;
}
const common=(s,id=randomUUID())=>({operationId:id,expectedSessionRevision:s.revision,expectedSummaryRevision:s.rollingSummary.revision});
const settings=(s,id=randomUUID())=>({...common(s,id),timing:'before',selectionId:item.selectionId,catalogRevision:item.selectionRevision});
async function seed(f,s,count){
 while(s.turns.length<count){const i=s.turns.length,id='synthetic-'+i,raw='回合 '+i+'\n<LongShortMemory>原文\t'+i+'</LongShortMemory>',selection=structuredClone(s.modelState.current);
  s.history.push({role:'user',content:'前置\n输入'+i+'\n后置'},{role:'assistant',content:raw});
  s.turns.push({requestId:id,raw,rawHash:sha(raw),model:{selectionRevision:s.modelState.revision,selection}});
  s.requests[id]={status:'complete',selectionRevision:s.modelState.revision,selection};s.revision++;
 }
 await f.store.write(s);return s;
}
const modelInput=(s,raw='合成摘要')=>({raw,finishReason:'stop',model:structuredClone(s.rollingSummary.config.model),receipt:{requestId:randomUUID(),requestHash:sha('synthetic request'),responseHash:sha(raw)},expectedActiveVersionId:s.rollingSummary.activeVersionId,targetThrough:s.turns.length-1});
async function syntheticResult(f,s,raw){s.rollingSummary=withSummaryVersion(s,modelSummaryVersion(s,modelInput(s,raw)));s.revision++;await f.store.write(s);return s;}
async function configured(f){let s=await f.create();await f.grant(s);return (await f.store.configureSummary(s.id,settings(s),f.plugins)).session;}

test('new v4 is explicit; frozen instruction equals B0; default full history and no model/calls',async t=>{
 const f=await fixture(t),s=await f.create();assert.equal(s.runtime.format,4);assert.equal(s.runtime.hash,summaryRuntime.hash);
 assert.deepEqual(s.rollingSummary.config,{timing:'before',model:null});assert.equal((await f.store.summaryStatus(s.id,f.plugins)).effectivePolicy.mode,'full-history');
 const examples=JSON.parse(await readFile(new URL('../../../docs/ai-rpg-experiment/rolling-summary/ASSEMBLY-EXAMPLES.json',import.meta.url),'utf8'));
 assert.equal(SUMMARY_INSTRUCTION_HASH,examples.summaryPromptSha256);assert.equal(SUMMARY_WRAPPER,examples.summaryWrapper);
 for(const [method,code] of [['send','DISPATCH'],['select','MODEL_SWITCH'],['createBranch','BRANCH'],['prepareHistoryRequest','ASSEMBLY']])await assert.rejects(f.store[method](s.id,{}),new RegExp('SUMMARY_'+code+'_NOT_CONNECTED'));
 await assert.rejects(f.store.create({...character,system:'injection'}),/SESSION_CONFIGURATION_INVALID/);
});
test('coverage, first and two rolling revisions, source hash, raw text and restart',async t=>{
 const f=await fixture(t);let s=await configured(f);
 for(let T=0;T<=4;T++){
  s=await seed(f,s,T);const source=summaryUpdateSource(s);assert.equal(source.targetThrough,Math.max(0,T-1));
  if(T<=1){assert.equal(source.needsUpdate,false);continue;}
  assert.deepEqual(source.source,s.history.slice((T-2)*2,(T-1)*2));assert.equal(source.previousSummary,T===2?null:'摘要'+(T-1));
  const history=structuredClone(s.history);s=await syntheticResult(f,s,'摘要'+T);assert.deepEqual(s.history,history);
  const status=await f.store.summaryStatus(s.id,f.plugins);assert.equal(status.effectivePolicy.ready,true);assert.equal(status.effectivePolicy.coveredThrough,T-1);assert.deepEqual(status.effectivePolicy.selectedTurns,[T]);
 }
 assert.equal(s.rollingSummary.versions.length,3);assert.equal(s.rollingSummary.versions[0].raw,'摘要2');
 const restarted=new SummarySessionStore(f.store.directory,()=>assert.fail('no dispatch'));await restarted.init();await restarted.requireRuntime(await restarted.read(s.id));
 assert.deepEqual((await restarted.read(s.id)).rollingSummary,s.rollingSummary);
});
test('M2 overrides M1 settings without erasing them; disable/uninstall restore current M1 or full',async t=>{
 const f=await fixture(t);let s=await configured(f);await f.grant(s,H);
 s=(await f.store.saveHistoryWindow(s.id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedConfigRevision:0,turns:7,includeInitialCharacter:true},f.plugins)).session;
 let status=await f.store.summaryStatus(s.id,f.plugins);assert.equal(status.effectivePolicy.mode,'rolling-summary');assert.equal((await f.store.historyWindowStatus(s.id,f.plugins)).temporarilyOverridden,true);
 const m1=structuredClone(s.historyWindow),summary=structuredClone(s.rollingSummary);
 await f.change('disable',s);assert.equal((await f.store.summaryStatus(s.id,f.plugins)).effectivePolicy.mode,'history-window');
 await f.change('enable',s);await f.change('uninstall');assert.equal((await f.store.summaryStatus(s.id,f.plugins)).effectivePolicy.mode,'history-window');
 await f.change('disable',s,H);assert.equal((await f.store.summaryStatus(s.id,f.plugins)).effectivePolicy.mode,'full-history');
 await f.change('install');assert.equal((await f.store.summaryStatus(s.id,f.plugins)).enabled,false);
 assert.deepEqual((await f.store.read(s.id)).historyWindow,m1);assert.deepEqual((await f.store.read(s.id)).rollingSummary,summary);
 f.plugins.summaryAuthorization=async()=>{throw Error('permission unknown');};await assert.rejects(f.store.summaryStatus(s.id,f.plugins),/permission unknown/);
});
test('strict settings, permission, model directory, idempotence, conflict and immutable story configuration',async t=>{
 const f=await fixture(t);let s=await f.create(),op=settings(s,'same');await f.change('install');await assert.rejects(f.store.configureSummary(s.id,op,f.plugins),/PLUGIN_NOT_AUTHORIZED/);await f.grant(s);
 assert.equal((await f.store.summaryStatus(s.id,f.plugins)).effectivePolicy.reason,'SUMMARY_MODEL_REQUIRED');
 for(const bad of [{...op,system:'x'},{...op,timing:'sometimes'},{...op,expectedSummaryRevision:-1}])await assert.rejects(f.store.configureSummary(s.id,bad,f.plugins),/SUMMARY_OPERATION_INVALID/);
 await assert.rejects(f.store.configureSummary(s.id,{...op,selectionId:'absent'},f.plugins),/MODEL_SELECTION_UNAVAILABLE/);
 const original=structuredClone(s);s=(await f.store.configureSummary(s.id,op,f.plugins)).session;
 for(const key of ['history','turns','params','characterText','modelState','runtime'])assert.deepEqual(s[key],original[key]);
 assert.equal((await f.store.configureSummary(s.id,op,f.plugins)).replayed,true);
 await assert.rejects(f.store.configureSummary(s.id,{...op,timing:'after'},f.plugins),/SUMMARY_OPERATION_COLLISION/);
 await assert.rejects(f.store.configureSummary(s.id,{...op,operationId:'other'},f.plugins),/SUMMARY_CONFIGURATION_CONFLICT/);
 await f.change('uninstall');assert.equal((await f.store.configureSummary(s.id,op,f.plugins)).replayed,true);
});
test('manual revisions, long Unicode edits, restore, unchanged raw and next rolling source',async t=>{
 const f=await fixture(t);let s=await configured(f);s=await seed(f,s,2);s=await syntheticResult(f,s,'原始模型输出');const original=s.rollingSummary.versions[0],history=structuredClone(s.history);
 const edit={...common(s,'edit'),text:'😀'.repeat(10000)};s=(await f.store.reviseSummary(s.id,edit,f.plugins)).session;
 assert.equal((await f.store.reviseSummary(s.id,edit,f.plugins)).replayed,true);assert.equal(s.rollingSummary.versions[0].raw,'原始模型输出');
 assert.equal(summaryUpdateSource(s).previousSummary,edit.text);assert.deepEqual(s.history,history);
 s=(await f.store.reviseSummary(s.id,{...common(s),restoreVersionId:original.id},f.plugins)).session;
 assert.equal(s.rollingSummary.versions.at(-1).restoredFrom,original.id);assert.equal(summaryUpdateSource(s).previousSummary,'原始模型输出');
 const restored=s.rollingSummary.activeVersionId;s=await seed(f,s,3);s=await syntheticResult(f,s,'覆盖至2');
 await assert.rejects(f.store.reviseSummary(s.id,{...common(s),restoreVersionId:restored},f.plugins),/SUMMARY_RESTORE_COVERAGE_MISMATCH/);
 for(const text of ['', '   ', '😀'.repeat(10001)])await assert.rejects(f.store.reviseSummary(s.id,{...common(s),text},f.plugins),/SUMMARY_OPERATION_INVALID/);
});
test('invalid/truncated/oversize/future output cannot replace valid version or falsify coverage',async t=>{
 const f=await fixture(t);let s=await configured(f);s=await seed(f,s,2);s=await syntheticResult(f,s,'旧摘要');s=await seed(f,s,3);const before=structuredClone(s);
 for(const patch of [{raw:''},{raw:'😀'.repeat(10001)},{finishReason:'length'}])assert.throws(()=>modelSummaryVersion(s,{...modelInput(s),...patch}),/SUMMARY_OUTPUT_REJECTED/);
 for(const patch of [{targetThrough:3},{expectedActiveVersionId:null}])assert.throws(()=>modelSummaryVersion(s,{...modelInput(s),...patch}),/SUMMARY_COVERAGE_CONFLICT/);
 assert.throws(()=>modelSummaryVersion(s,{...modelInput(s),receipt:{requestId:'x',requestHash:sha('x'),responseHash:sha('wrong')}}),/SUMMARY_RECEIPT_INVALID/);
 assert.deepEqual(s,before);const policy=(await f.store.summaryStatus(s.id,f.plugins)).effectivePolicy;assert.equal(policy.ready,false);assert.equal(policy.reason,'SUMMARY_UPDATE_REQUIRED');
 const changed=structuredClone(s);changed.history[0].content+='tamper';assert.throws(()=>requireSummaryState(changed),/SUMMARY_STATE_INVALID/);
 const corrupt=structuredClone(s);corrupt.rollingSummary.versions[0].effectiveText='changed';corrupt.rollingSummary=sealSummary(corrupt.rollingSummary);assert.throws(()=>requireSummaryState(corrupt),/SUMMARY_STATE_INVALID/);
});
test('pending generation, shared lock and revocation between proposal and commit deny mutations',async t=>{
 const f=await fixture(t);let s=await configured(f);s.requests.unknown={status:'pending'};await f.store.write(s);
 await assert.rejects(f.store.configureSummary(s.id,settings(s),f.plugins),/SUMMARY_UNCONFIRMED_REQUEST/);delete s.requests.unknown;await f.store.write(s);
 await f.store.exclusive(s.id,async()=>{await assert.rejects(f.store.configureSummary(s.id,settings(s),f.plugins),/SESSION_BUSY/);});
 const invoke=f.plugins.invoke;let release,entered;const ready=new Promise(r=>entered=r);
 f.plugins.invoke=async x=>{const ticket=await invoke(x);entered();await new Promise(r=>release=r);return ticket;};
 const pending=f.store.configureSummary(s.id,settings(s),f.plugins);await ready;await f.change('disable',s);release();await assert.rejects(pending,/SUMMARY_AUTHORIZATION_CHANGED/);
 assert.deepEqual((await f.store.read(s.id)).rollingSummary,s.rollingSummary);
});
test('failed publish keeps old version and recoverable intent; restart never replays new or old IDs',async t=>{
 const f=await fixture(t);let s=await configured(f);const op={...settings(s,'disk-fail'),timing:'after'};f.store.publishSummary=async()=>{throw Error('synthetic disk failure');};
 await assert.rejects(f.store.configureSummary(s.id,op,f.plugins),/synthetic disk failure/);
 const disk=await f.store.read(s.id);assert.equal(disk.rollingSummary.pending,'disk-fail');assert.deepEqual(disk.rollingSummary.config,s.rollingSummary.config);
 const restarted=new SummarySessionStore(f.store.directory,()=>assert.fail('never replay'),null,{control:f.control});await restarted.init();
 await assert.rejects(restarted.configureSummary(s.id,{...op,operationId:'new-id'},f.plugins),/SUMMARY_OPERATION_UNCONFIRMED/);
 assert.equal((await restarted.recoverSummaryOperation(s.id,'wrong')).status,'not-found');assert.equal((await restarted.read(s.id)).rollingSummary.pending,'disk-fail');
 assert.equal((await restarted.recoverSummaryOperation(s.id,'disk-fail')).status,'not-applied');assert.equal((await restarted.configureSummary(s.id,op,f.plugins)).operation.status,'not-applied');
 assert.equal((await readdir(f.store.directory)).filter(x=>x.endsWith('.tmp')).length,0);
});
test('lost successful response recovered once; input snapshot and same-session concurrent writes',async t=>{
 const f=await fixture(t);let s=await configured(f);const op=settings(s,'lost'),publish=f.store.publishSummary.bind(f.store);
 f.store.publishSummary=async value=>{await publish(value);throw Error('lost response');};await assert.rejects(f.store.configureSummary(s.id,op,f.plugins),/lost response/);
 assert.equal((await f.store.recoverSummaryOperation(s.id,'lost')).status,'complete');assert.equal((await f.store.configureSummary(s.id,op,f.plugins)).replayed,true);
 f.store.publishSummary=publish;s=await f.store.read(s.id);let release,entered;const ready=new Promise(r=>entered=r);
 f.store.publishSummary=async value=>{entered();await new Promise(r=>release=r);return publish(value);};
 const input=settings(s,'snapshot'),pending=f.store.configureSummary(s.id,input,f.plugins);input.timing='after';await ready;
 await assert.rejects(f.store.configureSummary(s.id,settings(s),f.plugins),/SESSION_BUSY/);release();s=(await pending).session;assert.equal(s.rollingSummary.config.timing,'before');
});
test('old sessions remain byte-identical; runtime mismatch, missing history and unproved summary refused',async t=>{
 const f=await fixture(t),oldStore=new HistorySessionStore(f.store.directory,()=>assert.fail('no dispatch'));const old=await oldStore.create(character),before=await readFile(oldStore.path(old.id));
 assert.equal((await f.store.summaryStatus(old.id,f.plugins)).compatible,false);await assert.rejects(f.store.configureSummary(old.id,{operationId:'legacy',expectedSessionRevision:0,expectedSummaryRevision:0,timing:'before',selectionId:item.selectionId,catalogRevision:item.selectionRevision},f.plugins),/SUMMARY_RUNTIME_INCOMPATIBLE/);
 assert.deepEqual(await readFile(oldStore.path(old.id)),before);
 let s=await configured(f);await assert.rejects(f.store.reviseSummary(s.id,{...common(s),text:'invented memory'},f.plugins),/SUMMARY_NOT_AVAILABLE/);
 s.runtime.hash=sha('wrong');await f.store.write(s);await assert.rejects(f.store.summaryStatus(s.id,f.plugins),/RUNTIME_BINDING_INVALID/);
});

test('revocation and re-enable while resolving model cannot revive the original settings operation',async t=>{
 const f=await fixture(t),s=await configured(f);let release,entered;const ready=new Promise(r=>entered=r);
 f.control.catalog=async()=>{entered();await new Promise(r=>release=r);return [item];};
 const pending=f.store.configureSummary(s.id,settings(s),f.plugins);await ready;await f.change('disable',s);await f.change('enable',s);release();
 await assert.rejects(pending,/SUMMARY_AUTHORIZATION_CHANGED/);assert.deepEqual((await f.store.read(s.id)).rollingSummary,s.rollingSummary);
});
test('pending setting has honest not-ready status and cannot be hidden by disabling summary',async t=>{
 const f=await fixture(t),s=await configured(f);f.store.publishSummary=async()=>{throw Error('disk');};
 await assert.rejects(f.store.configureSummary(s.id,settings(s,'pending'),f.plugins),/disk/);
 await f.change('disable',s);const status=await f.store.summaryStatus(s.id,f.plugins);assert.equal(status.effectivePolicy.mode,'full-history');assert.equal(status.effectivePolicy.ready,false);assert.equal(status.effectivePolicy.reason,'SUMMARY_OPERATION_UNCONFIRMED');
});
