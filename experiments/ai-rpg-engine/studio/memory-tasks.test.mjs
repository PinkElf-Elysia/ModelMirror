import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {VersionedSessionStore} from './versioned-store.mjs';
import {atomicJson,completeHistory} from './branch-archive.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {loadReviewedPlugins,MEMORY_PALACE_ID as M,ROLLING_SUMMARY_ID as R,sha,canonical} from '../plugins/catalog.mjs';
import {changeEntries} from '../plugins/memory-palace.mjs';
import {initialMemoryState,requireMemoryState,memoryOperations,sealMemory} from './memory-state.mjs';
import {initialMemoryTasks,requireMemoryTasks,memoryTaskBlock,parseMemoryProposal,MEMORY_INSTRUCTION,MEMORY_INSTRUCTION_HASH,memoryPlan,sealMemoryTasks} from './memory-task-state.mjs';
import {initialHistoryState} from './history-state.mjs';
import {initialSummaryState,sealSummary,requireSummaryState,SUMMARY_INSTRUCTION} from './summary-state.mjs';
import {COMPRESSION_INSTRUCTION} from './summary-compression.mjs';
import {initialModelState,controlledChoice} from './model-runtime.mjs';
import {parametersFor} from './model-parameters.mjs';
import {createMemoryTasks} from './memory-tasks.mjs';
import {createMemoryTransport} from './memory-task-provider.mjs';
import {createMemoryBudget,reservationId} from './memory-budget.mjs';
import {createSummaryBudget} from './summary-budget.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/memory-palace-b2/',import.meta.url));await mkdir(root,{recursive:true});
const models=['google/gemini-3.8-flash','openai/gpt-5.6-luna'].map((model,i)=>({model,name:model,selectionId:'model-'+i,selectionRevision:'v1',available:true,parameters:parametersFor(model)}));
const latch=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
function response(b,{raw,finish='stop',status='complete',receipt={}}={}){
 raw??=b.messages[0].content===MEMORY_INSTRUCTION?'{"operations":[]}':b.messages[0].content===SUMMARY_INSTRUCTION?'合成摘要':b.messages[0].content===COMPRESSION_INSTRUCTION?'合成压缩':'合成剧情原文';
 const model=models.find(m=>m.selectionId===b.selectionId).model,sse='data: '+JSON.stringify({choices:[{delta:{content:raw},finish_reason:finish}]})+'\n\ndata: [DONE]\n\n';
 return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:b.sessionId,requestId:b.requestId,selectionId:b.selectionId,selectionRevision:b.selectionRevision,requestedModel:model,actualModel:model,parameters:b.parameters,status,error:status==='complete'?null:'synthetic-failure',dispatched:true,retries:0,requestHash:sha(canonical(b)),rawHash:sha(raw),sseHash:sha(sse),...receipt}});
}
// Synthetic trusted M3 compatibility projection; actual disk, lock, catalog,
// receipts and shared budget code. Production constructor/assembly is the B3 gate.
class FixtureStore extends VersionedSessionStore {
 async write(s){return atomicJson(this.path(s.id),s);}
 async requireRuntime(s){if(s.runtime.hash!==sha('B2-synthetic-runtime'))throw Error('runtime mismatch');completeHistory(s);requireMemoryState(s);requireMemoryTasks(s);requireSummaryState(s);}
 async pluginSession(id){const s=await this.read(id);return {id,cardId:'earth',revision:s.revision,completedTurns:s.turns.length,resourceHash:s.runtime.hash,memoryPalaceCompatible:true,rollingSummaryCompatible:true,historyWindowCompatible:true,busy:this.running.has(id),pending:memoryTaskBlock(s)||!!s.memoryPalace.pending};}
}
async function fixture(t,{limit=40,onCall=null}={}){
 const directory=await mkdtemp(join(root,'case-')),wires=[],store=new FixtureStore(join(directory,'sessions'),()=>assert.fail('legacy provider must not run'));await store.init();
 const options={directory:join(directory,'dispatches'),limit,enabled:true,baseURL:'http://127.0.0.1:1/',serviceToken:'synthetic-memory-test-token-not-a-real-credential',fetcher:async(url,init)=>{if(init.method==='GET')return Response.json({models});const b=JSON.parse(init.body);wires.push(b);return onCall?onCall(b,wires.length):response(b);}};
 const transport=await createMemoryTransport(options),plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:id=>store.pluginSession(id),loadCatalog:()=>loadReviewedPlugins({rollingSummary:true,memoryPalace:true})});t.after(()=>plugins.close());
 const hooks={buildStory:async(s,input)=>({messages:[{role:'system',content:'B2 mock assembly; not author prompt'},...s.history,{role:'user',content:input}],input}),commitStory:async(s,task,result)=>{
  const user=task.messages.at(-1).content;s.history.push({role:'user',content:user},{role:'assistant',content:result.raw});s.turns.push({requestId:task.requestId,input:task.story.input,raw:result.raw,rawHash:sha(result.raw)});s.requests[task.requestId]={status:'complete'};s.revision++;
 },legacySend:async s=>store.exclusive(s.id,async()=>({status:'legacy-unmodified-path'}))};
 const tasks=createMemoryTasks({store,plugins,transport,...hooks});
 const f={directory,wires,store,plugins,transport,tasks,hooks,options,
  async change(action,id,pluginId=M){const c=await plugins.catalog(),p=c.plugins.find(p=>p.id===pluginId);return plugins.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(id?{sessionId:id,expectedSessionRevision:(await store.read(id)).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},
  async create({summary=false,timing='before',configured=true}={}){
   const id=randomUUID(),s={id,revision:0,history:[],turns:[],requests:{},runtime:{hash:sha('B2-synthetic-runtime')},modelState:initialModelState(controlledChoice(models[0])),historyWindow:initialHistoryState(),rollingSummary:initialSummaryState(),memoryPalace:initialMemoryState(),memoryTasks:initialMemoryTasks()};
   if(summary)s.rollingSummary=sealSummary({...s.rollingSummary,config:{timing,model:controlledChoice(models[0])}});
   await store.write(s);
   for(const p of [M,...(summary?[R]:[])]){if(!(await plugins.catalog()).plugins.find(x=>x.id===p).installed)await f.change('install',null,p);await f.change('enable',id,p);}
   if(configured)await tasks.configure(id,{operationId:randomUUID(),expectedSessionRevision:0,expectedMemoryRevision:0,selectionId:'model-1',catalogRevision:'v1'});
   return store.read(id);
  },
  async input(id,send=true,key=randomUUID()){const x=await tasks.status(id);return {operationId:key,expectedSessionRevision:x.sessionRevision,expectedMemoryRevision:x.memoryRevision,expectedContextPolicyRevision:x.effectivePolicy.revision,...(send?{input:'继续虚构经历'}:{})};},
  async seed(s,count=2){for(let i=0;i<count;i++){const raw='原始回复 '+i+'\n<details>卡内记忆</details>',requestId='seed-'+s.turns.length;s.history.push({role:'user',content:'原始角色资料/前后置 '+i},{role:'assistant',content:raw});s.turns.push({requestId,input:'原始输入'+i,raw,rawHash:sha(raw)});s.requests[requestId]={status:'complete'};s.revision++;}await store.write(s);return s;},
  async send(id,input=null){const first=await tasks.send(id,input||await f.input(id));return await tasks.wait(id)||first;}
 };return f;
}
const proposal={type:'create',name:'合成人物',keywords:['名字'],match:'any',roles:['user','assistant'],content:'说过一个尚未兑现的承诺。',sourceTurns:[1]};

test('frozen approved instruction and strict JSON including escaped duplicate keys',async()=>{
 const s=JSON.parse(await readFile(new URL('../../../docs/ai-rpg-experiment/memory-palace/STATUS.json',import.meta.url),'utf8'));
 assert.equal(MEMORY_INSTRUCTION_HASH,s.delivery.instructionSha256);assert.deepEqual(parseMemoryProposal('{"operations":[]}'),[]);
 for(const x of ['{"operations":[],"operations":[]}','{"operations":[],"oper\\u0061tions":[]}','{"operations":[{"type":"create","type":"update"}]}','```json\n{}\n```','{"operations":[],}','{"operations":[]} trailing','{"operations":[]','{"operations":[],"extra":1}'])assert.throws(()=>parseMemoryProposal(x),/MEMORY_OUTPUT_REJECTED/);
});

test('explicit independent model selection, idempotent settings and no dispatch on settings/status',async t=>{
 const f=await fixture(t),s=await f.create({configured:false});await assert.rejects(f.send(s.id),/MEMORY_MODEL_REQUIRED/);assert.equal(f.wires.length,0);
 const x={operationId:randomUUID(),expectedSessionRevision:0,expectedMemoryRevision:0,selectionId:'model-1',catalogRevision:'v1'};
 await f.tasks.configure(s.id,x);assert.equal((await f.tasks.configure(s.id,x)).replayed,true);
 await assert.rejects(f.tasks.configure(s.id,{...x,selectionId:'model-0'}),/MEMORY_OPERATION_COLLISION/);
 await f.tasks.catalog(s.id);await f.tasks.status(s.id);assert.equal(f.wires.length,0);
 const disk=await f.store.read(s.id);assert.equal(disk.modelState.current.model,models[0].model);assert.equal(disk.memoryTasks.config.model.model,models[1].model);
});

test('three completed stories update once each, raw source and existing entries, same ID never replays',async t=>{
 const f=await fixture(t,{onCall:b=>response(b,{raw:b.messages[0].content===MEMORY_INSTRUCTION?JSON.stringify({operations:JSON.parse(b.messages[1].content).entries.length?[]:[proposal]}):undefined})}),s=await f.create();
 let last,input;for(let i=0;i<3;i++){input=await f.input(s.id);last=await f.send(s.id,input);assert.equal(last.status,'complete');}
 const disk=await f.store.read(s.id),updates=f.wires.filter(w=>w.messages[0].content===MEMORY_INSTRUCTION);assert.equal(f.wires.length,6);assert.equal(disk.memoryPalace.processedThrough,3);assert.equal(disk.memoryPalace.entries.length,1);
 assert.deepEqual(updates[0].parameters,{max_tokens:16384});assert.equal(updates[1].selectionId,'model-1');assert.equal(JSON.parse(updates[1].messages[1].content).entries.length,1);
 assert.deepEqual(JSON.parse(updates[2].messages[1].content).turns,[{number:3,user:disk.history[4].content,assistant:disk.history[5].content}]);
 assert.equal((await f.tasks.send(s.id,input)).replayed,true);assert.equal(f.wires.length,6);assert.equal((await f.transport.status()).used,6);
});

test('catch-up uses all unprocessed raw turns, not M2 summary; six stages reserved atomically',async t=>{
 const f=await fixture(t,{limit:6,onCall:b=>response(b,{raw:b.messages[0].content===SUMMARY_INSTRUCTION?'长'.repeat(10001):undefined})}),s=await f.seed(await f.create({summary:true,timing:'after'}));
 assert.equal((await f.tasks.status(s.id)).maxCalls,6);const op=await f.send(s.id);assert.equal(op.status,'complete');assert.deepEqual(op.tasks.map(x=>x.stageId),['summary-before','compression-before','story','summary-after','compression-after','memory-after']);
 assert.equal(new Set(op.tasks.map(x=>x.requestId)).size,6);assert.equal(new Set(op.leases.map(x=>x.slot)).size,6);assert.equal((await f.transport.status()).used,6);
 const disk=await f.store.read(s.id),mem=JSON.parse(f.wires.at(-1).messages[1].content);assert.equal(mem.turns.length,3);assert.equal(mem.turns[0].user,s.history[0].content);assert.equal(mem.turns[0].assistant,s.history[1].content);assert.equal(disk.rollingSummary.versions.at(-1).coveredThrough,2);assert.equal(disk.memoryPalace.processedThrough,3);
});

test('insufficient joint budget dispatches nothing; unneeded compression reservations released',async t=>{
 const f=await fixture(t,{limit:5}),s=await f.seed(await f.create({summary:true,timing:'after'}));assert.equal((await f.send(s.id)).error,'BUDGET_EXHAUSTED');assert.equal(f.wires.length,0);assert.equal((await f.transport.status()).remaining,5);
 const g=await fixture(t,{limit:6}),v=await g.seed(await g.create({summary:true,timing:'after'}));const op=await g.send(v.id);assert.equal(op.status,'complete');assert.deepEqual(op.skipped,['compression-before','compression-after']);assert.equal((await g.transport.status()).remaining,2);assert.equal((await g.transport.status()).reserved,0);
});

for(const [name,raw,finish] of [['duplicate','{"operations":[],"operations":[]}','stop'],['empty','','stop'],['truncated','{"operations":[]}','length'],['extra','{"operations":[],"extra":true}','stop'],['invalid-operation',JSON.stringify({operations:[proposal,{...proposal,sourceTurns:[99]}]}),'stop'],['oversize',JSON.stringify({operations:[{...proposal,content:'字'.repeat(2001)}]}),'stop']])test('invalid memory '+name+' retains story/raw, advances no coverage, no automatic retry',async t=>{
 const f=await fixture(t,{onCall:b=>response(b,b.messages[0].content===MEMORY_INSTRUCTION?{raw,finish}:{})}),s=await f.create();const op=await f.send(s.id);assert.equal(op.status,'failed');const disk=await f.store.read(s.id);assert.equal(disk.turns.length,1);assert.equal(disk.memoryPalace.processedThrough,0);assert.equal(disk.memoryPalace.entries.length,0);assert.equal(op.tasks.at(-1).output.raw,raw);assert.equal((await f.transport.status()).used,2);
 await assert.rejects(f.send(s.id),/MEMORY_UPDATE_REQUIRES_EXPLICIT_ACTION/);assert.equal(f.wires.length,2);
});

test('M2 background failure preserves story and prevents M3; explicit update runs pending stages once',async t=>{
 let failing=true;const f=await fixture(t,{onCall:b=>response(b,{raw:failing&&b.messages[0].content===SUMMARY_INSTRUCTION?'':undefined})}),s=await f.seed(await f.create({summary:true,timing:'after'}),1);
 const op=await f.send(s.id);assert.equal(op.status,'failed');assert.deepEqual(op.tasks.map(t=>t.purpose),['story','summary']);assert.equal((await f.store.read(s.id)).turns.length,2);assert.equal((await f.transport.status()).reserved,0);
 failing=false;const update=await f.tasks.update(s.id,await f.input(s.id,false));assert.equal(update.status,'complete');assert.deepEqual(update.tasks.map(t=>t.purpose),['summary','memory']);assert.equal((await f.store.read(s.id)).memoryPalace.processedThrough,2);
});

test('background survives returned story; next send waits; simultaneous settings/manual writes reject',async t=>{
 const entered=latch(),gate=latch();let hold=true;const f=await fixture(t,{onCall:async b=>{if(hold&&b.messages[0].content===MEMORY_INSTRUCTION){entered.resolve();await gate.promise;}return response(b);}}),s=await f.create();
 const first=await f.tasks.send(s.id,await f.input(s.id));assert.equal(first.background,true);await entered.promise;assert.equal((await f.store.read(s.id)).turns.length,1);
 const disk=await f.store.read(s.id);await assert.rejects(f.tasks.configure(s.id,{operationId:randomUUID(),expectedSessionRevision:disk.revision,expectedMemoryRevision:disk.memoryPalace.revision,selectionId:'model-0',catalogRevision:'v1'}),/SESSION_BUSY/);
 await assert.rejects(memoryOperations(f.store,f.plugins).save(s.id,{operationId:randomUUID(),expectedSessionRevision:disk.revision,expectedMemoryRevision:disk.memoryPalace.revision,change:{action:'create',value:{name:'人',keywords:['人'],match:'any',roles:['user'],content:'记忆',enabled:true}}}),/SESSION_BUSY/);
 const next=f.tasks.send(s.id,await f.input(s.id));assert.equal(f.wires.length,2);hold=false;gate.resolve();await next;await f.tasks.wait(s.id);assert.equal((await f.store.read(s.id)).turns.length,2);assert.equal(f.wires.length,4);
});

for(const action of ['disable','uninstall'])test('late memory after '+action+' never activates; paid attempt retained',async t=>{
 const entered=latch(),gate=latch();const f=await fixture(t,{onCall:async b=>{if(b.messages[0].content===MEMORY_INSTRUCTION){entered.resolve();await gate.promise;}return response(b);}}),s=await f.create();
 await f.tasks.send(s.id,await f.input(s.id));await entered.promise;await f.change(action,action==='disable'?s.id:null);gate.resolve();const op=await f.tasks.wait(s.id);assert.equal(op.status,'revoked');assert.equal((await f.store.read(s.id)).memoryPalace.processedThrough,0);assert.equal((await f.transport.status()).used,2);
 assert.equal((await f.send(s.id)).status,'legacy-unmodified-path');assert.equal(f.wires.length,2);
});

test('unknown after background transport loss blocks new IDs across restart and even after disable',async t=>{
 const f=await fixture(t,{onCall:b=>{if(b.messages[0].content===MEMORY_INSTRUCTION)throw Error('synthetic lost connection');return response(b);}}),s=await f.create(),input=await f.input(s.id);assert.equal((await f.send(s.id,input)).status,'unknown');
 await f.change('disable',s.id);const restarted=createMemoryTasks({store:f.store,plugins:f.plugins,transport:await createMemoryTransport(f.options),...f.hooks});assert.equal((await restarted.recover(s.id,input.operationId)).status,'unknown');assert.equal(f.wires.length,2);
 await assert.rejects(restarted.send(s.id,await f.input(s.id)),/MEMORY_TASK_UNCONFIRMED/);assert.equal((await f.transport.status()).reserved,0);
});

test('cancel known late response cannot publish; cancelled unknown remains unresolved',async t=>{
 for(const unknown of [false,true]){
  const entered=latch(),gate=latch(),f=await fixture(t,{onCall:async b=>{if(b.messages[0].content===MEMORY_INSTRUCTION){entered.resolve();await gate.promise;if(unknown)throw Error('lost');}return response(b);}}),s=await f.create();
  await f.tasks.send(s.id,await f.input(s.id));await entered.promise;f.store.cancel(s.id);gate.resolve();const op=await f.tasks.wait(s.id);assert.equal(op.status,unknown?'unknown':'cancelled');assert.equal((await f.store.read(s.id)).memoryPalace.processedThrough,0);assert.equal((await f.transport.status()).used,2);
 }
});

test('failed final publication recovers original receipt once; no fresh dispatch or partial library',async t=>{
 const f=await fixture(t),s=await f.create(),write=f.store.write.bind(f.store);f.store.write=async x=>{if(x.memoryPalace.processedThrough)throw Error('synthetic final write fail');return write(x);};
 const input=await f.input(s.id);await f.tasks.send(s.id,input);let final;try{final=await f.tasks.wait(s.id);}catch{}f.store.write=write;
 const disk=await f.store.read(s.id);assert.equal(disk.memoryPalace.processedThrough,0);assert.equal(disk.memoryPalace.entries.length,0);
 const reopened=createMemoryTasks({store:f.store,plugins:f.plugins,transport:f.transport,...f.hooks});const op=await reopened.recover(s.id,input.operationId);assert.equal(op.status,'complete');assert.equal((await f.store.read(s.id)).memoryPalace.processedThrough,1);assert.equal(f.wires.length,2);await reopened.recover(s.id,input.operationId);assert.equal(f.wires.length,2);
});

test('settings intent crash recovered with original ID; no network call or silent reapply',async t=>{
 const f=await fixture(t),s=await f.create({configured:false}),write=f.store.write.bind(f.store);let n=0;f.store.write=async x=>{if(++n===2)throw Error('setting disk fail');return write(x);};
 const input={operationId:randomUUID(),expectedSessionRevision:0,expectedMemoryRevision:0,selectionId:'model-1',catalogRevision:'v1'};await assert.rejects(f.tasks.configure(s.id,input),/setting disk fail/);f.store.write=write;
 assert.equal((await f.tasks.recoverSetting(s.id,input.operationId)).status,'not-applied');assert.equal((await f.store.read(s.id)).memoryTasks.config.model,null);assert.equal(f.wires.length,0);assert.equal((await f.tasks.configure(s.id,input)).status,'not-applied');
});

test('shared slots prevent reset by another session/old transport; duplicate stage and repeat group rejected',async()=>{
 const directory=await mkdtemp(join(root,'budget-')),b=await createMemoryBudget({directory,limit:3}),old=await createSummaryBudget({directory,limit:3});
 const stages=[{stageId:'story',purpose:'story'},{stageId:'memory-after',purpose:'memory'}],id=reservationId('one','op');const leases=await b.reserve(id,'one',stages);
 await assert.rejects(old.reserve(reservationId('other','op'),'other',['summary','story']),/BUDGET_EXHAUSTED/);
 await b.dispatched(leases[0],'story',sha('wire'));assert.equal(await b.release(leases[0]),false);await b.release(leases[1]);assert.equal((await b.status()).remaining,2);await assert.rejects(b.reserve(id,'one',stages),/BUDGET_OPERATION_ALREADY_RESERVED/);
 await assert.rejects(b.reserve(reservationId('bad','op'),'bad',[stages[0],stages[0]]),/BUDGET_RESERVATION_INVALID/);
});

test('full library and stale revisions refuse before any dispatch; manual entries stay protected',async t=>{
 const f=await fixture(t),s=await f.create();let entries=[];for(let i=0;i<100;i++)entries=changeEntries(entries,{action:'create',value:{name:'条目'+i,keywords:['关键'+i],match:'any',roles:['user'],content:'过去事实',enabled:true}},'manual-'+i);
 s.memoryPalace=sealMemory({...s.memoryPalace,entries});await f.store.write(s);await assert.rejects(f.send(s.id),/MEMORY_CAPACITY_FULL/);assert.equal(f.wires.length,0);
 const x=await f.input(s.id);await assert.rejects(f.tasks.send(s.id,{...x,expectedMemoryRevision:99}),/MEMORY_TASK_REVISION_CONFLICT/);
 await assert.rejects(f.tasks.send(s.id,{...x,history:[]}),/MEMORY_TASK_INPUT_INVALID/);
});

test('protected and disabled entries sent as data; model cannot edit protected entry atomically',async t=>{
 let item;const f=await fixture(t,{onCall:b=>response(b,b.messages[0].content===MEMORY_INSTRUCTION?{raw:JSON.stringify({operations:[{...proposal,type:'update',id:item.id,baseVersion:item.version}]})}:{})}),s=await f.create();
 const entries=changeEntries([],{action:'create',value:{name:'保护对象',keywords:['对象'],match:'any',roles:['user'],content:'玩家确认的事实',enabled:false}},'manual-one');item=entries[0];s.memoryPalace=sealMemory({...s.memoryPalace,entries});await f.store.write(s);
 const op=await f.send(s.id);assert.equal(op.status,'failed');const source=JSON.parse(f.wires.at(-1).messages[1].content);assert.equal(source.entries[0].protected,true);assert.equal(source.entries[0].enabled,false);assert.deepEqual((await f.store.read(s.id)).memoryPalace.entries,entries);
});

test('authority revoked between catalog selection and dispatch spends no provider slot',async t=>{
 const f=await fixture(t),s=await f.create();const execute=f.transport.execute.bind(f.transport);let once=true;
 f.transport.execute=async(...args)=>{if(once){once=false;await f.change('disable',s.id);}return execute(...args);};
 const op=await f.send(s.id);assert.equal(op.status,'failed');assert.equal(f.wires.length,0);assert.equal((await f.transport.status()).used,0);assert.equal((await f.transport.status()).reserved,0);assert.equal((await f.store.read(s.id)).turns.length,0);
});

test('recovery publishes completed story only and never launches missing memory stage',async t=>{
 const f=await fixture(t),s=await f.create(),write=f.store.write.bind(f.store);f.store.write=async x=>{if(x.turns.length)throw Error('story publication disk fail');return write(x);};
 const input=await f.input(s.id);try{await f.send(s.id,input);}catch{}f.store.write=write;assert.equal((await f.store.read(s.id)).turns.length,0);assert.equal(f.wires.length,1);
 const restarted=createMemoryTasks({store:f.store,plugins:f.plugins,transport:f.transport,...f.hooks}),op=await restarted.recover(s.id,input.operationId);assert.equal(op.status,'failed');assert.equal((await f.store.read(s.id)).turns.length,1);assert.equal((await f.store.read(s.id)).memoryPalace.processedThrough,0);assert.equal(f.wires.length,1);
 await assert.rejects(restarted.send(s.id,await f.input(s.id)),/MEMORY_UPDATE_REQUIRES_EXPLICIT_ACTION/);
 const updated=await restarted.update(s.id,await f.input(s.id,false));assert.equal(updated.status,'complete');assert.equal(f.wires.length,2);
});

test('cancelled unknown later gets a complete receipt, recovery still cannot activate it',async t=>{
 const entered=latch(),gate=latch(),f=await fixture(t,{onCall:async b=>{if(b.messages[0].content===MEMORY_INSTRUCTION){entered.resolve();await gate.promise;throw Error('lost');}return response(b);}}),s=await f.create(),input=await f.input(s.id);
 await f.tasks.send(s.id,input);await entered.promise;f.store.cancel(s.id);gate.resolve();const op=await f.tasks.wait(s.id);assert.equal(op.status,'unknown');assert.equal(op.cancelRequested,true);
 const original=f.transport.recover.bind(f.transport);f.transport.recover=async task=>task.purpose==='memory'?{raw:'{"operations":[]}',finishReason:'stop',dispatched:true,evidence:{record:{status:'complete',sessionId:s.id,requestId:task.requestId,requestedModel:task.selection.model,parameters:task.selection.parameters,rawHash:sha('{"operations":[]}'),requestHash:sha(JSON.stringify({sessionId:task.sessionId,requestId:task.requestId,selectionId:task.selection.selectionId,selectionRevision:task.selection.selectionRevision,messages:task.messages,parameters:task.selection.parameters}))}}}:original(task);
 const restarted=createMemoryTasks({store:f.store,plugins:f.plugins,transport:f.transport,...f.hooks});const done=await restarted.recover(s.id,input.operationId);assert.equal(done.tasks.at(-1).status,'cancelled');assert.equal((await f.store.read(s.id)).memoryPalace.processedThrough,0);assert.equal(f.wires.length,2);
});

test('M2 before timing and current library survive story failure; no repeat summary on next send',async t=>{
 let failStory=true;const f=await fixture(t,{onCall:b=>response(b,failStory&&b.messages[0].content==='B2 mock assembly; not author prompt'?{status:'failed'}:{})}),s=await f.seed(await f.create({summary:true,timing:'before'}));
 const first=await f.send(s.id);assert.equal(first.status,'failed');assert.deepEqual(first.tasks.map(t=>t.purpose),['summary','story']);assert.equal((await f.store.read(s.id)).rollingSummary.versions.length,1);failStory=false;
 const next=await f.send(s.id);assert.equal(next.status,'complete');assert.deepEqual(next.tasks.map(t=>t.purpose),['story','memory']);assert.equal((await f.store.read(s.id)).rollingSummary.versions.length,1);
});

test('parallel session reservations share budget and cannot exceed it',async()=>{
 const directory=await mkdtemp(join(root,'parallel-')),a=await createMemoryBudget({directory,limit:3}),b=await createMemoryBudget({directory,limit:3}),plan=[{stageId:'story',purpose:'story'},{stageId:'memory-after',purpose:'memory'}];
 const outcomes=await Promise.allSettled([a.reserve(reservationId('a','op'),'a',plan),b.reserve(reservationId('b','op'),'b',plan)]);assert.equal(outcomes.filter(o=>o.status==='fulfilled').length,1);assert.equal((await b.status()).reserved,2);
});

test('interrupted allocation recovers only owned slots, releases without dispatch, no reservation replay',async()=>{
 const directory=await mkdtemp(join(root,'allocation-crash-')),budget=await createMemoryBudget({directory,limit:2}),id=reservationId('s','crash'),plan=[{stageId:'story',purpose:'story'},{stageId:'memory-after',purpose:'memory'}];
 const leases=await budget.reserve(id,'s',plan),file=join(directory,'earth','m3-reservations',id+'.json'),group=JSON.parse(await readFile(file,'utf8'));group.status='allocating';group.leases=[];await atomicJson(file,group);
 const restarted=await createMemoryBudget({directory,limit:2}),recovered=await restarted.recover(id);assert.equal(recovered.status,'abandoned');assert.deepEqual(recovered.leases,leases);
 await assert.rejects(restarted.resolve(leases[0],{sessionId:'s',purpose:'story'}),/BUDGET_LEASE_INVALID/);
 for(const l of recovered.leases)await restarted.release(l);assert.deepEqual(await restarted.status(),{limit:2,used:0,reserved:0,remaining:2});await assert.rejects(restarted.reserve(id,'s',plan),/BUDGET_OPERATION_ALREADY_RESERVED/);
});

test('completed receipt for a different request hash is unknown and cannot activate memory',async t=>{
 const f=await fixture(t),s=await f.create(),execute=f.transport.execute.bind(f.transport);f.transport.execute=async(...args)=>{const r=await execute(...args);if(args[0].purpose==='memory')r.evidence.record.requestHash=sha('another-wire');return r;};
 const op=await f.send(s.id);assert.equal(op.status,'unknown');assert.equal((await f.store.read(s.id)).memoryPalace.processedThrough,0);assert.equal(f.wires.length,2);
});

test('publication acknowledgement lost after atomic write cannot overwrite committed library',async t=>{
 const f=await fixture(t),s=await f.create(),write=f.store.write.bind(f.store);let lost=false;
 f.store.write=async x=>{await write(x);if(!lost&&x.memoryPalace.processedThrough===1){lost=true;throw Error('acknowledgement lost after commit');}};
 const input=await f.input(s.id);await f.tasks.send(s.id,input);try{await f.tasks.wait(s.id);}catch(e){assert.equal(e.code,'MEMORY_PUBLICATION_UNCERTAIN');}f.store.write=write;
 assert.equal((await f.store.read(s.id)).memoryPalace.processedThrough,1);const restarted=createMemoryTasks({store:f.store,plugins:f.plugins,transport:f.transport,...f.hooks});const op=await restarted.recover(s.id,input.operationId);assert.equal(op.status,'complete');assert.equal((await f.store.read(s.id)).memoryPalace.revision,s.memoryPalace.revision+1);assert.equal(f.wires.length,2);
});

test('explicit compression repair reuses complete overflow and does not summarize source twice',async t=>{
 let failCompression=true;const f=await fixture(t,{onCall:b=>response(b,{raw:b.messages[0].content===SUMMARY_INSTRUCTION?'摘要'.repeat(6000):b.messages[0].content===COMPRESSION_INSTRUCTION?(failCompression?'':'压缩后的合成摘要'):undefined})}),s=await f.seed(await f.create({summary:true}));
 const failed=await f.send(s.id);assert.equal(failed.status,'failed');assert.deepEqual(failed.tasks.map(t=>t.purpose),['summary','compression']);assert.equal((await f.store.read(s.id)).turns.length,2);failCompression=false;
 const op=await f.tasks.update(s.id,await f.input(s.id,false));assert.equal(op.status,'complete');assert.deepEqual(op.tasks.map(t=>t.purpose),['compression','memory']);assert.equal(f.wires.filter(b=>b.messages[0].content===SUMMARY_INSTRUCTION).length,1);assert.equal((await f.store.read(s.id)).rollingSummary.versions[0].compression.raw,'摘要'.repeat(6000));
});
