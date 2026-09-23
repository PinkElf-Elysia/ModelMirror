import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {MemorySessionStore} from './memory-session-store.mjs';
import {SummarySessionStore} from './summary-session-store.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {loadReviewedPlugins,MEMORY_PALACE_ID as M,ROLLING_SUMMARY_ID as R,HISTORY_WINDOW_ID as H,MODEL_SELECTOR_ID as P,sha,canonical} from '../plugins/catalog.mjs';
import {createMemoryTransport} from './memory-task-provider.mjs';
import {MEMORY_INSTRUCTION,memoryContextPolicy} from './memory-task-state.mjs';
import {SUMMARY_INSTRUCTION} from './summary-state.mjs';
import {COMPRESSION_INSTRUCTION} from './summary-compression.mjs';
import {parametersFor} from './model-parameters.mjs';
import {assemble,activeSystem} from '../card-replica/lib/assembly.mjs';
import {requireMemoryEvidence} from './memory-assembly.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/memory-palace-b3/',import.meta.url));await mkdir(root,{recursive:true});
const models=['google/gemini-3.8-flash','openai/gpt-5.6-luna'].map((model,i)=>({model,name:model,selectionId:'model-'+i,selectionRevision:'v1',available:true,parameters:parametersFor(model)}));
const kind=b=>b.messages[0].content===MEMORY_INSTRUCTION?'memory':b.messages[0].content===SUMMARY_INSTRUCTION?'summary':b.messages[0].content===COMPRESSION_INSTRUCTION?'compression':'story';
function response(b,raw){
 raw??=kind(b)==='memory'?'{"operations":[]}':kind(b)==='summary'?'早期事实合成摘要。':kind(b)==='compression'?'合成压缩。':'合成剧情原文\n<details>卡内记忆保持原样</details>\n';
 const model=models.find(m=>m.selectionId===b.selectionId).model,sse='data: '+JSON.stringify({choices:[{delta:{content:raw},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n';
 return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:b.sessionId,requestId:b.requestId,selectionId:b.selectionId,selectionRevision:b.selectionRevision,requestedModel:model,actualModel:model,parameters:b.parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(b)),rawHash:sha(raw),sseHash:sha(sse)}});
}
const fields={name:'旧码头',keywords:['旧码头'],match:'any',roles:['user','assistant'],content:'约好在旧码头再见。'};
function updateLibrary(b){if(kind(b)!=='memory')return response(b);const source=JSON.parse(b.messages[1].content),e=source.entries.find(e=>!e.protected),last=source.turns.at(-1).number;
 const op=e?{type:'update',id:e.id,baseVersion:e.version,...fields,content:'旧码头事件至第'+last+'回合。',sourceTurns:source.turns.map(t=>t.number)}:{type:'create',...fields,sourceTurns:source.turns.map(t=>t.number)};
 return response(b,JSON.stringify({operations:[op]}));
}
async function fixture(t,{limit=60,onCall=response}={}){
 const directory=await mkdtemp(join(root,'case-')),wires=[],store=new MemorySessionStore(join(directory,'sessions'),()=>assert.fail('uncontrolled generator'));await store.init();
 const transport=await createMemoryTransport({directory:join(directory,'dispatches'),limit,enabled:true,baseURL:'http://127.0.0.1:1/',serviceToken:'synthetic-memory-test-token-not-a-real-credential',fetcher:async(url,init)=>{if(init.method==='GET')return Response.json({models});const b=JSON.parse(init.body);wires.push(b);await writeFile(join(directory,'wires.json'),JSON.stringify(wires,null,2));return onCall(b);}});
 const plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:id=>store.pluginSession(id),loadCatalog:()=>loadReviewedPlugins({rollingSummary:true,memoryPalace:true})});t.after(()=>plugins.close());store.connectMemory(plugins,transport);
 const f={directory,wires,store,plugins,transport,
  async change(action,id,pluginId=M){const c=await plugins.catalog(),p=c.plugins.find(p=>p.id===pluginId);return plugins.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(id?{sessionId:id,expectedSessionRevision:(await store.read(id)).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},
  async enable(id,p=M){if(!(await plugins.catalog()).plugins.find(x=>x.id===p).installed)await f.change('install',null,p);await f.change('enable',id,p);},
  async create({memory=false,summary=false,timing='before',history=false,mode='real'}={}){const s=await store.create({characterText:'姓名:合成角色\n虚构测试角色，仅用于离线请求验证。',world:'表世界',mode});await f.enable(s.id,P);await f.select(s.id,0);if(history){await f.enable(s.id,H);await f.window(s.id,1);}if(summary){await f.enable(s.id,R);await f.summary(s.id,timing);}if(memory){await f.enable(s.id);await f.configure(s.id,1);}return store.read(s.id);},
  async select(id,index){const s=await store.read(id);return store.select(id,{operationId:randomUUID(),expectedSessionRevision:s.revision,selectionRevision:s.modelState.revision,selectionId:'model-'+index,catalogRevision:'v1'});},
  async configure(id,index){const s=await store.read(id);return store.memoryTasks.configure(id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedMemoryRevision:s.memoryPalace.revision,selectionId:'model-'+index,catalogRevision:'v1'});},
  async summary(id,timing){const s=await store.read(id);return store.configureSummary(id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedSummaryRevision:s.rollingSummary.revision,timing,selectionId:'model-0',catalogRevision:'v1'},plugins);},
  async window(id,window){const s=await store.read(id);return store.saveHistoryWindow(id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedConfigRevision:s.historyWindow.revision,turns:window,includeInitialCharacter:false},plugins);},
  async input(id,text='继续合成经历'){const x=await store.memoryTasks.status(id);return {operationId:randomUUID(),expectedSessionRevision:x.sessionRevision,expectedMemoryRevision:x.memoryRevision,expectedContextPolicyRevision:x.effectivePolicy.revision,input:text};},
  async send(id,text){const x=await f.input(id,text),front=await store.send(id,x);await store.memoryTasks.wait(id);await store.summaryTasks.wait(id);const s=await store.read(id);await store.requireRuntime(s);return {s,x,front};},
  async branch(id,turn,name='合成分支'){await f.enable(id,'rpg.branch-save');const s=await store.read(id),x={operationId:randomUUID(),expectedSessionRevision:s.revision,turn,name};const result=await store.createBranch(id,x);await store.requireRuntime(result.session);return {...result,x};}
 };return f;
}

