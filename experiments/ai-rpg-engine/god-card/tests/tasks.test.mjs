import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,readdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {VersionedSessionStore} from '../../studio/versioned-store.mjs';
import {atomicJson,completeHistory} from '../../studio/branch-archive.mjs';
import {createPluginService} from '../../plugins/host.mjs';
import {loadReviewedPlugins,MEMORY_PALACE_ID as M,ROLLING_SUMMARY_ID as R,canonical,sha} from '../../plugins/catalog.mjs';
import {initialHistoryState} from '../../studio/history-state.mjs';
import {initialModelState} from '../../studio/model-runtime.mjs';
import {initialSummaryState,sealSummary,SUMMARY_INSTRUCTION} from '../../studio/summary-state.mjs';
import {initialMemoryState,requireMemoryState} from '../../studio/memory-state.mjs';
import {initialMemoryTasks,memoryTaskBlock,MEMORY_INSTRUCTION} from '../../studio/memory-task-state.mjs';
import {createMemoryTasks} from '../../studio/memory-tasks.mjs';
import {createSummaryTasks} from '../../studio/summary-tasks.mjs';
import {createMemoryTransport} from '../../studio/memory-task-provider.mjs';
import {createMemoryBudget} from '../../studio/memory-budget.mjs';
import {createCardState} from '../../studio/card-state.mjs';
import {createModelPolicy} from '../../studio/card-model-policy.mjs';
import {createCardContext} from '../../studio/card-context.mjs';
import {godAdapter,godRuntime} from '../lib/adapter.mjs';
import {newSetup} from '../lib/setup.mjs';
import {loadFrozen} from '../lib/assembly.mjs';

