import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {AuthoringStore} from './store.mjs';
import {blankDocument} from './contracts.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {ControlPlaneModelControl,PARAMETER_SELECTION_FORMAT} from './model-control.mjs';
import {PARAMETER_PROFILE,PARAMETER_CONFIG} from './generation-settings.mjs';
import {ControlledProvider} from './controlled-provider.mjs';
import {JointSessionHost} from './joint-session.mjs';
import {jointSessionId} from './joint-session-state.mjs';
import {JointBudget} from './joint-budget.mjs';
import {createAuthoringHistoryControl} from './history-control.mjs';
import {openComparisonHost} from './comparison-host.mjs';
import {TurnPlayback} from './turn-playback.mjs';
import {createPresentationCache} from './presentation.mjs';
import {formalComparisonControl} from './comparison-test-support.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/c1b-host-tests/',import.meta.url));await mkdir(root,{recursive:true});
async function fixture(t,{plugins=false,limit=30,effects=false}={}){
 const remote=await formalComparisonControl(t),directory=await mkdtemp(join(root,'run-')),store=await AuthoringStore.open(join(directory,'works'));
 const doc=blankDocument();doc.metadata.name='独立港口作品';doc.metadata.audience='male';doc.sources.system='作者冻结 system';doc.sources.prefix='前置';doc.sources.suffix='后置';
 if(effects){doc.settings.variablePanel={enabled:true,skeleton:'{"energy":0}',template:'<p>{{energy}}</p>',instructions:'返回能量'};doc.enhancements.cg.entries=['0.2','0.9'].map((key,i)=>({id:'image-'+i,name:key,categoryId:null,enabled:true,roles:['assistant'],rule:'or',keywords:['候选原文 '+key],target:'chat',mode:'regular',images:[{alias:'scene',weight:100}],special:{enabled:true,showNameWhenLocked:false,hintEnabled:true,hint:'港口见面'}}));}
 const work=await store.create({operationId:'work',expectedRevision:null,document:doc});if(effects)await store.putAsset(work.workId,{operationId:'image',expectedRevision:0,alias:'scene',fileName:'test.png',mediaType:'image/png',bytes:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII=','base64')});
 const pub=await store.publish(work.workId,{operationId:'publish',expectedRevision:effects?1:0}),reference={workId:work.workId,versionId:pub.result.versionId};
 const resolveRuntime=r=>openPublishedRuntime(store,r),control=new ControlPlaneModelControl({origin:remote.origin,serviceToken:remote.token});await control.refresh();
 let host,history,budget,provider,comparison,playback,cache;const id=jointSessionId('opening');
 async function open(){history=await createAuthoringHistoryControl({directory:join(directory,'plugins'),lookupSession:id=>host.lookupSession(id),resolveRuntime});budget=await JointBudget.open({directory:join(directory,'budget'),allowanceId:'shared',limit});provider=await ControlledProvider.open({directory:join(directory,'provider'),origin:remote.origin,serviceToken:remote.token});host=await JointSessionHost.open({directory:join(directory,'sessions'),resolveRuntime,provider,budget,modelControl:control,historyControl:history});playback=await TurnPlayback.open({directory:join(directory,'display'),host,resolveRuntime,cache:(cache=createPresentationCache()),origin:()=>remote.origin});comparison=await openComparisonHost({directory:join(directory,'comparison'),host,budget,playback,origin:remote.origin,serviceToken:remote.token});}
 async function close(){await comparison.close();await playback.close();await host.close();await provider.close();await history.close();await budget.close();}
 await open();t.after(async()=>{await close();await store.close();});await host.create({operationId:'opening',expectedRevision:null,reference,openingText:'雾港开局',presetId:null});
 async function selection(purpose,temperature=.4){const c=await control.parameterCatalog(),e=c.models.find(m=>m.available);return {format:PARAMETER_SELECTION_FORMAT,catalogRevision:c.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,capabilityRevision:e.capabilityRevision,profile:{format:PARAMETER_PROFILE,purpose,modelId:e.modelId,parameters:{format:PARAMETER_CONFIG,values:{temperature:{mode:'explicit',value:temperature}}}}};}
 for(const purpose of ['story','summary','memory']){const s=(await host.read(id)).session;await host.configureModel({sessionId:id,operationId:randomUUID(),expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,choice:await selection(purpose)});}
 if(plugins)for(const pluginId of ['rpg.rolling-summary','rpg.memory-palace'])for(const action of ['install','enable']){const c=await history.pluginService.catalog(),p=c.plugins.find(p=>p.id===pluginId);await history.pluginService.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(action==='enable'?{permissions:p.permissions,sessionId:id,expectedSessionRevision:(await host.lookupSession(id)).revision}:{})});}
 return {...remote,id,selection,get host(){return host;},get comparison(){return comparison;},get budget(){return budget;},get playback(){return playback;},frame:p=>cache.get(p.token).frame,async reopen(){await close();await open();},async intent(input='雾港开局'){const state=await host.comparisonInspect(id);return {sessionId:id,operationId:randomUUID(),expectedContextRevision:state.contextRevision,expectedAuthorizationRevision:state.authorizationRevision,input,candidates:[{id:'a',choice:await selection('story',.2)},{id:'b',choice:await selection('story',.9)}]};}};
}
test('formal Python quota + real authored host freeze, choose once, replay and ordinary shared allowance',async t=>{
 const f=await fixture(t),intent=await f.intent(),pre=await f.comparison.preflight(intent);assert.equal(pre.maximumCalls,2);assert.equal((await f.evidence()).calls.length,0);
 const created=await f.comparison.create(intent),done=await f.comparison.wait(f.id,created.plan.id);assert.equal(done.status,'awaiting-choice',JSON.stringify(done.events));assert.deepEqual(done.tasks.map(t=>t.state),['completed','completed'],JSON.stringify(done.events));assert.equal(done.tasks[0].request.messagesHash,done.tasks[1].request.messagesHash);assert.equal((await f.host.read(f.id)).session.turns.length,0);
 const pick={operationId:'pick',expectedContextRevision:done.context.contextRevision,candidateId:'b'},selected=await f.comparison.adopt(f.id,done.plan.id,pick);assert.equal(selected.status,'settled',JSON.stringify(selected.events));
 const v=await f.host.read(f.id);assert.equal(v.session.turns.length,1);assert.match(v.session.turns[0].assistant,/0.9/);assert.equal(v.session.models.current.story.wireParameters.temperature,.4);assert.equal(v.budget.used,2);assert.equal(v.budget.reserved,0);assert.ok(v.session.operations[0].receipt.comparison);
 assert.equal((await f.playback.status(f.id,1)).state,'ready');await f.comparison.adopt(f.id,done.plan.id,pick);await assert.rejects(()=>f.comparison.adopt(f.id,done.plan.id,{...pick,operationId:'other',candidateId:'a'}),{code:'COMPARISON_DECISION_CONFLICT'});
 await f.reopen();await f.comparison.reconcile(f.id,done.plan.id);assert.equal((await f.host.read(f.id)).session.turns.length,1);assert.equal((await f.evidence()).calls.length,2);
 const second=await f.comparison.create(await f.intent('继续'));await f.comparison.wait(f.id,second.plan.id);await f.comparison.discard(f.id,second.plan.id,{operationId:'discard',expectedContextRevision:(await f.comparison.read(f.id,second.plan.id)).context.contextRevision});assert.equal((await f.host.read(f.id)).session.draft,'继续');assert.equal((await f.host.read(f.id)).session.turns.length,1);
});
test('adopted original alone advances actual M2 then M3; no effects for rejected candidates',async t=>{
 const f=await fixture(t,{plugins:true});for(const input of ['雾港开局','第二轮']){const b=await f.comparison.create(await f.intent(input)),done=await f.comparison.wait(f.id,b.plan.id);assert.equal(done.status,'awaiting-choice',JSON.stringify(done.events));const selected=await f.comparison.adopt(f.id,b.plan.id,{operationId:randomUUID(),expectedContextRevision:done.context.contextRevision,candidateId:'a'});assert.equal(selected.status,'settled',JSON.stringify(selected.events));}
 const v=await f.host.read(f.id);assert.equal(v.session.turns.length,2);assert.equal(v.session.memory.snapshot.processedThrough,2);assert.equal(v.session.summary.snapshot.versions.length,1);assert.equal(v.budget.used,7);assert.equal(v.budget.reserved,0);assert.equal((await f.evidence()).calls.length,7);for(const j of v.session.memory.jobs)assert.ok(!j.request.messages[1].content.includes('候选原文 0.9'));
});