test('new runtime, default-off M3 and zero-plugin messages exactly match existing assembly',async t=>{
 const f=await fixture(t),s=await f.store.create({characterText:'姓名:无插件合成角色',world:'表世界',mode:'real'});assert.equal((await f.plugins.catalog()).plugins.some(p=>p.installed),false);assert.equal(s.runtime.format,5);assert.equal((await f.plugins.memoryAuthorization(s.id)).enabled,false);
 let previous=s;for(const input of ['开始虚构生活','第二次输入','第三次输入']){const {s:next,x}=await f.send(s.id,input),wire=f.wires.at(-1);assert.deepEqual(wire.messages,assemble({...s,input,history:previous.history}).messages);assert.equal(next.turns.at(-1).memoryPolicy.enabled,false);assert.equal(next.turns.at(-1).memorySettlement.snapshot.library.processedThrough,0);await f.store.send(s.id,x);assert.equal(f.wires.length,next.turns.length);previous=next;}
 assert.equal((await f.transport.status()).used,3);assert.equal(previous.history.length,6);
});

test('M3 selects independent memory model; actual source and story messages preserve raw text',async t=>{
 const f=await fixture(t,{onCall:updateLibrary}),s=await f.create({memory:true});
 await f.send(s.id,'去旧码头');const {s:end}=await f.send(s.id,'旧码头见');
 assert.deepEqual(f.wires.map(kind),['story','memory','story','memory']);assert.equal(f.wires[1].selectionId,'model-1');assert.deepEqual(f.wires[1].parameters,{max_tokens:16384});
 assert.deepEqual(JSON.parse(f.wires[3].messages[1].content).turns,[{number:2,user:end.history[2].content,assistant:end.history[3].content}]);
 assert.equal(f.wires[2].messages.filter(m=>m.role==='system').length,1);assert.ok(f.wires[2].messages[0].content.startsWith(activeSystem+'\n\n【记忆宫殿'));assert.equal(f.wires[2].messages[0].content.slice(activeSystem.length).includes('memory-'),false);
 assert.deepEqual(f.wires[2].messages.slice(1,-1),end.history.slice(0,2));assert.equal(f.wires[2].messages.at(-1).content,assemble({...s,input:'旧码头见',history:end.history.slice(0,2)}).current);
 assert.equal(end.memoryPalace.processedThrough,2);assert.notEqual(end.turns[1].memoryPolicy.usedSnapshot.hash,end.turns[1].memorySettlement.snapshot.hash);requireMemoryEvidence(end);
});

