import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {formalComparisonControl} from '../authoring/comparison-test-support.mjs';
import {ControlPlaneModelControl,PARAMETER_SELECTION_FORMAT} from '../authoring/model-control.mjs';
import {PARAMETER_PROFILE,PARAMETER_CONFIG} from '../authoring/generation-settings.mjs';
import {createGenerationCards,GENERATION_CARDS} from './generation-cards.mjs';
import {newSetup} from '../god-card/lib/setup.mjs';
const inputFor=id=>id==='earth'?{characterText:'姓名：林舟\n身份：成年旅行者',world:'表世界'}:id==='god'?{setup:newSetup()}:{opening:'姓名：林舟\n身份：成年旅行者'};
async function fixture(t){
 const remote=await formalComparisonControl(t),root=new URL('../.rpg04-work/c1b-cards/',import.meta.url);await mkdir(root,{recursive:true});const directory=await mkdtemp(new URL('run-',root).pathname.replace(/^\/([A-Z]:)/,'$1'));
 const control=new ControlPlaneModelControl({origin:remote.origin,serviceToken:remote.token});await control.refresh();const catalog=await control.parameterCatalog(),model=catalog.models.find(m=>m.available);
 const config={directory:join(directory,'sessions'),budgetDirectory:join(directory,'budget'),origin:remote.origin,serviceToken:remote.token,control,defaultModel:model.modelId,limits:Object.fromEntries(Object.keys(GENERATION_CARDS).map(id=>[id,12])),comparison:true};
 let host=await createGenerationCards(config);t.after(()=>host.close());
 const selection=(purpose,temperature)=>({format:PARAMETER_SELECTION_FORMAT,catalogRevision:catalog.revision,selectionId:model.selectionId,selectionRevision:model.selectionRevision,capabilityRevision:model.capabilityRevision,profile:{format:PARAMETER_PROFILE,purpose,modelId:model.modelId,parameters:{format:PARAMETER_CONFIG,values:{temperature:{mode:'explicit',value:temperature}}}}});
 async function grant(s,id,action='enable'){let c=await host.plugins.catalog(),e=c.plugins.find(x=>x.id===id);assert.ok(e);if(!e.installed){await host.plugins.change('install',{operationId:randomUUID(),pluginId:id,version:e.version,artifactSha256:e.artifactSha256,manifestSha256:e.manifestSha256,expectedRegistryRevision:c.revision});c=await host.plugins.catalog();e=c.plugins.find(x=>x.id===id);}return host.plugins.change(action,{operationId:randomUUID(),pluginId:id,version:e.version,artifactSha256:e.artifactSha256,manifestSha256:e.manifestSha256,expectedRegistryRevision:c.revision,sessionId:s.id,expectedSessionRevision:s.revision,...(action==='enable'?{permissions:e.permissions}:{})});}
 async function intent(h,id,input){const state=await h.comparisonAdapter.comparisonInspect(id);return {sessionId:id,operationId:randomUUID(),expectedContextRevision:state.contextRevision,expectedAuthorizationRevision:state.authorizationRevision,input,candidates:[{id:'a',choice:selection('story',.2)},{id:'b',choice:selection('story',.9)}]};}
 return {...remote,model,selection,grant,intent,get host(){return host;},async reopen(){await host.close();host=await createGenerationCards(config);}};
}
test('six actual card stores freeze original assembly, adopt once and reopen with shared slot budgets',async t=>{
 const f=await fixture(t),sessions=[];
 for(const card of Object.keys(GENERATION_CARDS)){
  const h=f.host.hosts.get(card),s=await h.store.create(inputFor(card));await assert.rejects(h.comparisonAdapter.comparisonInspect(s.id),{code:'COMPARISON_AUTHORIZATION_REQUIRED'});await f.grant(s,'rpg.model-comparison');
  const catalog=await f.host.plugins.catalog();assert.ok(catalog.plugins.some(p=>p.id==='rpg.model-comparison'));
  assert.equal((await f.host.plugins.parameterAuthorization(s.id)).enabled,false);assert.equal((await f.host.plugins.sessionStatus(s.id,'rpg.model-selector')).enabled,false);
  const rs=await h.store.requirementsStatus(s.id);await h.store.configureRequirements(s.id,{operationId:randomUUID(),expectedSessionRevision:rs.sessionRevision,expectedRequirementsRevision:rs.revision,text:'第二人称；只写已发生的行动。'});
 const configured=(await h.store.read(s.id)).modelState,request=await f.intent(h,s.id,s.characterText),b=await h.comparison.create(request);
  const listed=await h.comparison.handle('GET',s.id);assert.equal(listed.state.pendingBatchId,b.plan.id);
  const done=await h.comparison.wait(s.id,b.plan.id);assert.equal(done.status,'awaiting-choice',JSON.stringify(done.events));assert.deepEqual(done.tasks.map(t=>t.state),['completed','completed'],JSON.stringify(done.events));assert.equal(done.tasks[0].request.messagesHash,done.tasks[1].request.messagesHash);assert.equal((await h.store.read(s.id)).turns.length,0);assert.match(done.tasks[0].request.messages.at(-1).content,/【持续要求】/);const pendingState=await h.store.requirementsStatus(s.id);await assert.rejects(h.store.configureRequirements(s.id,{operationId:randomUUID(),expectedSessionRevision:pendingState.sessionRevision,expectedRequirementsRevision:pendingState.revision,text:'不应改变冻结上下文'}));
  const pick={operationId:randomUUID(),expectedContextRevision:done.context.contextRevision,candidateId:'b'},adopted=await h.comparison.adopt(s.id,b.plan.id,pick);assert.equal(adopted.status,'settled',JSON.stringify(adopted.events));
  const result=await h.store.read(s.id);assert.equal(result.turns.length,1);assert.match(result.turns[0].raw,/0.9/);assert.deepEqual(result.modelState,configured);await h.profile.context.verifyMemory(result);assert.equal((await h.io.budget.status()).used,2);assert.equal((await h.io.budget.status()).reserved,0);
  await h.comparison.adopt(s.id,b.plan.id,pick);await assert.rejects(h.store.recoverMemoryOperation(s.id,request.operationId),{code:'COMPARISON_RECOVERY_REQUIRED'});sessions.push({card,id:s.id,batch:b.plan.id});
 }
 assert.equal((await f.evidence()).calls.length,12);await f.reopen();for(const {card,id,batch} of sessions){const h=f.host.hosts.get(card);await h.comparison.reconcile(id,batch);assert.equal((await h.store.read(id)).turns.length,1);assert.equal((await h.io.budget.status()).used,2);}assert.equal((await f.evidence()).calls.length,12);
});
test('card M2 then M3 consume shared holds only after adoption; discard keeps original history',async t=>{
 const f=await fixture(t),h=f.host.hosts.get('earth'),s=await h.store.create(inputFor('earth'));for(const id of ['rpg.model-comparison','rpg.rolling-summary','rpg.memory-palace'])await f.grant(await h.store.read(s.id),id);
 let current=await h.store.read(s.id);await h.store.configureSummary(s.id,{operationId:randomUUID(),expectedSessionRevision:current.revision,expectedSummaryRevision:current.rollingSummary.revision,timing:'after',selectionId:f.model.selectionId,catalogRevision:f.model.selectionRevision},f.host.plugins);
 current=await h.store.read(s.id);await h.store.memoryTasks.configure(s.id,{operationId:randomUUID(),expectedSessionRevision:current.revision,expectedMemoryRevision:current.memoryPalace.revision,selectionId:f.model.selectionId,catalogRevision:f.model.selectionRevision});
 for(const input of [s.characterText,'继续']){const b=await h.comparison.create(await f.intent(h,s.id,input)),done=await h.comparison.wait(s.id,b.plan.id);assert.equal(done.status,'awaiting-choice',JSON.stringify(done.events));const adopted=await h.comparison.adopt(s.id,b.plan.id,{operationId:randomUUID(),expectedContextRevision:done.context.contextRevision,candidateId:'a'});assert.equal(adopted.status,'settled',JSON.stringify(adopted.events));}
 current=await h.store.read(s.id);assert.equal(current.turns.length,2);assert.equal(current.memoryPalace.processedThrough,2);assert.equal(current.rollingSummary.versions.length,1);assert.equal((await h.io.budget.status()).used,7);
 const b=await h.comparison.create(await f.intent(h,s.id,'放弃这一轮'));await h.comparison.wait(s.id,b.plan.id);await h.comparison.discard(s.id,b.plan.id,{operationId:randomUUID(),expectedContextRevision:(await h.comparison.read(s.id,b.plan.id)).context.contextRevision});current=await h.store.read(s.id);assert.equal(current.turns.length,2);assert.equal(current.draft,'放弃这一轮');assert.equal((await h.io.budget.status()).used,9);assert.equal((await h.io.budget.status()).reserved,0);assert.equal((await f.evidence()).calls.length,9);
});

