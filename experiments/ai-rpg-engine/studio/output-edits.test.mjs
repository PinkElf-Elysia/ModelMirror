import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {start} from './server.mjs';
import {newSetup} from '../god-card/lib/setup.mjs';
import {parse} from '../plugins/transcript.mjs';
import {editOutput} from './message-tools.mjs';
import {fileURLToPath} from 'node:url';
import {preserveOutputEditRuntime} from './output-edit-compatibility.mjs';
import {sha,canonical} from '../plugins/catalog.mjs';

async function fixture(t,card,features=true,autoSummaryV2=false){
 const root=new URL('../.rpg04-work/output-edit/',import.meta.url);
 await mkdir(root,{recursive:true});const directory=await mkdtemp(join(fileURLToPath(root),'case-'));
 const host=await start({port:0,directory,autoSummaryV2,messageTools:true,transcript:true,memoryPalace:features,godCard:true,godPlugins:features,godBranches:true});
 t.after(async()=>{await host.plugins.close();await new Promise(r=>host.server.close(r));});
 const store=card==='earth'?host.earth:host.god,calls=[];
 if(features){const execute=store.control.execute;store.control.execute=async(task,...args)=>{calls.push(structuredClone(task));return execute(task,...args);};}
 const base='http://127.0.0.1:'+host.server.address().port;
 const api=async(path,body,expected=200)=>{
  const response=await fetch(base+'/rpg-app/'+path,body?{method:'POST',headers:{origin:base,'content-type':'application/json'},body:JSON.stringify(body)}:{});
  const value=await response.json();assert.equal(response.status,expected,JSON.stringify(value));return value;
 };
 const setup=newSetup();setup.user.姓名='离线编辑测试';setup.opening='初临钟楼';
 let s=await api(card+'/api/sessions',card==='god'?{setup,mode:'offline'}:{characterText:'姓名：离线编辑测试',world:'表世界',params:{},mode:'offline'},201);
 const path=card+'/api/sessions/'+s.id;
 async function grant(pluginId,id=s.id){
  let c=await api('api/plugins'),p=c.plugins.find(p=>p.id===pluginId);
  const binding={pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256};
  if(!p.installed)await api('api/plugins/'+pluginId+'/install',{...binding,operationId:randomUUID(),expectedRegistryRevision:c.revision});
  c=await api('api/plugins');const session=await api(card+'/api/sessions/'+id);
  await api(card+'/api/sessions/'+id+'/plugins/'+pluginId+'/enable',{...binding,operationId:randomUUID(),expectedRegistryRevision:c.revision,sessionId:id,expectedSessionRevision:session.revision,permissions:p.permissions});
 }
 async function send(id=s.id,input='离线继续'){
  const p=card+'/api/sessions/'+id,session=await api(p);
  const policy=features?await api(p+'/memory-palace'):null;
  const result=await api(p+'/send',features?{operationId:randomUUID(),expectedSessionRevision:session.revision,expectedMemoryRevision:policy.memoryRevision,expectedContextPolicyRevision:policy.effectivePolicy.revision,input:session.turns.length||card==='earth'?input:session.characterText}:{requestId:randomUUID(),revision:session.revision,input:session.turns.length||card==='earth'?input:session.characterText});
  if(features){await store.memoryTasks.wait(id);await store.summaryTasks.wait(id);return api(p);}
  return result;
 }
 return {directory,host,store,calls,api,base,path,grant,send,s,card};
}
for(const card of ['earth','god'])for(const features of [false,true])test(card+' edit display/export/context without dispatch; preserve receipts and isolated branches ('+features+')',async t=>{
 const f=await fixture(t,card,features);
 let s=await f.send();
 const original=s.turns[0].raw,receipt=structuredClone(s.requests),requestId=s.turns[0].requestId;
 const edit={operationId:randomUUID(),turn:1,revision:s.revision,text:'已修改的钟楼记录\n<details open><summary>状态</summary>同行者平安。</details><script>alert(1)</script>'};
 await f.api(f.path+'/message-tools/edit-output',edit,403);
 await f.grant('rpg.message-tools');await f.grant('rpg.transcript');await f.grant('rpg.branch-save');
 const callCount=f.calls.length;
 const saved=await f.api(f.path+'/message-tools/edit-output',edit);
 assert.equal(f.calls.length,callCount);
 assert.equal(saved.revision,s.revision+1);
 assert.equal((await f.api(f.path+'/message-tools/edit-output',edit)).replayed,true);
 await f.api(f.path+'/message-tools/edit-output',{...edit,text:'另一版本'},409);
 s=await f.api(f.path);assert.equal(s.turns.length,1);assert.equal(s.turns[0].requestId,requestId);assert.equal(s.turns[0].raw,edit.text);
 assert.equal(s.turns[0].outputEdited,true);assert(!s.turns[0].html.includes('<script>'));assert.deepEqual(s.requests,receipt);
 const disk=JSON.parse(await readFile(join(f.directory,card,'sessions',s.id+'.json'),'utf8').catch(async()=>readFile(join(f.directory,card,s.id+'.json'),'utf8')));
 assert.equal(disk.turns[0].raw,original);assert.equal(disk.history[1].content,original);
 const exported=await fetch(f.base+'/rpg-app/'+f.path+'/transcript/export');
 assert.equal(parse(Buffer.from(await exported.arrayBuffer())).turns[0].raw,edit.text);
 await f.api(f.path+'/message-tools/edit-output',{...edit,operationId:randomUUID()},409);
 const branch=await f.api(f.path+'/branches',{operationId:randomUUID(),expectedSessionRevision:s.revision,turn:1,name:'修订分支'},201);
 assert.equal(branch.session.turns[0].raw,edit.text);
 s=await f.send();assert.equal(s.turns.length,2);assert.equal(s.turns[0].raw,edit.text);
 if(features){
  assert.equal(s.turns[1].contextPolicy.outputEditCount,1);
  const disk=await f.store.read(s.id);
  const task=Object.values(disk.summaryTasks.operations).flatMap(o=>o.tasks).findLast(t=>t.purpose==='story');
  assert(task.messages.some(m=>m.role==='assistant'&&m.content===edit.text));
  // Store validation runs during send. A second send reconstructs the prior
  // edited request and detects any accidentally rewritten provider evidence.
  assert.equal((await f.send()).turns.length,3);
 }
 await f.grant('rpg.message-tools',branch.session.id);
 const childPath=card+'/api/sessions/'+branch.session.id;
 const child=await f.api(childPath);
 await f.api(childPath+'/message-tools/edit-output',{operationId:randomUUID(),turn:1,revision:child.revision,text:'分支独有修订'});
 assert.equal((await f.api(f.path)).turns[0].raw,edit.text);
 assert.equal((await f.send(child.id)).turns[0].raw,'分支独有修订');
});