test('M1 window and M3 recall use distinct scopes; disabling restores original policies',async t=>{
 const f=await fixture(t,{onCall:updateLibrary}),s=await f.create({memory:true,history:true});
 for(const input of ['去旧码头','中间经历','新的经历'])await f.send(s.id,input);
 const story=f.wires.filter(b=>kind(b)==='story')[2],disk=await f.store.read(s.id);assert.equal(story.messages.length,4);assert.deepEqual(story.messages.slice(1,3),disk.history.slice(2,4));assert.ok(story.messages[0].content.includes('旧码头'));assert.equal(story.messages.at(-1).content.includes(s.characterText),false);
 await f.change('disable',s.id);await f.send(s.id,'关闭记忆后');assert.equal(f.wires.at(-1).messages[0].content,activeSystem);assert.equal(f.wires.at(-1).messages.length,4);
 await f.change('disable',s.id,H);await f.send(s.id,'完整历史');assert.equal(f.wires.at(-1).messages.length,10);assert.equal((await f.store.read(s.id)).history.length,10);
});

test('M2+M3 single system ordered data, M1 override, controlled update order and no summary as memory source',async t=>{
 const f=await fixture(t,{onCall:updateLibrary}),s=await f.create({memory:true,summary:true,history:true,timing:'after'});
 for(const input of ['旧码头开始','之后','再次到旧码头'])await f.send(s.id,input);
 const stories=f.wires.filter(b=>kind(b)==='story'),last=stories[2],disk=await f.store.read(s.id);assert.equal(last.messages.length,4);const system=last.messages[0].content;assert.ok(system.startsWith(activeSystem));assert.ok(system.indexOf('历史摘要')<system.indexOf('记忆宫殿·历史资料'));assert.equal(last.messages.filter(m=>m.role==='system').length,1);
 assert.deepEqual(f.wires.map(kind),['story','memory','story','summary','memory','story','summary','memory']);assert.equal(JSON.parse(f.wires.at(-1).messages[1].content).turns[0].assistant,disk.history[5].content);assert.equal(disk.history.length,6);
 await f.change('disable',s.id,R);await f.send(s.id,'旧码头再见');assert.equal(f.wires.at(-2).messages[0].content.includes('【历史摘要'),false);assert.equal(f.wires.at(-2).messages.length,4);
});

test('branch freezes used and settled memory; parent future edits and model changes never leak',async t=>{
 const f=await fixture(t,{onCall:updateLibrary}),s=await f.create({memory:true,history:true});
 await f.send(s.id,'旧码头开始');await f.configure(s.id,0);await f.send(s.id,'第二步旧码头');await f.send(s.id,'父路线的未来');
 const parent=await f.store.read(s.id),e=parent.memoryPalace.entries[0];await f.store.memoryEdits.save(s.id,{operationId:randomUUID(),expectedSessionRevision:parent.revision,expectedMemoryRevision:parent.memoryPalace.revision,change:{action:'update',id:e.id,baseVersion:e.version,value:{...e.value,content:'父路线未来的人工改写'}}});
 await f.select(s.id,1);const now=await f.store.read(s.id),used=(await f.transport.status()).used,{session:child,x}=await f.branch(s.id,1);
 assert.deepEqual(child.history,parent.history.slice(0,2));assert.deepEqual(child.memoryPalace,parent.turns[0].memorySettlement.snapshot.library);assert.equal(child.memoryPalace.processedThrough,1);assert.equal(child.memoryTasks.config.model.selectionId,'model-1');assert.deepEqual(child.memoryTasks.operations,{});assert.deepEqual(child.requests,{});assert.equal(child.summaryTasks,undefined);assert.equal(child.memoryLegacyInputs,undefined);assert.equal((await f.plugins.memoryAuthorization(child.id)).enabled,false);assert.equal((await f.transport.status()).used,used);
 assert.equal((await f.store.createBranch(s.id,x)).session.id,child.id);assert.equal((await f.store.createBranch(s.id,x)).replayed,true);
 await f.send(child.id,'分支继续');assert.equal(f.wires.at(-1).messages[0].content,activeSystem);assert.equal(f.wires.at(-1).messages.length,4);assert.deepEqual((await f.store.read(s.id)).history,now.history);
 await f.enable(child.id);await f.send(child.id,'旧码头继续');const last=f.wires.at(-2);assert.ok(last.messages[0].content.includes('约好在旧码头再见'));assert.equal(last.messages[0].content.includes('父路线未来的人工改写'),false);assert.equal(f.wires.at(-1).selectionId,'model-1');assert.deepEqual(JSON.parse(f.wires.at(-1).messages[1].content).turns.map(t=>t.number),[2,3]);
 const reopened=new MemorySessionStore(f.store.directory,()=>assert.fail());reopened.control=f.transport;await reopened.init();const restored=await reopened.read(child.id);await reopened.requireRuntime(restored);assert.equal(restored.turns.length,3);assert.deepEqual(restored.turns[0],parent.turns[0]);assert.equal((await f.transport.status()).used,used+3);
});