test('revoked comparison cannot adopt a late candidate; original-ID discard releases only unused stages',async t=>{
 const f=await fixture(t),h=f.host.hosts.get('god'),s=await h.store.create(inputFor('god'));await f.grant(s,'rpg.model-comparison');const b=await h.comparison.create(await f.intent(h,s.id,s.characterText)),done=await h.comparison.wait(s.id,b.plan.id);assert.equal(done.status,'awaiting-choice');await f.grant(await h.store.read(s.id),'rpg.model-comparison','disable');await assert.rejects(h.comparison.adopt(s.id,b.plan.id,{operationId:randomUUID(),expectedContextRevision:done.context.contextRevision,candidateId:'a'}));await h.comparison.discard(s.id,b.plan.id,{operationId:randomUUID(),expectedContextRevision:done.context.contextRevision});assert.equal((await h.store.read(s.id)).turns.length,0);assert.equal((await h.io.budget.status()).used,2);assert.equal((await f.evidence()).calls.length,2);
});

test('C2-B six card future HTTP adapter checks both grants and atomically applies the next actual wire',async t=>{
 const f=await fixture(t),records=[];
 for(const card of Object.keys(GENERATION_CARDS)){
  const h=f.host.hosts.get(card),s=await h.store.create(inputFor(card));await f.grant(s,'rpg.model-comparison');
  const b=await h.comparison.create(await f.intent(h,s.id,s.characterText)),done=await h.comparison.wait(s.id,b.plan.id);
  await h.comparison.adopt(s.id,b.plan.id,{operationId:randomUUID(),expectedContextRevision:done.context.contextRevision,candidateId:'a'});
  const route=s.id+'/'+b.plan.id+'/future',status=await h.comparison.handle('GET',route);assert.equal(status.allowed,false);
  for(const id of ['rpg.model-selector','rpg.generation-parameters'])await f.grant(await h.store.read(s.id),id);
  const before=await h.store.read(s.id),v=await h.comparison.handle('GET',route);
  const intent={operationId:randomUUID(),candidateId:'b',...Object.fromEntries(['expectedRevision','expectedModelRevision','expectedParameterRevision','expectedAuthorizationRevision'].map(k=>[k,v[k]]))};
  const receipt=await h.comparison.handle('POST',route,intent);assert.equal(receipt.status,'applied');
  assert.deepEqual(await h.comparison.handle('POST',route,intent),receipt);
  assert.deepEqual(await h.comparison.handle('GET',route+'/'+intent.operationId),receipt);
  const changed=await h.store.read(s.id);await h.store.requireRuntime(changed);
  assert.equal(changed.revision,before.revision+1);assert.equal(changed.modelState.revision,before.modelState.revision+1);
  assert.equal(changed.generationParameters.values.story.wireParameters.temperature,.9);
  assert.deepEqual(changed.rollingSummary,before.rollingSummary);assert.deepEqual(changed.memoryPalace,before.memoryPalace);
  assert.deepEqual(changed.turns,before.turns);
  await assert.rejects(h.comparison.handle('POST',route,{...intent,operationId:randomUUID()}),{code:'COMPARISON_FUTURE_CONFIGURATION_CHANGED'});
  const p=await h.store.parameterStatus(s.id),m=await h.store.memoryTasks.status(s.id);
  await h.store.send(s.id,{operationId:randomUUID(),expectedSessionRevision:changed.revision,expectedMemoryRevision:changed.memoryPalace.revision,expectedContextPolicyRevision:m.effectivePolicy.revision,input:'继续前进',expectedGeneration:p.expected});
  await h.store.memoryTasks.wait(s.id);await h.store.summaryTasks.wait(s.id);
  assert.equal((await h.store.read(s.id)).turns.length,2);assert.equal((await f.evidence()).calls.at(-1).temperature,.9);
  assert.equal((await h.io.budget.status()).used,3);records.push({card,id:s.id,receipt});
 }
 const calls=(await f.evidence()).calls.length;assert.equal(calls,18);
 await f.reopen();for(const {card,id,receipt}of records){const store=f.host.hosts.get(card).store;await store.requireRuntime(await store.read(id));assert.deepEqual(await store.comparisonFutureOperation(id,receipt.intent.operationId),receipt);}
 assert.equal((await f.evidence()).calls.length,calls);
});


