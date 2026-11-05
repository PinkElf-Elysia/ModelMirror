import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {AuthoringStore} from './store.mjs';
import {blankDocument} from './contracts.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {JointSessionHost} from './joint-session.mjs';
import {createJointSettings} from './joint-settings.mjs';
import {JointBudget} from './joint-budget.mjs';
import {BranchCheckpoints} from './branch-storage.mjs';
import {createAuthoringHistoryControl} from './history-control.mjs';
import {ControlledProvider} from './controlled-provider.mjs';
import {CONTROLLED_MODE,CONTROLLED_SESSION_FORMAT,CONTROLLED_BRANCH_FORMAT} from './controlled-receipt.mjs';
import {jointSessionId} from './joint-session-state.mjs';
import {fakeControl,syntheticToken} from './controlled-test-support.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6al-tests/',import.meta.url));await mkdir(root,{recursive:true});
const plugins=['rpg.model-selector','rpg.history-window','rpg.rolling-summary','rpg.memory-palace','rpg.branch-save'];
async function fixture(t,options={}){
 const f=await fakeControl(t,options),directory=await mkdtemp(join(root,'run-')),store=await AuthoringStore.open(join(directory,'works'));
 const doc=blankDocument();doc.metadata.name='Generic protocol work';doc.metadata.audience='male';doc.sources.system='AUTHOR SYSTEM';doc.sources.prefix='PRE';doc.sources.suffix='POST';
 const work=await store.create({operationId:'work',expectedRevision:null,document:doc}),pub=await store.publish(work.workId,{operationId:'pub',expectedRevision:0}),reference={workId:work.workId,versionId:pub.result.versionId};
 const resolveRuntime=r=>openPublishedRuntime(store,r);let host,history,provider,budget,branches;
 async function open(){
  await f.modelControl.refresh();history=await createAuthoringHistoryControl({directory:join(directory,'plugins'),lookupSession:id=>host.lookupSession(id),resolveRuntime});
  provider=await ControlledProvider.open({directory:join(directory,'provider'),origin:f.origin,serviceToken:syntheticToken});
  budget=await JointBudget.open({directory:join(directory,'budget'),allowanceId:'shared',limit:options.limit??30});branches=await BranchCheckpoints.open({directory:join(directory,'branches')});
  host=await JointSessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider,budget,modelControl:f.modelControl,historyControl:history,branchStore:branches,fault:options.fault});
 }
 async function close(){await host.close();await history.close();await provider.close();await budget.close();await branches.close();}
 await open();const id=jointSessionId('session');await host.create({operationId:'session',expectedRevision:null,reference,openingText:'Opening',presetId:null});
 t.after(async()=>{await close();await store.close();});
 const api={...f,id,get host(){return host;},get provider(){return provider;},read:(target=id)=>host.read(target),
  async reopen(){await close();await open();},
  async settings(){const service=await createJointSettings({directory:join(directory,'settings-view'),host,control:history,resolveRuntime});try{return await service.read(id);}finally{await service.close();}},
  async select(purpose,target=id){const s=(await host.read(target)).session,c=f.modelControl.catalog(),e=c.models.find(m=>m.modelId==='synthetic/'+purpose);return host.configureModel({sessionId:target,operationId:randomUUID(),expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,choice:{catalogRevision:c.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,parameters:{maxOutputTokens:4096}}});},
  async change(action,pluginId,target=id){const service=history.pluginService,c=await service.catalog(),p=c.plugins.find(p=>p.id===pluginId);return service.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,
   ...(['enable','disable'].includes(action)?{sessionId:target,expectedSessionRevision:(await host.lookupSession(target)).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},
  async send(operationId='one',input='Opening'){const v=await host.read(id);const intent={sessionId:id,operationId,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,kind:'send',input};const out=await host.send(intent);await host.waitForIdle(id);return {out,intent};},
  async recover(operationId='one'){const v=await host.read(id);return host.recover({sessionId:id,operationId,expectedRevision:v.session.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision});},
  async enabled(plugin,target=id){return (await history.pluginService.sessionStatus(target,plugin)).enabled;}
 };
 for(const p of plugins)await api.change('install',p);
 await api.change('enable',plugins[0]);for(const p of ['story','summary','memory'])await api.select(p);
 for(const p of plugins.slice(1))if(!options.disabled?.includes(p))await api.change('enable',p);
 return api;
}
test('five plugins execute against controlled protocol with M2 then M3 and durable branch isolation',async t=>{
 const f=await fixture(t);assert.equal(f.host.readiness.mode,CONTROLLED_MODE);assert.equal((await f.settings()).realProvider,true);
 await f.send();await f.send('two','Continue');const v=await f.read();
 assert.deepEqual(f.calls.map(p=>p.purpose),['story','memory','story','summary','memory']);
 assert.equal(v.session.format,CONTROLLED_SESSION_FORMAT);assert.equal(v.session.turns.length,2);assert.equal(v.budget.used,5);assert.equal(v.budget.reserved,0);
 assert.ok(v.session.models.changes[0].pluginReceipt);assert.equal(v.session.summary.snapshot.versions.length,1);assert.equal(v.session.memory.snapshot.processedThrough,2);
 assert.equal(v.session.summary.jobs[0].receipt.kind,'control-plane');assert.equal(v.session.memory.jobs[1].request.source.turns[0].assistant,'Full original story.');
 const branch=await f.host.fork({sessionId:f.id,operationId:'fork',expectedRevision:v.session.revision,expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision,expectedContextPolicyRevision:v.effectivePolicy.revision,turn:2,name:'Child'});
 assert.equal(branch.session.format,CONTROLLED_BRANCH_FORMAT);assert.equal(branch.session.turns.length,2);assert.equal(branch.session.models.current.story,null);assert.equal(branch.session.operations.length,0);
 for(const p of plugins)assert.equal(await f.enabled(p,branch.session.id),false);
 const child=await f.read(branch.session.id);assert.equal(child.budget.used,5);
 await f.reopen();assert.equal((await f.read()).session.turns.length,2);assert.equal((await f.read(branch.session.id)).session.turns.length,2);assert.equal(f.calls.length,5);
});
test('lost story response survives host restart and recovers by original GET only',async t=>{
 const f=await fixture(t,{dropAfterStored:true});const {intent}=await f.send();assert.equal((await f.read()).status,'unknown');assert.equal(f.calls.length,1);
 await f.reopen();await f.recover();assert.equal(f.calls.length,1);const v=await f.read();assert.equal(v.session.turns.length,1);assert.equal(v.budget.used,1);
 assert.equal((await f.host.send(intent)).replayed,true);assert.equal(f.calls.length,1);
});
test('insufficient shared reserve starts no controlled task',async t=>{
 const f=await fixture(t,{limit:1});await f.send();assert.equal(f.calls.length,0);assert.equal((await f.read()).session.runs[0].diagnostic,'JOINT_BUDGET_EXHAUSTED');
});
for(const [m2,m3] of [[false,false],[true,false],[false,true],[true,true]])test('controlled plugin combination M2='+m2+' M3='+m3,async t=>{
 const disabled=[...(!m2?['rpg.rolling-summary']:[]),...(!m3?['rpg.memory-palace']:[])],f=await fixture(t,{disabled});
 await f.send();await f.send('two','Continue');const purposes=f.calls.map(c=>c.purpose);
 assert.equal(purposes.filter(p=>p==='story').length,2);assert.equal(purposes.filter(p=>p==='summary').length,m2?1:0);assert.equal(purposes.filter(p=>p==='memory').length,m3?2:0);
 assert.equal((await f.read()).budget.used,2+(m2?1:0)+(m3?2:0));
});

test('unknown memory result survives restart and recovery does not resubmit',async t=>{
 const f=await fixture(t,{dropAfterStored:true,dropPurpose:'memory'});await f.send();let v=await f.read();
 assert.equal(v.session.turns.length,1);assert.equal(v.session.memory.jobs[0].state,'unknown');assert.equal(f.calls.length,2);
 await f.reopen();await f.recover();v=await f.read();assert.equal(v.session.memory.snapshot.processedThrough,1);assert.equal(v.session.memory.jobs[0].state,'applied');assert.equal(f.calls.length,2);
});
test('summary compression remains one independent task before memory',async t=>{
 const f=await fixture(t,{text:p=>p.purpose==='summary'?'L'.repeat(10001):p.purpose==='compression'?'Short summary':p.purpose==='memory'?'{"operations":[]}':'Original story'});
 await f.send();await f.send('two','Continue');const v=await f.read();
 assert.deepEqual(f.calls.map(p=>p.purpose),['story','memory','story','summary','compression','memory']);assert.equal(v.budget.used,6);assert.equal(v.budget.reserved,0);
 assert.equal(v.session.summary.jobs[0].compressionReceipt.kind,'control-plane');assert.equal(v.session.summary.jobs[0].receipt.result.text.length,10001);
});
test('revoked memory result is retained but not applied',async t=>{
 let f;f=await fixture(t,{onDispatch:async p=>{if(p.purpose==='memory')await f.change('disable','rpg.memory-palace');}});
 await f.send();const v=await f.read();assert.equal(v.session.turns.length,1);assert.equal(v.session.memory.jobs[0].state,'revoked');assert.ok(v.session.memory.jobs[0].receipt);
 assert.equal(v.session.memory.snapshot.processedThrough,0);assert.equal(v.budget.used,2);assert.equal(f.calls.length,2);
});
test('disabling, uninstalling and reinstalling each plugin preserves calls and grants separation',async t=>{
 const f=await fixture(t);await f.send();const before=(await f.read()).budget.used;
 for(const p of plugins){await f.change('disable',p);await f.change('uninstall',p);await f.change('install',p);assert.equal(await f.enabled(p),false);}
 assert.equal((await f.read()).budget.used,before);assert.equal(f.calls.length,2);
});


test('disabled readiness rejects before task creation, reservation and transport',async t=>{
 const f=await fixture(t,{ready:false});const before=await f.read();await assert.rejects(()=>f.send(),{code:'CONTROL_EXECUTION_DISABLED'});
 const after=await f.read();assert.equal(after.session.revision,before.session.revision);assert.equal(after.session.operations.length,0);assert.equal(after.session.runs.length,0);assert.equal(after.budget.used,0);assert.equal(after.budget.reserved,0);assert.equal(f.calls.length,0);
 const s=after.session;await assert.rejects(()=>f.host.estimate({sessionId:f.id,operationId:'estimate',expectedRevision:s.revision,expectedModelRevision:s.models.revision,expectedContextPolicyRevision:after.effectivePolicy.revision,expectedSummaryRevision:s.summary.snapshot.revision,expectedMemoryRevision:s.memory.snapshot.revision,kind:'send',input:'Opening'}),{code:'CONTROL_EXECUTION_DISABLED'});assert.equal(f.calls.length,0);
});
for(const lost of [false,true])test('gate closing after readiness releases confirmed undispatched slot; lost='+lost,async t=>{
 const f=await fixture(t,{status:'failed',dispatched:false,dropAfterStored:lost});const {intent}=await f.send();
 if(lost){assert.equal((await f.read()).status,'unknown');assert.equal((await f.read()).budget.used,1);await f.reopen();await f.recover();}
 const v=await f.read();assert.equal(v.session.turns.length,0);assert.equal(v.session.operations[0].state,'failed');assert.equal(v.session.operations[0].receipt.control.dispatched,false);
 assert.equal(v.budget.used,0);assert.equal(v.budget.reserved,0);assert.equal(v.budget.remaining,30);assert.equal(f.calls.length,1);
 await f.reopen();assert.equal((await f.host.send(intent)).replayed,true);assert.equal((await f.read()).budget.used,0);assert.equal(f.calls.length,1);
});