test('controlled model switch leaves memory/window/budget intact and uses new story model only',async t=>{
 const f=await fixture(t,{onCall:updateLibrary}),s=await f.create({memory:true,history:true});await f.send(s.id,'旧码头');const before=await f.store.read(s.id),used=(await f.transport.status()).used;
 await f.select(s.id,1);const after=await f.store.read(s.id);assert.deepEqual(after.memoryPalace,before.memoryPalace);assert.deepEqual(after.historyWindow,before.historyWindow);assert.deepEqual(after.memoryTasks.config,before.memoryTasks.config);assert.equal((await f.transport.status()).used,used);
 await f.send(s.id,'旧码头再见');assert.equal(f.wires.at(-2).selectionId,'model-1');assert.deepEqual(f.wires.at(-2).parameters,{max_tokens:16384});assert.equal(f.wires.at(-1).selectionId,'model-1');
});

test('old M2 sessions stay readable and cannot authorize M3; runtime and receipt tampering fail',async t=>{
 const f=await fixture(t),oldStore=new SummarySessionStore(join(f.directory,'old'),()=>assert.fail());await oldStore.init();const old=await oldStore.create({characterText:'姓名:旧合成角色',world:'表世界',mode:'offline'});await f.store.write(old);assert.equal((await f.store.read(old.id)).runtime.format,4);assert.equal((await f.store.pluginSession(old.id)).memoryPalaceCompatible,undefined);await assert.rejects(f.enable(old.id),/MEMORY.*INCOMPATIBLE/);
 const s=await f.create({memory:true});await f.send(s.id,'开始');const disk=await f.store.read(s.id),bad=structuredClone(disk);bad.runtime.descriptor.memoryPolicy='wrong';await assert.rejects(f.store.requireRuntime(bad),/RUNTIME_BINDING_INVALID/);
 const broken=structuredClone(disk);broken.turns[0].memorySettlement.snapshot.library.processedThrough=99;assert.throws(()=>requireMemoryEvidence(broken),/MEMORY_SETTLEMENT_INVALID|MEMORY_SNAPSHOT_INVALID/);
 const receipt=structuredClone(disk);receipt.requests[receipt.turns[0].requestId].assemblyHash=sha('wrong');assert.throws(()=>requireMemoryEvidence(receipt),/MEMORY_TURN_BINDING_INVALID/);
});

test('offline sessions cannot trigger memory or summary model updates on a live transport',async t=>{
 const f=await fixture(t),s=await f.create({memory:true,summary:true,mode:'offline'}),input=await f.input(s.id);await assert.rejects(f.store.send(s.id,input),/OFFLINE_TRANSPORT_REQUIRED/);const {input:ignored,...update}=input;await assert.rejects(f.store.memoryTasks.update(s.id,update),/OFFLINE_TRANSPORT_REQUIRED/);assert.equal(f.wires.length,0);assert.equal((await f.transport.status()).used,0);
});