// Hold exact I/O boundaries so polling races cannot pass by scheduler luck.
const gate=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
async function configureBackground(f,h,s){
 for(const id of ['rpg.model-comparison','rpg.rolling-summary','rpg.memory-palace'])await f.grant(await h.store.read(s.id),id);
 let current=await h.store.read(s.id);await h.store.configureSummary(s.id,{operationId:randomUUID(),expectedSessionRevision:current.revision,expectedSummaryRevision:current.rollingSummary.revision,timing:'after',selectionId:f.model.selectionId,catalogRevision:f.model.selectionRevision},f.host.plugins);
 current=await h.store.read(s.id);await h.store.memoryTasks.configure(s.id,{operationId:randomUUID(),expectedSessionRevision:current.revision,expectedMemoryRevision:current.memoryPalace.revision,selectionId:f.model.selectionId,catalogRevision:f.model.selectionRevision});
}

test('settled-batch polling never takes the write lock or blocks the next M2/M3 adoption',async t=>{
 const f=await fixture(t),h=f.host.hosts.get('god'),s=await h.store.create(inputFor('god'));await configureBackground(f,h,s);
 const first=await h.comparison.create(await f.intent(h,s.id,s.characterText)),ready=await h.comparison.wait(s.id,first.plan.id);
 assert.equal((await h.comparison.adopt(s.id,first.plan.id,{operationId:randomUUID(),expectedContextRevision:ready.context.contextRevision,candidateId:'a'})).status,'settled');
 const second=await h.comparison.create(await f.intent(h,s.id,'检查门锁，尚未开门。')),done=await h.comparison.wait(s.id,second.plan.id),pick={operationId:randomUUID(),expectedContextRevision:done.context.contextRevision,candidateId:'a'};
 const original=h.store.read.bind(h.store),entered=gate(),release=gate();let armed=true,lockHeld;
 h.store.read=async id=>{const snapshot=await original(id);if(armed){armed=false;lockHeld=h.store.locks.has(id);entered.resolve();await release.promise;}return snapshot;};
 const polling=h.comparison.handle('GET',s.id);await entered.promise;let adopted;
 try{adopted=await h.comparison.adopt(s.id,second.plan.id,pick);}finally{release.resolve();await polling;h.store.read=original;}
 assert.equal(lockHeld,false,'already-settled GET must remain read-only');assert.equal(adopted.status,'settled',adopted.diagnostic);
 const current=await h.store.read(s.id);assert.equal(current.turns.length,2);assert.equal(current.memoryPalace.processedThrough,2);assert.equal(current.rollingSummary.versions.length,1);
 const unchanged=structuredClone(current);await h.comparison.adopt(s.id,second.plan.id,pick);await Promise.all(Array.from({length:8},()=>h.comparison.handle('GET',s.id)));
 assert.deepEqual(await h.store.read(s.id),unchanged);assert.equal((await f.evidence()).calls.length,7);assert.equal((await h.io.budget.status()).reserved,0);
});