test('lost HTTP completion acknowledgements recover original tasks after reopen with no second dispatch',async t=>{
 const f=await fixture(t);await f.fault({dropTasks:2,blockLookup:true});const b=await f.comparison.create(await f.intent()),done=await f.comparison.wait(f.id,b.plan.id);assert.equal(done.status,'blocked');assert.ok(done.tasks.every(t=>t.state==='unknown'));assert.equal((await f.evidence()).calls.length,2);assert.equal((await f.host.read(f.id)).session.turns.length,0);
 await f.reopen();await f.fault({blockLookup:false});const recovered=await f.comparison.reconcile(f.id,b.plan.id);assert.equal(recovered.status,'awaiting-choice',JSON.stringify(recovered.events));await f.comparison.adopt(f.id,b.plan.id,{operationId:'recover-pick',expectedContextRevision:recovered.context.contextRevision,candidateId:'a'});assert.equal((await f.host.read(f.id)).session.turns.length,1);assert.equal((await f.evidence()).calls.length,2);assert.equal((await f.host.read(f.id)).budget.used,2);
});
test('local budget cannot cover the entire batch: no provider request or host run starts',async t=>{
 const f=await fixture(t,{limit:1});await assert.rejects(f.comparison.create(await f.intent()),{code:'COMPARISON_BUDGET_EXHAUSTED'});assert.equal((await f.evidence()).calls.length,0);const v=await f.host.read(f.id);assert.equal(v.session.runs.length,0);assert.equal(v.budget.used,0);assert.equal(v.budget.reserved,0);
});