const latch=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
const branchInput=s=>({operationId:randomUUID(),expectedSessionRevision:s.revision,turn:1,name:'边界测试路线'});
test('background story visible, common lock blocks branches/edits and next send waits for memory',async t=>{
 const entered=latch(),release=latch();let count=0;const f=await fixture(t,{onCall:async b=>{if(kind(b)==='memory'&&++count===1){entered.resolve();await release.promise;}return updateLibrary(b);}}),s=await f.create({memory:true});await f.enable(s.id,'rpg.branch-save');t.after(()=>release.resolve());
 const input=await f.input(s.id,'旧码头');const front=await f.store.send(s.id,input);assert.equal(front.background,true);await entered.promise;const during=await f.store.read(s.id);assert.equal(during.turns.length,1);assert.equal(during.turns[0].memorySettlement,undefined);assert.equal((await f.store.memoryTasks.status(s.id)).effectivePolicy.ready,false);
 await assert.rejects(f.store.createBranch(s.id,branchInput(during)),/SESSION_BUSY/);await assert.rejects(f.configure(s.id,0),/SESSION_BUSY/);
 const pending=f.store.send(s.id,await f.input(s.id,'旧码头下一步'));await new Promise(r=>setTimeout(r,20));assert.equal(f.wires.length,2);release.resolve();await pending;await f.store.memoryTasks.wait(s.id);const end=await f.store.read(s.id);await f.store.requireRuntime(end);assert.equal(end.turns.length,2);assert.equal(end.memoryPalace.processedThrough,2);assert.equal(f.wires.length,4);assert.ok(end.turns[0].memorySettlement);
});

test('in-flight revocation discards memory and seals old snapshot, retaining dispatched receipt',async t=>{
 const entered=latch(),release=latch(),f=await fixture(t,{onCall:async b=>{if(kind(b)==='memory'){entered.resolve();await release.promise;}return updateLibrary(b);}}),s=await f.create({memory:true});t.after(()=>release.resolve());await f.enable(s.id,'rpg.branch-save');
 await f.store.send(s.id,await f.input(s.id,'旧码头'));await entered.promise;await f.change('disable',s.id);release.resolve();const op=await f.store.memoryTasks.wait(s.id),end=await f.store.read(s.id);assert.equal(op.status,'revoked');await f.store.requireRuntime(end);assert.equal(end.turns.length,1);assert.equal(end.memoryPalace.entries.length,0);assert.equal(end.turns[0].memorySettlement.status,'revoked');assert.equal((await f.transport.status()).used,2);
 const branch=await f.store.createBranch(s.id,branchInput(end));assert.equal(branch.session.memoryPalace.processedThrough,0);assert.equal(branch.session.turns[0].raw,end.turns[0].raw);
});

test('unknown background survives restart, blocks new IDs and branching even after disable',async t=>{
 const f=await fixture(t,{onCall:b=>{if(kind(b)==='memory')throw Error('synthetic connection lost');return response(b);}}),s=await f.create({memory:true});await f.enable(s.id,'rpg.branch-save');
 const x=await f.input(s.id);await f.store.send(s.id,x);await f.store.memoryTasks.wait(s.id);const end=await f.store.read(s.id);assert.equal(end.memoryTasks.operations[x.operationId].status,'unknown');await f.change('disable',s.id);
 await assert.rejects(f.store.send(s.id,await f.input(s.id)),/MEMORY_TASK_UNCONFIRMED/);await assert.rejects(f.store.createBranch(s.id,branchInput(end)),/MEMORY_TASK_UNCONFIRMED/);
 const restarted=new MemorySessionStore(f.store.directory,()=>assert.fail());await restarted.init();restarted.connectMemory(f.plugins,f.transport);await restarted.requireRuntime(await restarted.read(s.id));const recovered=await restarted.memoryTasks.recover(s.id,x.operationId);assert.equal(recovered.status,'unknown');assert.equal(f.wires.length,2);assert.equal((await f.transport.status()).used,2);
});

test('invalid proposal keeps story and old library; explicit update does not rewrite sealed branch point',async t=>{
 let malformed=true;const f=await fixture(t,{onCall:b=>kind(b)==='memory'&&malformed?response(b,'{"operations":[{"invalid":true}]}'):updateLibrary(b)}),s=await f.create({memory:true});await f.send(s.id,'旧码头');const failed=await f.store.read(s.id);assert.equal(failed.turns[0].memorySettlement.status,'failed');assert.equal(failed.memoryPalace.processedThrough,0);await assert.rejects(f.store.send(s.id,await f.input(s.id)),/REQUIRES_EXPLICIT_ACTION/);
 malformed=false;const {input:ignored,...x}=await f.input(s.id);await f.store.memoryTasks.update(s.id,x);assert.equal((await f.store.read(s.id)).memoryPalace.processedThrough,1);const {session:branch}=await f.branch(s.id,1);assert.equal(branch.memoryPalace.processedThrough,0);assert.deepEqual(branch.memoryPalace.entries,[]);assert.equal(f.wires.length,3);
});