test('concurrent first-settlement reads share one write and a failed save remains recoverable',async t=>{
 const f=await fixture(t),h=f.host.hosts.get('god'),s=await h.store.create(inputFor('god'));await f.grant(s,'rpg.model-comparison');
 const b=await h.comparison.create(await f.intent(h,s.id,s.characterText)),done=await h.comparison.wait(s.id,b.plan.id),pick={operationId:randomUUID(),expectedContextRevision:done.context.contextRevision,candidateId:'a'};
 const write=h.store.write.bind(h.store),entered=gate(),release=gate(),seen=gate(),settle=h.comparisonAdapter.comparisonSettled.bind(h.comparisonAdapter);let writes=0,settlements=0;
 h.store.write=async v=>{if(Object.values(v.memoryTasks.operations).some(o=>o.comparison?.plan.id===b.plan.id&&o.status==='complete')){writes++;entered.resolve();await release.promise;}return write(v);};
 h.comparisonAdapter.comparisonSettled=v=>{if(v.plan.id===b.plan.id&&++settlements===2)seen.resolve();return settle(v);};
 const adopting=h.comparison.adopt(s.id,b.plan.id,pick);await entered.promise;
 const polling=h.comparison.read(s.id,b.plan.id);polling.catch(()=>{});await seen.promise;release.resolve();
 const results=await Promise.allSettled([adopting,polling]);h.store.write=write;h.comparisonAdapter.comparisonSettled=settle;
 assert.ok(results.every(r=>r.status==='fulfilled'),JSON.stringify(results.map(r=>r.reason?.code)));assert.equal(writes,1);
 // Simulate a fresh operation whose final status save fails after formal commit.
 const second=await h.comparison.create(await f.intent(h,s.id,'继续')),ready=await h.comparison.wait(s.id,second.plan.id),intent={operationId:randomUUID(),expectedContextRevision:ready.context.contextRevision,candidateId:'b'};
 let fail=true;h.store.write=async v=>{if(fail&&Object.values(v.memoryTasks.operations).some(o=>o.comparison?.plan.id===second.plan.id&&o.status==='complete')){fail=false;throw Object.assign(Error('test-only save failure'),{code:'TEST_SAVE_FAILED'});}return write(v);};
 await assert.rejects(h.comparison.adopt(s.id,second.plan.id,intent),{code:'TEST_SAVE_FAILED'});h.store.write=write;
 await f.reopen();const reopened=f.host.hosts.get('god');await reopened.comparison.reconcile(s.id,second.plan.id);
 assert.equal((await reopened.store.read(s.id)).turns.length,2);assert.equal((await f.evidence()).calls.length,4);assert.equal((await reopened.io.budget.status()).reserved,0);
});