test('runtime translation is exact and does not excuse content or setup tampering',async t=>{
 const f=await fixture(t,'earth',true);
 const session=JSON.parse(await readFile(join(f.directory,'earth',f.s.id+'.json'),'utf8'));
 const baseline=JSON.parse(await readFile(new URL('./output-edit-baseline.json',import.meta.url)));
 const saved=structuredClone(session),d=saved.runtime.descriptor;
 for(const [path,digest] of Object.entries(baseline.artifacts))if(path in d.hostArtifacts)d.hostArtifacts[path]=digest;
 delete d.hostArtifacts['studio/output-edits.mjs'];d.baseRuntimeHash=baseline.baseHashes[3];saved.runtime.hash=sha(canonical(d));
 const current=session.runtime.hash;
 const store={status:s=>s.runtime.hash!==sha(canonical(s.runtime.descriptor))?{compatible:false,code:'RUNTIME_BINDING_INVALID'}:s.runtime.hash===current?{compatible:true}:{compatible:false,code:'RUNTIME_VERSION_MISMATCH'}};
 preserveOutputEditRuntime(store);assert.equal(store.status(saved).compatible,true);
 const tampered=structuredClone(saved);tampered.runtime.descriptor.content.author.system='0'.repeat(64);tampered.runtime.hash=sha(canonical(tampered.runtime.descriptor));
 assert.equal(store.status(tampered).compatible,false);
 saved.runtime.hash='0'.repeat(64);assert.equal(store.status(saved).compatible,false);
});