test('legacy intent acknowledgement loss is recoverable only with same ID; no silent new send',async t=>{
 const f=await fixture(t),s=await f.create(),write=f.store.write.bind(f.store);await f.change('install',null,M);const input=await f.input(s.id);let once=true;
 f.store.write=async next=>{await write(next);if(once&&next.memoryLegacyInputs?.[input.operationId]){once=false;throw Error('synthetic bridge acknowledgement lost');}};
 await assert.rejects(f.store.send(s.id,input),/acknowledgement lost/);f.store.write=write;assert.equal(f.wires.length,0);assert.equal((await f.store.pluginSession(s.id)).pending,true);await assert.rejects(f.store.send(s.id,await f.input(s.id)),/MEMORY_TASK_UNCONFIRMED/);await assert.rejects(f.enable(s.id),/PLUGIN_SESSION_BUSY/);
 await f.store.send(s.id,input);assert.equal(f.wires.length,1);await f.store.send(s.id,input);assert.equal(f.wires.length,1);await f.store.requireRuntime(await f.store.read(s.id));
});

test('model changes after prepared send are rejected without dispatch; same budget spans all branches',async t=>{
 const f=await fixture(t,{limit:2}),s=await f.create({memory:true});const stale=await f.input(s.id);await f.select(s.id,1);await assert.rejects(f.store.send(s.id,stale),/REVISION_CONFLICT/);assert.equal(f.wires.length,0);
 await f.send(s.id,'first');const {session:child}=await f.branch(s.id,1);const sent=await f.store.send(child.id,await f.input(child.id));assert.equal(sent.status,'failed');assert.equal(sent.error,'BUDGET_EXHAUSTED');assert.equal((await f.store.read(child.id)).turns.length,1);assert.equal(f.wires.length,2);assert.equal((await f.transport.status()).used,2);
});

test('actual recall caps complete entries; names/body only, raw input roles, no recursive activation',async t=>{
 const f=await fixture(t),s=await f.create({memory:true,history:true});await f.send(s.id,'无关键词经历');
 async function add(value){const current=await f.store.read(s.id);await f.store.memoryEdits.save(s.id,{operationId:randomUUID(),expectedSessionRevision:current.revision,expectedMemoryRevision:current.memoryPalace.revision,change:{action:'create',value:{...fields,enabled:true,...value}}});}
 for(let i=0;i<6;i++)await add({name:'同名地点 '+i,keywords:['RECALL'],content:'资'.repeat(1600)+'递归词',roles:['user']});
 await add({name:'不得递归',keywords:['递归词'],content:'不应因注入内容命中'});await add({name:'开局资料不扫描',keywords:['合成角色'],content:'不应因角色快照命中',roles:['user']});await add({name:'角色过滤',keywords:['recall'],roles:['assistant'],content:'当前用户输入不应匹配 AI 限定条目'});
 await f.send(s.id,'recall');const story=f.wires.at(-2),disk=await f.store.read(s.id),record=disk.turns.at(-1).memoryPolicy.recall;assert.equal(record.results.length,6);assert.equal(record.results.filter(x=>x.selected).length,4);assert.ok(record.bodyCharacters<=8000);assert.ok(record.results.filter(x=>!x.selected).every(x=>x.reason==='body-limit'));
 const data=story.messages[0].content.slice(activeSystem.length);assert.equal(data.includes('不得递归'),false);assert.equal(data.includes('开局资料不扫描'),false);assert.equal(data.includes('角色过滤'),false);assert.equal(data.includes('sourceTurns'),false);assert.equal(disk.memoryPalace.entries.length,9);
});

test('unstarted intent with changed installation is closed by original-ID recovery, never dispatched',async t=>{
 const f=await fixture(t),s=await f.create(),write=f.store.write.bind(f.store),input=await f.input(s.id);let once=true;
 f.store.write=async next=>{await write(next);if(once&&next.memoryLegacyInputs?.[input.operationId]){once=false;throw Error('synthetic intent crash');}};
 await assert.rejects(f.store.send(s.id,input),/intent crash/);f.store.write=write;await f.change('install',null,M);
 await assert.rejects(f.store.send(s.id,input),/MEMORY_AUTHORIZATION_CHANGED/);assert.equal((await f.store.recoverMemoryOperation(s.id,input.operationId)).status,'not-applied');assert.equal((await f.store.send(s.id,input)).status,'not-applied');assert.equal(f.wires.length,0);assert.equal((await f.store.pluginSession(s.id)).pending,false);
 await f.send(s.id,'新的主动发送');assert.equal(f.wires.length,1);
});