test('original adoption ID resumes after a lock failure and restart without new candidates',async t=>{
 const f=await fixture(t);let h=f.host.hosts.get('god');const s=await h.store.create(inputFor('god'));await configureBackground(f,h,s);
 for(const input of [s.characterText,'检查黄铜钥匙，尚未转动。']){
  const b=await h.comparison.create(await f.intent(h,s.id,input)),done=await h.comparison.wait(s.id,b.plan.id),pick={operationId:randomUUID(),expectedContextRevision:done.context.contextRevision,candidateId:'a'};
  if(input===s.characterText){await h.comparison.adopt(s.id,b.plan.id,pick);continue;}
  h.store.locks.add(s.id);let blocked;try{blocked=await h.comparison.adopt(s.id,b.plan.id,pick);}finally{h.store.locks.delete(s.id);}
  assert.equal(blocked.status,'blocked');assert.equal(blocked.diagnostic,'SESSION_BUSY');assert.ok(blocked.steps.every(v=>v.receipt===null));assert.equal((await f.evidence()).calls.length,5);
  const candidates=structuredClone(blocked.tasks.filter(v=>v.result));await f.reopen();h=f.host.hosts.get('god');
  const recovered=await h.comparison.adopt(s.id,b.plan.id,pick);assert.equal(recovered.status,'settled',recovered.diagnostic);assert.deepEqual(recovered.decision.intent,pick);
  assert.deepEqual(recovered.tasks.filter(v=>candidates.some(c=>c.id===v.id)),candidates);await h.comparison.adopt(s.id,b.plan.id,pick);
 }
 const current=await h.store.read(s.id);assert.equal(current.turns.length,2);assert.equal(current.memoryPalace.processedThrough,2);assert.equal(current.rollingSummary.versions.length,1);
 const calls=(await f.evidence()).calls;assert.equal(calls.length,7);assert.equal((await h.io.budget.status()).used,7);assert.equal((await h.io.budget.status()).reserved,0);
});