test('only adopted candidate advances the native variable panel and unlocks its CG; recovery is idempotent',async t=>{
 const f=await fixture(t,{effects:true}),b=await f.comparison.create(await f.intent()),done=await f.comparison.wait(f.id,b.plan.id);assert.equal((await f.playback.gallery(f.id)).unlocked,0);assert.equal((await f.host.read(f.id)).session.turns.length,0);
 const pick={operationId:'effects-pick',expectedContextRevision:done.context.contextRevision,candidateId:'a'};assert.equal((await f.comparison.adopt(f.id,b.plan.id,pick)).status,'settled');let s=(await f.host.read(f.id)).session;assert.equal(s.turns[0].variablePanel.snapshot.data.energy,2);assert.equal(s.turns[0].variablePanel.snapshot.revision,1);assert.equal((await f.playback.gallery(f.id)).unlocked,1);
 await f.reopen();await f.comparison.reconcile(f.id,b.plan.id);s=(await f.host.read(f.id)).session;assert.equal(s.turns.length,1);assert.equal(s.turns[0].variablePanel.snapshot.revision,1);const g=await f.playback.gallery(f.id);assert.equal(g.unlocked,1);assert.equal(g.cards.find(c=>c.unlock).name,'0.2');assert.equal((await f.evidence()).calls.length,2);
});

test('formal HTTP runs no more than two providers and early adoption waits for the remaining candidates',async t=>{
 const f=await fixture(t);await f.fault({delay:1});const intent=await f.intent();intent.candidates.push({id:'c',choice:await f.selection('story',.6)},{id:'d',choice:await f.selection('story',.7)});const b=await f.comparison.create(intent);let seen;
 for(let n=0;n<600;n++){seen=await f.comparison.read(f.id,b.plan.id);if(seen.tasks[0].state==='completed')break;await new Promise(r=>setTimeout(r,25));}assert.equal(seen.tasks[0].state,'completed');assert.ok(seen.tasks.some(t=>!['completed','released'].includes(t.state)));
 const picked=await f.comparison.adopt(f.id,b.plan.id,{operationId:'early',expectedContextRevision:seen.context.contextRevision,candidateId:'a'});assert.equal((await f.host.read(f.id)).session.turns.length,1);assert.notEqual(picked.status,'settled');await assert.rejects(f.host.comparisonInspect(f.id));await f.comparison.wait(f.id,b.plan.id);const final=await f.comparison.read(f.id,b.plan.id);assert.equal(final.status,'settled');const evidence=await f.evidence();assert.equal(evidence.calls.length,4);assert.equal(evidence.maximum,2);assert.equal((await f.host.read(f.id)).session.turns.length,1);
});


test('comparison freeze rechecks capability after reservation outlives the control cache',async t=>{
 const f=await fixture(t),{plan}=await f.comparison.preflight(await f.intent());
 await new Promise(resolve=>setTimeout(resolve,31000));
 const context=await f.host.comparisonFreeze({plan});
 assert.ok(context.messages.length>=2);assert.equal(context.source.input,'雾港开局');
 assert.equal((await f.evidence()).calls.length,0);
 assert.equal((await f.host.read(f.id)).session.turns.length,0);
});


test('candidate preview hides only validated panel metadata without committing or rewriting the frozen result',async t=>{
 const f=await fixture(t,{effects:true}),b=await f.comparison.create(await f.intent()),done=await f.comparison.wait(f.id,b.plan.id);
 const before=await f.host.read(f.id),frozen=JSON.stringify(done),calls=(await f.evidence()).calls.length;
 assert.ok(done.context.source.variables);
 for(const task of done.tasks){
  assert.match(task.result.raw,/<variable_panel>/);
  const view=await f.playback.candidate(f.id,done,task),html=f.frame(view);
  assert.match(html,/候选原文/);assert.doesNotMatch(html,/variable_panel|&quot;energy&quot;/);
  assert.equal(view.manifest.binding.rawHash,task.result.rawHash);
 }
 const original=done.tasks[0];
 for(const raw of ['正文<variable_panel>{"energy":2}', '正文<variable_panel>{"energy":"wrong"}</variable_panel>', '正文<variable_panel>{bad}</variable_panel>', '正文<variable_panel>{"energy":2}</variable_panel><variable_panel>{"energy":3}</variable_panel>']){
  const task=structuredClone(original);task.result.raw=raw;
  const view=await f.playback.candidate(f.id,done,task);assert.match(f.frame(view),/variable_panel/);
 }
 const disabled=structuredClone(done);disabled.context.source.variables=null;
 assert.match(f.frame(await f.playback.candidate(f.id,disabled,original)),/variable_panel/);
 assert.deepEqual(await f.host.read(f.id),before);assert.equal(JSON.stringify(done),frozen);
 assert.equal((await f.playback.gallery(f.id)).unlocked,0);assert.equal((await f.evidence()).calls.length,calls);
});