test('terminal M3 send remains idempotent after disable/uninstall; receipt data cannot masquerade as new request',async t=>{
 const f=await fixture(t),s=await f.create({memory:true}),{x}=await f.send(s.id,'开始');await f.change('disable',s.id);assert.equal((await f.store.send(s.id,x)).replayed,true);await f.change('uninstall',null,M);assert.equal((await f.store.send(s.id,x)).replayed,true);assert.equal((await f.store.recoverMemoryOperation(s.id,x.operationId)).status,'complete');assert.equal(f.wires.length,2);
 const disk=await f.store.read(s.id),bad=structuredClone(disk),turn=bad.turns[0];turn.memoryPolicy.requestHash=sha('not-the-real-wire');bad.requests[turn.requestId].memoryPolicy=structuredClone(turn.memoryPolicy);bad.requests[turn.requestId].assemblyHash=turn.memoryPolicy.requestHash;assert.throws(()=>requireMemoryEvidence(bad),/MEMORY_TURN_BINDING_INVALID/);
});

test('actual host reserves six stages for M2 catch-up/compression and M3, then seals independent branch snapshots',async t=>{
 const f=await fixture(t,{limit:8,onCall:b=>kind(b)==='summary'?response(b,'摘要'.repeat(5001)):updateLibrary(b)}),s=await f.create();await f.send(s.id,'旧码头第一段');await f.send(s.id,'第二段');
 await f.enable(s.id,R);await f.summary(s.id,'after');await f.enable(s.id);await f.configure(s.id,1);assert.equal((await f.store.memoryTasks.status(s.id)).maxCalls,6);
 const {s:end}=await f.send(s.id,'旧码头第三段');assert.deepEqual(f.wires.slice(2).map(kind),['summary','compression','story','summary','compression','memory']);assert.equal((await f.transport.status()).used,8);
 const last=end.turns.at(-1);assert.equal(last.contextPolicy.coveredThrough,1);assert.deepEqual(last.contextPolicy.selectedTurns,[2]);assert.equal(last.memoryPolicy.usedSnapshot.library.processedThrough,0);assert.equal(last.memorySettlement.snapshot.library.processedThrough,3);
 const allSource=JSON.parse(f.wires.at(-1).messages[1].content);assert.deepEqual(allSource.turns.map(t=>t.number),[1,2,3]);assert.deepEqual(allSource.turns.map(t=>t.assistant),end.history.filter(m=>m.role==='assistant').map(m=>m.content));
 const {session:child}=await f.branch(s.id,3);assert.deepEqual(child.rollingSummary,last.contextPolicy.summarySnapshot);assert.deepEqual(child.memoryPalace,last.memorySettlement.snapshot.library);assert.equal((await f.plugins.summaryAuthorization(child.id)).enabled,false);assert.equal(child.rollingSummary.versions.find(v=>v.id===child.rollingSummary.activeVersionId).coveredThrough,1);assert.deepEqual(child.memoryTasks.operations,{});
});

test('M2 without M3 keeps old before/after call order and full original memory source after enabling M3',async t=>{
 for(const timing of ['before','after']){
  const f=await fixture(t),s=await f.create({summary:true,timing});for(const input of ['one','two','three'])await f.send(s.id,input);
  assert.deepEqual(f.wires.map(kind),timing==='before'?['story','story','summary','story']:['story','story','summary','story','summary']);
  const story=f.wires.filter(b=>kind(b)==='story').at(-1);assert.equal(story.messages.length,4);assert.equal(story.messages[0].content.includes('【记忆宫殿·历史资料】'),false);
  await f.enable(s.id);await f.configure(s.id,0);await f.send(s.id,'four');const memory=f.wires.filter(b=>kind(b)==='memory');assert.equal(memory.length,1);assert.deepEqual(JSON.parse(memory[0].messages[1].content).turns.map(t=>t.number),[1,2,3,4]);
 }
});
