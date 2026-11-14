import test from 'node:test';import assert from 'node:assert/strict';
import {join} from 'node:path';
import {baselineFixture} from './baseline-fixture.mjs';
const currentRoot=new URL('../authoring/',import.meta.url);
async function open(root,directory,kind){
 const module=async n=>import(new URL(n+'.mjs',root));
 const {AuthoringStore}=await module('store'),{blankDocument,sha256}=await module('contracts'),{openPublishedRuntime}=await module('runtime-binding');
 const {OfflineFixtureProvider}=await module('offline-session'),{OfflineModelControl}=await module('model-control'),{createAuthoringHistoryControl}=await module('history-control');
 const store=await AuthoringStore.open(join(directory,'works')),resolveRuntime=r=>openPublishedRuntime(store,r);
 const modelControl=new OfflineModelControl({catalogId:'character-legacy-check',models:[{selectionId:'story',selectionRevision:sha256('story'),modelId:'fixture/story',name:'fixture',available:true,contextLength:256000,maxOutputTokens:16000,parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}}]});
 let host,budget;
 const historyControl=await createAuthoringHistoryControl({directory:join(directory,'plugins'),lookupSession:id=>host.lookupSession(id),resolveRuntime});
 const provider=await OfflineFixtureProvider.open(join(directory,'provider'),{replies:{one:{status:'completed',text:'基线中的原文',finishReason:'stop'},two:{status:'completed',text:'角色扩展后续聊',finishReason:'stop'}}});
 const options={directory:join(directory,'sessions'),resolveRuntime,provider,modelControl,historyControl};
 if(kind==='offline'){const {OfflineSessionHost}=await module('offline-session');host=await OfflineSessionHost.open({directory:options.directory,resolveRuntime,provider});}
 else if(kind==='joint'){const {JointBudget}=await module('joint-budget');budget=await JointBudget.open({directory:join(directory,'budget'),allowanceId:'legacy-check',limit:8});const {JointSessionHost}=await module('joint-session');host=await JointSessionHost.open({...options,budget});}
 else {const {SummarySessionHost}=await module('summary-session');host=await SummarySessionHost.open(options);}
 return {store,host,blankDocument,modelControl,sha256,async close(){await host.close();await historyControl.close();await provider.close();await budget?.close();await store.close();}};
}
for(const kind of ['offline','summary','joint'])test('actual a6563a9b '+kind+' archive continues with exact original descriptors',async t=>{
 const {root:oldRoot,directory}=await baselineFixture();
 let f=await open(oldRoot,directory,kind);t.after(()=>f.close());const doc=f.blankDocument();doc.metadata.name='旧世界测试夹具';doc.metadata.audience='male';doc.sources.system='旧定义';
 const w=await f.store.create({operationId:'work',expectedRevision:null,document:doc}),p=await f.store.publish(w.workId,{operationId:'publish',expectedRevision:0});
 const created=await f.host.create({operationId:'session',expectedRevision:null,reference:{workId:w.workId,versionId:p.result.versionId},openingText:'基线开局',presetId:null}),id=created.session.id;
 const s=(await f.host.read(id)).session,c=f.modelControl.catalog();
 if(kind!=='offline')await f.host.configureModel({sessionId:id,operationId:'model',expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose:'story',choice:{catalogRevision:c.revision,selectionId:'story',selectionRevision:f.sha256('story'),parameters:{maxOutputTokens:4096}}});
 async function send(operationId,input){const v=await f.host.read(id);if(kind==='offline'){await f.host.send({sessionId:id,operationId,input,expectedRevision:v.session.revision});return;}await f.host.send({sessionId:id,operationId,input,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,...(kind==='joint'?{kind:'send',expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision}:{})});await f.host.waitForIdle(id);}
 await send('one','基线开局');const before=(await f.host.read(id)).session;assert.equal(before.turns.length,1);await f.close();
 f=await open(currentRoot,directory,kind);const restored=(await f.host.read(id)).session;assert.deepEqual(restored.binding,before.binding);assert.equal(restored.executionHash,before.executionHash);
 await send('two','继续');const after=(await f.host.read(id)).session;assert.equal(after.turns.length,2);assert.deepEqual(after.binding,before.binding);assert.equal(after.executionHash,before.executionHash);
 const {openPublishedRuntime}=await import(new URL('runtime-binding.mjs',currentRoot));const runtime=await openPublishedRuntime(f.store,{workId:w.workId,versionId:p.result.versionId});assert.throws(()=>runtime.requireBinding({...after.binding,runtimeHash:'0'.repeat(64)}));
});