for(const card of ['earth','god'])test(card+' edits with window, rolling summary and memory keep corrected sources and prior receipts',async t=>{
 const f=await fixture(t,card,true);
 for(const id of ['rpg.message-tools','rpg.history-window','rpg.rolling-summary','rpg.memory-palace'])await f.grant(id);
 let s=await f.store.read(f.s.id);
 const model=(await f.store.control.catalog({sessionId:s.id}))[0];
 await f.store.configureSummary(s.id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedSummaryRevision:s.rollingSummary.revision,timing:'before',selectionId:model.selectionId,catalogRevision:model.selectionRevision},f.host.plugins);
 s=await f.store.read(s.id);
 await f.store.memoryTasks.configure(s.id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedMemoryRevision:s.memoryPalace.revision,selectionId:model.selectionId,catalogRevision:model.selectionRevision});
 await f.send();await f.send();await f.send();
 s=await f.store.read(s.id);
 const summary=structuredClone(s.rollingSummary),memory=structuredClone(s.memoryPalace),turns=structuredClone(s.turns),calls=f.calls.length;
 await f.api(f.path+'/message-tools/edit-output',{operationId:randomUUID(),turn:1,revision:s.revision,text:'修订事实：旧码头没有毁坏'});
 s=await f.store.read(s.id);
 assert.deepEqual(s.rollingSummary,summary);assert.deepEqual(s.memoryPalace,memory);assert.deepEqual(s.turns,turns);assert.equal(f.calls.length,calls);
 await f.send();
 const story=f.calls.findLast(c=>c.purpose==='story');
 assert(story.messages[0].content.includes('修订事实：旧码头没有毁坏'));
 await f.store.requireRuntime(await f.store.read(s.id));
 // Editing a still-recent reply must feed the next summary and raw context.
 s=await f.store.read(s.id);
 await f.api(f.path+'/message-tools/edit-output',{operationId:randomUUID(),turn:3,revision:s.revision,text:'第三轮修订：同行者仍在钟楼'});
 await f.send();
 assert(f.calls.filter(c=>c.purpose==='summary').at(-1).messages.some(m=>m.content.includes('第三轮修订：同行者仍在钟楼')));
 await f.store.requireRuntime(await f.store.read(s.id));
});

test('pending legacy intent, invalid input and revoked commit never save an edit',async()=>{
 const base={id:'session',revision:1,turns:[{requestId:'reply',raw:'原文',rawHash:sha('原文')}],
 history:[{role:'user',content:'输入'},{role:'assistant',content:'原文'}],requests:{}};
 let session=structuredClone(base),writes=0,revoked=false;
 const store={exclusive:async(_id,run)=>run(),read:async()=>structuredClone(session),
 requireRuntime:async()=>{},write:async s=>{writes++;session=structuredClone(s);}};
 const plugins={sessionStatus:async()=>({enabled:true}),invoke:async()=>({}),
 commitResult:async(_ticket,commit)=>{if(revoked)throw Error('PLUGIN_REVOKED');return commit();}};
 const input={operationId:randomUUID(),revision:1,turn:1,text:'修订'};
 session.memoryLegacyInputs={original:{status:'prepared'}};
 await assert.rejects(editOutput(store,plugins,'session',input),/MESSAGE_EDIT_BUSY/);
 session=structuredClone(base);
 await assert.rejects(editOutput(store,plugins,'session',{...input,text:'   '}),/MESSAGE_EDIT_INVALID/);
 await assert.rejects(editOutput(store,plugins,'session',{...input,turn:2}),/MESSAGE_TURN_INVALID/);
 revoked=true;await assert.rejects(editOutput(store,plugins,'session',input),/PLUGIN_REVOKED/);
 assert.equal(writes,0);assert.deepEqual(session,base);
});