const root=fileURLToPath(new URL('../../.rpg04-work/god-card/b3-tasks/',import.meta.url));await mkdir(root,{recursive:true});
const models=['story','summary','memory'].map((name,i)=>({model:'synthetic/god-'+name,name,selectionId:'god-'+name,selectionRevision:'v1',available:true,parameters:{max_tokens:2048+i*1024,temperature:.3+i*.1}}));
const policy=createModelPolicy({cardId:'god',parametersFor:model=>models.find(m=>m.model===model)?.parameters});
const state=createCardState(policy),context=createCardContext({cardId:'god',assemble:godAdapter.assemble,state});
const latch=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
function response(body){
 const raw=body.messages[0].content===MEMORY_INSTRUCTION?JSON.stringify({operations:JSON.parse(body.messages[1].content).entries.length?[]:[{type:'create',name:'钟楼',keywords:['钟楼'],match:'any',roles:['user','assistant'],content:'修复钟楼的承诺尚未兑现。',sourceTurns:[1]}]}):body.messages[0].content===SUMMARY_INSTRUCTION?'神明承诺修复钟楼，目前尚未完成。':'钟楼仍然静默；修复承诺尚未兑现。';
 const model=models.find(m=>m.selectionId===body.selectionId).model,sse='data: '+JSON.stringify({choices:[{delta:{content:raw},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n';
 return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:body.sessionId,requestId:body.requestId,selectionId:body.selectionId,selectionRevision:body.selectionRevision,requestedModel:model,actualModel:model,parameters:body.parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(body)),rawHash:sha(raw),sseHash:sha(sse)}});
}
// Synthetic compatibility declaration only. The production God plugin catalog
// remains unchanged. Disk, locks, plugin grants, coordinators and transport are real.
class FixtureStore extends VersionedSessionStore {
 async write(s){return atomicJson(this.path(s.id),s);}
 async requireRuntime(s){assert.equal(s.runtime.hash,godRuntime.hash);state.guard(s);completeHistory(s);state.requireModelState(s);state.summary.requireSummaryState(s);requireMemoryState(s);state.memory.requireMemoryTasks(s);}
 async requireSummarySession(s){await this.requireRuntime(s);return state.summary.requireSummaryState(s);}
 async pluginSession(id){const s=await this.read(id);return {id,cardId:'god',revision:s.revision,completedTurns:s.turns.length,resourceHash:s.runtime.hash,memoryPalaceCompatible:true,rollingSummaryCompatible:true,historyWindowCompatible:true,busy:this.running.has(id),pending:memoryTaskBlock(s)||!!s.memoryPalace.pending};}
}
async function fixture(t,{limit=30,onCall=null}={}){
 const directory=await mkdtemp(join(root,'case-')),wires=[],store=new FixtureStore(join(directory,'sessions'),()=>assert.fail('legacy provider forbidden'));await store.init();
 const options={directory:join(directory,'dispatches'),cardId:'god',modelPolicy:policy,limit,enabled:true,baseURL:'http://127.0.0.1:1/',serviceToken:'synthetic-god-token-not-a-real-credential',fetcher:async(url,init)=>{if(init.method==='GET')return Response.json({models});const body=JSON.parse(init.body);wires.push(body);return onCall?onCall(body):response(body);}};
 const transport=await createMemoryTransport(options),plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:id=>store.pluginSession(id),loadCatalog:async()=>(await loadReviewedPlugins({rollingSummary:true,memoryPalace:true})).map(e=>{const manifest={...e.manifest,compatibleCards:['god']};return {...e,manifest,manifestSha256:sha(canonical(manifest))};})});t.after(()=>plugins.close());
 const hooks={store,plugins,transport,cardState:state,buildStory:context.memory,commitStory:context.commitMemory,settleStory:context.settleMemory};
 const tasks=createMemoryTasks(hooks);
 const summaryTransport={...transport,budget:{...transport.budget,reserve:(group,id,purposes)=>transport.budget.reserve(group,id,purposes.map(purpose=>({stageId:purpose,purpose})))}};
 const summaryTasks=createSummaryTasks({store,plugins,transport:summaryTransport,cardState:state,buildStory:(s,i,p)=>({...context.summary(s,i,p),input:i}),commitStory:context.commitSummary});
 const f={directory,store,plugins,tasks,summaryTasks,transport,wires,hooks,
  async change(action,id,pluginId=M){const c=await plugins.catalog(),p=c.plugins.find(p=>p.id===pluginId);return plugins.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(id?{sessionId:id,expectedSessionRevision:(await store.read(id)).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},
  async create({summary=false,memory=true,timing='before'}={}){
   const setup=newSetup();setup.user.姓名='虚构神明';setup.opening='初临钟楼';
   const s={...godAdapter.create({setup,mode:'offline'}),id:randomUUID(),revision:0,history:[],turns:[],requests:{},runtime:{hash:godRuntime.hash,descriptor:godRuntime.descriptor},modelState:initialModelState(policy.controlledChoice(models[0])),historyWindow:initialHistoryState(),rollingSummary:initialSummaryState(),memoryPalace:initialMemoryState(),memoryTasks:initialMemoryTasks()};
   if(summary)s.rollingSummary=sealSummary({...s.rollingSummary,config:{timing,model:policy.controlledChoice(models[1])}});
   await store.write(s);
   for(const pluginId of [...(memory?[M]:[]),...(summary?[R]:[])]){if(!(await plugins.catalog()).plugins.find(x=>x.id===pluginId).installed)await f.change('install',null,pluginId);await f.change('enable',s.id,pluginId);}
   if(memory)await tasks.configure(s.id,{operationId:randomUUID(),expectedSessionRevision:0,expectedMemoryRevision:0,selectionId:models[2].selectionId,catalogRevision:'v1'});
   return store.read(s.id);
  },
  async input(id){const x=await tasks.status(id),s=await store.read(id);return {operationId:randomUUID(),expectedSessionRevision:x.sessionRevision,expectedMemoryRevision:x.memoryRevision,expectedContextPolicyRevision:x.effectivePolicy.revision,input:s.turns.length?'再次观察钟楼':s.characterText};},
  async send(id,input=null){const first=await tasks.send(id,input||await f.input(id));return await tasks.wait(id)||first;},
  async summarySend(id){const s=await store.read(id),p=state.summary.effectiveContextPolicy(s,await plugins.summaryAuthorization(id),await plugins.historyAuthorization(id));return summaryTasks.send(id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedContextPolicyRevision:p.revision,input:s.turns.length?'继续观察钟楼':s.characterText});}
 };return f;
}