test('main v2 chat uses edited sources for explicit small-summary rebuild and preserves edits across history restore',async t=>{
 const f=await fixture(t,'earth',true,true);
 await f.grant('rpg.message-tools');await f.grant('rpg.rolling-summary');
 await f.send();await f.send();
 let s=await f.store.read(f.s.id);const original=structuredClone(s.turns),summary=structuredClone(s.autoSummaryV2),count=f.calls.length;
 for(const [turn,text] of [[1,'修订一：旧码头开放'],[2,'修订二：周岚在钟楼']]){
  await f.api(f.path+'/message-tools/edit-output',{operationId:randomUUID(),turn,revision:s.revision,text});s=await f.store.read(s.id);
 }
 assert.equal(f.calls.length,count);assert.deepEqual(s.autoSummaryV2,summary);assert.deepEqual(s.turns,original);
 let status=await f.api(f.path+'/rolling-summary');
 await f.api(f.path+'/rolling-summary/v2-regenerate',{operationId:randomUUID(),expectedSessionRevision:status.sessionRevision,expectedContextPolicyRevision:status.effectivePolicy.revision,summaryTurn:2});
 await f.store.memoryTasks.wait(s.id);
 const small=f.calls.findLast(c=>c.stageId==='round-summary-small');
 assert(small.messages.some(m=>m.content.includes('修订二：周岚在钟楼')));
 assert.equal(f.calls.filter(c=>c.purpose==='story').length,2);
 await f.send();
 const story=f.calls.findLast(c=>c.purpose==='story');assert(story.messages[0].content.includes('修订一：旧码头开放'));assert(story.messages[0].content.includes('修订二：周岚在钟楼'));
 s=await f.store.read(s.id);await f.store.requireRuntime(s);
 const rewind={action:'rewind',operationId:randomUUID(),expectedRevision:s.revision,turn:2};
 await f.api(f.path+'/lifecycle',rewind);
 let current=await f.store.read(s.id);assert.equal(current.turns.length,1);assert.equal(current.outputEdits.length,1);await f.store.requireRuntime(current);
 assert.equal((await f.api(f.path)).turns[0].raw,'修订一：旧码头开放');
 await f.send(s.id,'重新选择路线');current=await f.store.read(s.id);await f.store.requireRuntime(current);
 await f.api(f.path+'/lifecycle',{action:'restore-version',operationId:randomUUID(),expectedRevision:current.revision,versionId:rewind.operationId});
 current=await f.store.read(s.id);assert.equal(current.outputEdits.length,2);assert.equal(current.turns.length,3);await f.store.requireRuntime(current);
 assert.equal((await f.api(f.path)).turns[1].raw,'修订二：周岚在钟楼');
});

test('exact committed main runtime descriptors remain compatible without admitting prompt or setup drift',async()=>{
 const baseline=JSON.parse(await readFile(new URL('./output-edit-main-baseline.json',import.meta.url)));
 const imports=[['./runtime-binding.mjs','earthRuntime'],['./model-runtime.mjs','modelRuntime'],['./history-runtime.mjs','historyRuntime'],['./summary-runtime.mjs','summaryRuntime'],['./memory-runtime.mjs','memoryRuntime']];
 for(const [i,[path,name]] of imports.entries()){
  const runtime=(await import(path))[name];
  const store={status:s=>s.runtime.hash!==sha(canonical(s.runtime.descriptor))||s.runtime.setupHash!=='frozen-setup'?{compatible:false,code:'RUNTIME_BINDING_INVALID'}:s.runtime.hash===runtime.hash?{compatible:true}:{compatible:false,code:'RUNTIME_VERSION_MISMATCH'}};
  preserveOutputEditRuntime(store);
  const s={runtime:{...structuredClone(baseline.runtimes[i]),setupHash:'frozen-setup'}};
  assert.equal(store.status(s).compatible,true,name);
  const changed=structuredClone(s),author=changed.runtime.descriptor.author||changed.runtime.descriptor.content.author;author.system='tampered';changed.runtime.hash=sha(canonical(changed.runtime.descriptor));
  assert.equal(store.status(changed).compatible,false,name+' prompt drift');
  assert.equal(store.status({...s,runtime:{...s.runtime,setupHash:'tampered'}}).compatible,false,name+' setup drift');
 }
});