test('God task chain binds three explicit profiles, raw assembly, receipts and its own budget',async t=>{
 const f=await fixture(t),s=await f.create({summary:true});assert.equal(f.wires.length,0);
 let input,op;
 for(let i=0;i<3;i++){input=await f.input(s.id);op=await f.send(s.id,input);assert.equal(op.status,'complete',JSON.stringify(op));}
 assert.deepEqual(op.tasks.map(x=>x.stageId),['summary-before','story','memory-after']);
 assert.equal(f.wires.length,7);
 const disk=await f.store.read(s.id);assert.equal(disk.turns.length,3);assert.equal(disk.memoryPalace.processedThrough,3);assert.equal(disk.rollingSummary.versions.length,1);
 assert.equal(context.verifyMemory(disk),true);state.requireModelState(disk);
 const stories=f.wires.filter(w=>w.selectionId==='god-story'),summaries=f.wires.filter(w=>w.selectionId==='god-summary'),memories=f.wires.filter(w=>w.selectionId==='god-memory');
 for(const wire of f.wires)assert.deepEqual(wire.parameters,models.find(m=>m.selectionId===wire.selectionId).parameters);
 assert.equal(stories[0].messages[0].content,loadFrozen().system);assert.equal(stories[0].messages[1].content,s.characterText);
 assert.ok(stories[2].messages[0].content.startsWith(loadFrozen().system));assert.equal(stories[2].messages.filter(m=>m.role==='system').length,1);
 assert.deepEqual(stories[2].messages.slice(1,-1),disk.history.slice(2,4));
 assert.ok(stories[2].messages[0].content.indexOf('【历史摘要 · 数据】')<stories[2].messages[0].content.indexOf('【记忆宫殿·历史资料】'));
 assert.equal(summaries.length,1);assert.equal(JSON.parse(summaries[0].messages[1].content).completedTurns[0].content,disk.history[0].content);
 assert.equal(JSON.parse(memories[2].messages[1].content).turns[0].assistant,disk.turns[2].raw);
 assert.equal((await f.tasks.send(s.id,input)).replayed,true);assert.equal(f.wires.length,7);
 assert.equal((await f.transport.status()).used,7);assert.deepEqual(await readdir(join(f.directory,'dispatches')),['god']);
 for(const turn of disk.turns)assert.match(turn.model.evidence.ref,/^god\/slot-/);
 const earth=await createMemoryBudget({directory:join(f.directory,'dispatches'),limit:30});assert.equal((await earth.status()).used,0);
 await writeFile(join(f.directory,'wire-proof.json'),JSON.stringify({kind:'mock controlled transport; synthetic God compatibility only',realProviderCalls:0,wires:f.wires,turns:disk.turns},null,2));
});

test('summary-only coordinator uses God profile and reconstructs original persisted requests',async t=>{
 const f=await fixture(t),s=await f.create({summary:true,memory:false});
 for(let n=0;n<3;n++)assert.equal((await f.summarySend(s.id)).status,'complete');
 const disk=await f.store.read(s.id);assert.equal(context.verifySummary(disk),true);assert.equal(f.wires.length,4);
 assert.deepEqual(f.wires[2].parameters,models[1].parameters);assert.equal(f.wires[2].messages[0].content,SUMMARY_INSTRUCTION);
 assert.equal((await f.transport.status()).used,4);
});

test('God joint budget refuses an incomplete reservation without dispatch',async t=>{
 const f=await fixture(t,{limit:1}),s=await f.create();const op=await f.send(s.id);
 assert.equal(op.error,'BUDGET_EXHAUSTED');assert.equal(f.wires.length,0);assert.equal((await f.transport.status()).used,0);
});

test('God late background output after revocation is retained but not published',async t=>{
 const entered=latch(),gate=latch();const f=await fixture(t,{onCall:async body=>{if(body.selectionId==='god-memory'){entered.resolve();await gate.promise;}return response(body);}}),s=await f.create();
 try{
  await f.tasks.send(s.id,await f.input(s.id));await entered.promise;await f.change('disable',s.id);
 }finally{gate.resolve();}
 const op=await f.tasks.wait(s.id);assert.equal(op.status,'revoked');const disk=await f.store.read(s.id);
 assert.equal(disk.turns.length,1);assert.equal(disk.memoryPalace.processedThrough,0);assert.equal((await f.transport.status()).used,2);assert.equal(context.verifyMemory(disk),true);
});

test('God unknown result survives coordinator restart and is never resent under a new ID',async t=>{
 const f=await fixture(t,{onCall:body=>{if(body.selectionId==='god-memory')throw Error('synthetic lost response');return response(body);}}),s=await f.create(),input=await f.input(s.id);
 assert.equal((await f.send(s.id,input)).status,'unknown');
 const restarted=createMemoryTasks(f.hooks);
 assert.equal((await restarted.recover(s.id,input.operationId)).status,'unknown');
 await assert.rejects(restarted.send(s.id,await f.input(s.id)),/MEMORY_TASK_UNCONFIRMED/);
 assert.equal(f.wires.length,2);assert.equal((await f.transport.status()).used,2);assert.equal((await f.store.read(s.id)).memoryPalace.processedThrough,0);
});

test('transport refuses a policy from another card before any dispatch',async t=>{
 const f=await fixture(t);
 await assert.rejects(createMemoryTransport({directory:join(f.directory,'missing-policy'),cardId:'god'}),/CONTROL_MODEL_POLICY_REQUIRED/);
 await assert.rejects(createMemoryTransport({directory:join(f.directory,'bad'),cardId:'god',modelPolicy:createModelPolicy({cardId:'earth',parametersFor:()=>({max_tokens:1})})}),/CONTROL_MODEL_POLICY_INVALID/);
});
