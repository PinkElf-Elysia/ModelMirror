import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,readdir,rename} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {SummarySessionStore} from './summary-session-store.mjs';
import {createSummaryTasks} from './summary-tasks.mjs';
import {createSummaryTransport} from './summary-task-provider.mjs';
import {createSummaryBudget,reservationId} from './summary-budget.mjs';
import {createControlledProvider} from './controlled-provider.mjs';
import {parametersFor} from './model-parameters.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {loadReviewedPlugins,ROLLING_SUMMARY_ID as R,sha,canonical} from '../plugins/catalog.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/rolling-summary-b2/tests/',import.meta.url));await mkdir(root,{recursive:true});
const token='synthetic-service-token-rolling-summary-no-real-key';
const catalog=['google/gemini-3.8-flash','openai/gpt-5.6-luna'].map((model,i)=>({model,name:model,selectionId:'model-'+i,selectionRevision:'v1',available:true,parameters:parametersFor(model)}));
const latch=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
function response(body,{raw='合成输出',finish='stop',status='complete'}={}){
 const sse='data: '+JSON.stringify({model:body.selectionId==='model-1'?catalog[1].model:catalog[0].model,choices:[{delta:{content:raw},finish_reason:finish}]})+'\n\ndata: [DONE]\n\n';
 const model=catalog.find(m=>m.selectionId===body.selectionId).model;
 return new Response(JSON.stringify({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:body.sessionId,requestId:body.requestId,selectionId:body.selectionId,selectionRevision:body.selectionRevision,requestedModel:model,actualModel:model,parameters:body.parameters,status,error:status==='complete'?null:'synthetic failure',dispatched:true,retries:0,requestHash:sha(canonical(body)),rawHash:sha(raw),sseHash:sha(sse)}}),{status:200,headers:{'content-type':'application/json'}});
}
async function fixture(t,{limit=20,onCall}={}){
 const directory=await mkdtemp(join(root,'f-')),wires=[];
 const options={directory:join(directory,'provider'),baseURL:'http://127.0.0.1:1/',serviceToken:token,enabled:true,limit,fetcher:async(url,request)=>{
  if(request.method==='GET')return new Response(JSON.stringify({models:catalog}),{headers:{'content-type':'application/json'}});
  const body=JSON.parse(request.body);wires.push(body);return onCall?onCall(body,wires):response(body);
 }};
 const transport=await createSummaryTransport(options),store=new SummarySessionStore(join(directory,'sessions'),()=>assert.fail('no legacy dispatch'),null,{control:transport});await store.init();
 const plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:id=>store.pluginSession(id),loadCatalog:()=>loadReviewedPlugins({rollingSummary:true})});t.after(()=>plugins.close());
 const hooks={buildStory:async(s,input)=>({messages:[{role:'system',content:'B2 合成剧情回调，非B3装配证据'},{role:'user',content:input}],current:input}),commitStory:async(s,task,result)=>{
  s.history.push({role:'user',content:task.story.current},{role:'assistant',content:result.raw});s.turns.push({requestId:task.requestId,raw:result.raw,rawHash:sha(result.raw),model:{selectionRevision:s.modelState.revision,selection:task.selection}});s.requests[task.requestId]={status:'complete',selectionRevision:s.modelState.revision,selection:task.selection};s.revision++;
 }};
 const tasks=createSummaryTasks({store,plugins,transport,...hooks});
 const f={directory,options,wires,transport,store,plugins,tasks,hooks,async change(action,s){const c=await plugins.catalog(),p=c.plugins.find(x=>x.id===R);return plugins.change(action,{operationId:randomUUID(),pluginId:R,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},async create(timing='before',model=0){let s=await store.create({characterText:'姓名：虚构B2角色',world:'表世界',params:{max_tokens:16384},mode:'offline'});if(!(await plugins.catalog()).plugins.find(x=>x.id===R).installed)await f.change('install');await f.change('enable',s);s=(await store.configureSummary(s.id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedSummaryRevision:0,timing,selectionId:catalog[model].selectionId,catalogRevision:'v1'},plugins)).session;return s;},async input(s,input='继续',operationId=randomUUID()){const status=await store.summaryStatus(s.id,plugins);return {operationId,expectedSessionRevision:status.sessionRevision,expectedContextPolicyRevision:status.effectivePolicy.revision,...(input===null?{}:{input})};},async seed(s,count=2){for(let i=0;i<count;i++){const id='seed-'+i,raw='历史原文'+i,selection=structuredClone(s.modelState.current);s.history.push({role:'user',content:'输入'+i},{role:'assistant',content:raw});s.turns.push({requestId:id,raw,rawHash:sha(raw),model:{selectionRevision:0,selection}});s.requests[id]={status:'complete',selectionRevision:0,selection};s.revision++;}await store.write(s);return s;}};return f;
}

test('foreground 4 stories and 2 summaries, independent Luna parameters, exact summary source and no duplicate calls',async t=>{
 const f=await fixture(t);let s=await f.create('before',1);assert.equal(f.wires.length,0);
 let last,input;for(let i=0;i<4;i++){input=await f.input(s,'剧情'+i);last=await f.tasks.send(s.id,input);assert.equal(last.status,'complete');s=await f.store.read(s.id);}
 assert.equal(f.wires.length,6);assert.equal(s.rollingSummary.versions.length,2);assert.equal(s.rollingSummary.versions.at(-1).coveredThrough,2);
 const summaries=f.wires.filter(w=>w.selectionId==='model-1');assert.equal(summaries.length,2);assert.deepEqual(summaries[0].parameters,{max_tokens:16384});
 assert.deepEqual(JSON.parse(summaries[0].messages[1].content).completedTurns,s.history.slice(0,2));
 assert.deepEqual(JSON.parse(summaries[1].messages[1].content).completedTurns,s.history.slice(2,4));assert.equal(JSON.parse(summaries[1].messages[1].content).previousSummary,'合成输出');
 assert.equal((await f.tasks.send(s.id,input)).replayed,true);assert.equal(f.wires.length,6);assert.equal((await f.transport.status()).used,6);
});
test('three permits required atomically; insufficient budget dispatches neither and other sessions cannot reset budget',async t=>{
 const f=await fixture(t,{limit:1});let s=await f.seed(await f.create());const before=structuredClone(s.history),r=await f.tasks.send(s.id,await f.input(s));
 assert.equal(r.error,'BUDGET_EXHAUSTED');assert.equal(f.wires.length,0);assert.equal((await f.transport.status()).remaining,1);assert.deepEqual((await f.store.read(s.id)).history,before);
 const other=await f.create();assert.equal((await f.tasks.send(other.id,await f.input(other))).status,'complete');assert.equal((await f.transport.status()).remaining,0);
});
test('summary failure retains old data/draft, releases unstarted story, and requires explicit update',async t=>{
 const f=await fixture(t,{onCall:body=>response(body,{raw:'',finish:'stop'})});const s=await f.seed(await f.create()),input=await f.input(s,'保留草稿');
 const r=await f.tasks.send(s.id,input);assert.equal(r.status,'failed');assert.equal(f.wires.length,1);assert.equal((await f.transport.status()).used,1);assert.equal((await f.transport.status()).reserved,0);
 const current=await f.store.read(s.id);assert.equal(current.rollingSummary.activeVersionId,null);assert.equal(r.input.input,'保留草稿');assert.deepEqual(current.history,s.history);
 await assert.rejects(f.tasks.send(s.id,await f.input(current)),/SUMMARY_UPDATE_REQUIRES_EXPLICIT_ACTION/);
 assert.equal((await f.tasks.update(s.id,await f.input(current,null))).status,'failed');assert.equal(f.wires.length,2);
});
test('successful summary followed by failed story is retained and never summarized again on explicit next send',async t=>{
 let calls=0;const f=await fixture(t,{onCall:body=>response(body,{status:++calls===2?'failed':'complete'})});const s=await f.seed(await f.create());
 const first=await f.tasks.send(s.id,await f.input(s));assert.equal(first.status,'failed');assert.equal(first.tasks[0].status,'complete');let disk=await f.store.read(s.id);assert.equal(disk.rollingSummary.versions.length,1);
 const next=await f.tasks.send(s.id,await f.input(disk));assert.equal(next.status,'complete');assert.equal(next.tasks.length,1);assert.equal(next.tasks[0].purpose,'story');assert.equal(f.wires.length,3);
});
for(const variant of ['truncated','oversize'])test('invalid summary '+variant+' preserves evidence; oversized complete response gets one compression attempt',async t=>{
 const raw=variant==='oversize'?'😀'.repeat(10001):'不完整';const f=await fixture(t,{onCall:body=>response(body,{raw,finish:variant==='truncated'?'length':'stop'})});const s=await f.seed(await f.create());const r=await f.tasks.send(s.id,await f.input(s));
 assert.equal(r.status,'failed');assert.equal(f.wires.length,variant==='oversize'?2:1);assert.equal(r.tasks[0].output.raw,raw);assert.equal((await f.store.read(s.id)).rollingSummary.activeVersionId,null);
});
test('cancel and late response consume one dispatched permit and never publish summary/story',async t=>{
 const entered=latch(),release=latch();const f=await fixture(t,{onCall:async body=>{entered.resolve();await release.promise;return response(body);}}),s=await f.seed(await f.create());
 const pending=f.tasks.send(s.id,await f.input(s));await entered.promise;assert.equal(f.store.cancel(s.id).cancelRequested,true);release.resolve();const result=await pending;
 assert.equal(result.status,'cancelled');assert.equal((await f.store.read(s.id)).rollingSummary.versions.length,0);assert.equal(f.wires.length,1);assert.equal((await f.transport.status()).used,1);assert.equal((await f.transport.status()).reserved,0);
});
test('revoke during model call prevents late publication, retains output, and stops story dispatch',async t=>{
 const entered=latch(),release=latch();const f=await fixture(t,{onCall:async body=>{entered.resolve();await release.promise;return response(body);}}),s=await f.seed(await f.create());
 const pending=f.tasks.send(s.id,await f.input(s));await entered.promise;await f.change('disable',await f.store.read(s.id));release.resolve();const result=await pending;
 assert.equal(result.status,'revoked');assert.equal(result.tasks[0].output.raw,'合成输出');assert.equal((await f.store.read(s.id)).rollingSummary.versions.length,0);assert.equal(f.wires.length,1);
});
test('unknown transport result blocks new IDs and edits across restart; recovery never redispatches',async t=>{
 const f=await fixture(t,{onCall:()=>{throw Error('synthetic connection dropped');}}),s=await f.seed(await f.create());const input=await f.input(s),result=await f.tasks.send(s.id,input);assert.equal(result.status,'unknown');
 const restartedStore=new SummarySessionStore(f.store.directory,()=>assert.fail('restart never dispatches'));await restartedStore.init();
 const second=createSummaryTasks({store:restartedStore,plugins:f.plugins,transport:await createSummaryTransport(f.options),...f.hooks});assert.equal(f.wires.length,1);
 assert.equal((await second.recover(s.id,input.operationId)).status,'unknown');assert.equal(f.wires.length,1);
 await assert.rejects(second.send(s.id,await f.input(s)),/SUMMARY_TASK_UNCONFIRMED/);
 const disk=await f.store.read(s.id);await assert.rejects(f.store.configureSummary(s.id,{operationId:randomUUID(),expectedSessionRevision:disk.revision,expectedSummaryRevision:disk.rollingSummary.revision,timing:'after',selectionId:'model-0',catalogRevision:'v1'},f.plugins),/SUMMARY_OPERATION_UNCONFIRMED/);
});
test('successful provider result lost before summary publication is recoverable once, without continuing story',async t=>{
 const f=await fixture(t),s=await f.seed(await f.create());const write=f.store.write.bind(f.store);let failWrites=false;
 f.store.write=async value=>{if(value.rollingSummary.versions.length)failWrites=true;if(failWrites)throw Error('synthetic disk unavailable');return write(value);};
 const input=await f.input(s);await assert.rejects(f.tasks.send(s.id,input),/synthetic disk unavailable/);f.store.write=write;
 const disk=await f.store.read(s.id);assert.equal(disk.rollingSummary.versions.length,0);const second=createSummaryTasks({store:f.store,plugins:f.plugins,transport:f.transport,...f.hooks});
 const recovered=await second.recover(s.id,input.operationId);assert.equal(recovered.status,'failed');assert.equal((await f.store.read(s.id)).rollingSummary.versions.length,1);assert.equal(f.wires.length,1);
 await second.recover(s.id,input.operationId);assert.equal(f.wires.length,1);
});
test('background starts only after committed reply; next active send waits and page state is irrelevant',async t=>{
 const entered=latch(),release=latch();let call=0;const f=await fixture(t,{onCall:async body=>{if(++call===3){entered.resolve();await release.promise;}return response(body);}});let s=await f.create('after');
 await f.tasks.send(s.id,await f.input(s));s=await f.store.read(s.id);await f.tasks.send(s.id,await f.input(s));await entered.promise;
 s=await f.store.read(s.id);assert.equal(s.turns.length,2);assert.equal(s.rollingSummary.versions.length,0);
 const waiting=f.tasks.send(s.id,await f.input(s,'下一次主动发送'));assert.equal(f.wires.length,3);release.resolve();assert.equal((await waiting).status,'complete');await f.tasks.wait(s.id);
 s=await f.store.read(s.id);assert.equal(s.turns.length,3);assert.equal(s.rollingSummary.versions.at(-1).coveredThrough,2);assert.equal(f.wires.length,5);
});
test('shared slot budget blocks older transports from taking pre-reserved permits; release retains evidence',async t=>{
 const directory=await mkdtemp(join(root,'budget-')),budget=await createSummaryBudget({directory,limit:2});const leases=await budget.reserve(reservationId('s','op'),'s',['summary','story']);
 const legacy=await createControlledProvider({directory,baseURL:'http://127.0.0.1:1/',serviceToken:token,enabled:true,limit:2,fetcher:()=>assert.fail('no spare slot')});
 await assert.rejects(legacy.generate('earth',{sessionId:'other',requestId:'r',selection:catalog[0],messages:[{role:'system',content:'s'},{role:'user',content:'u'}]}),/BUDGET_EXHAUSTED/);
 await budget.dispatched(leases[0],'r',sha('request'));assert.equal(await budget.release(leases[0]),false);assert.equal(await budget.release(leases[1]),true);assert.equal((await budget.status()).remaining,1);
 assert.ok((await readdir(join(directory,'earth/m2-reservations'))).some(x=>x.startsWith('released-')));
});

test('stale revision and double click do not spend; configuration is locked during task and disabled plugin permits core callback',async t=>{
 const entered=latch(),release=latch();let gate=true;const f=await fixture(t,{onCall:async body=>{if(gate){entered.resolve();await release.promise;}return response(body);}}),s=await f.seed(await f.create());
 const input=await f.input(s);await assert.rejects(f.tasks.send(s.id,{...input,expectedSessionRevision:0}),/SUMMARY_TASK_REVISION_CONFLICT/);assert.equal(f.wires.length,0);
 const running=f.tasks.send(s.id,input);await entered.promise;await assert.rejects(f.tasks.send(s.id,input),/SESSION_BUSY/);
 await assert.rejects(f.store.reviseSummary(s.id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedSummaryRevision:s.rollingSummary.revision,text:'修改'},f.plugins),/SESSION_BUSY/);
 release.resolve();await running;gate=false;const disk=await f.store.read(s.id);await f.change('disable',disk);
 const core=await f.tasks.send(s.id,await f.input(disk));assert.equal(core.status,'complete');assert.equal(core.tasks.length,1);assert.equal(core.tasks[0].purpose,'story');
});
test('concurrent two-permit admissions share one budget; released reservations do not erase attempt history',async()=>{
 const directory=await mkdtemp(join(root,'parallel-')),a=await createSummaryBudget({directory,limit:3}),b=await createSummaryBudget({directory,limit:3});
 const result=await Promise.allSettled([a.reserve(reservationId('a','x'),'a',['summary','story']),b.reserve(reservationId('b','x'),'b',['summary','story'])]);
 assert.equal(result.filter(x=>x.status==='fulfilled').length,1);assert.equal((await a.status()).reserved,2);
 const leases=result.find(x=>x.status==='fulfilled').value;for(const l of leases)await a.release(l);assert.equal((await b.status()).remaining,3);
 await assert.rejects(a.reserve(leases[0].groupId,'a',['summary','story']),/BUDGET_OPERATION_ALREADY_RESERVED/);
});
test('background failure does not erase completed story and blocks next automatic summary until explicit action',async t=>{
 let n=0;const f=await fixture(t,{onCall:body=>response(body,{raw:++n===3?'':'合成输出'})});let s=await f.create('after');
 await f.tasks.send(s.id,await f.input(s));s=await f.store.read(s.id);const input=await f.input(s);assert.equal((await f.tasks.send(s.id,input)).status,'complete');await f.tasks.wait(s.id);
 s=await f.store.read(s.id);assert.equal(s.turns.length,2);assert.equal((await f.store.summaryStatus(s.id,f.plugins)).effectivePolicy.reason,'SUMMARY_UPDATE_REQUIRES_EXPLICIT_ACTION');
 await assert.rejects(f.tasks.send(s.id,await f.input(s)),/SUMMARY_UPDATE_REQUIRES_EXPLICIT_ACTION/);assert.equal(f.wires.length,3);
 const update=await f.tasks.update(s.id,await f.input(s,null));assert.equal(update.status,'complete');assert.equal((await f.store.read(s.id)).rollingSummary.versions.length,1);
});

test('release interrupted after move is recovered from retained evidence without touching a reused slot',async()=>{
 const directory=await mkdtemp(join(root,'release-')),budget=await createSummaryBudget({directory,limit:1}),leases=await budget.reserve(reservationId('s','release'),'s',['summary']),lease=leases[0];
 await rename(join(directory,'earth',lease.slot),join(directory,'earth/m2-reservations','released-'+lease.groupId+'-0'));
 const newer=(await budget.reserve(reservationId('new','new'),'new',['story']))[0];
 assert.equal(await budget.wasDispatched(lease),false);assert.equal(await budget.release(lease),true);
 assert.equal(await budget.resolve(newer,{sessionId:'new',purpose:'story'}),join(directory,'earth',newer.slot));assert.equal((await budget.status()).reserved,1);
});

test('recovery of a truncated result after status persistence failed remains a known failure, never a valid summary',async t=>{
 const f=await fixture(t,{onCall:body=>response(body,{raw:'截断原文',finish:'length'})}),s=await f.seed(await f.create()),write=f.store.write.bind(f.store);
 f.store.write=async value=>{if(Object.values(value.summaryTasks?.operations||{}).some(o=>o.status==='failed'))throw Error('lost terminal write');return write(value);};
 const input=await f.input(s);await assert.rejects(f.tasks.send(s.id,input),/lost terminal write/);f.store.write=write;
 const recovered=await f.tasks.recover(s.id,input.operationId);assert.equal(recovered.status,'failed');assert.equal(recovered.tasks[0].output.raw,'截断原文');assert.equal((await f.store.read(s.id)).rollingSummary.versions.length,0);assert.equal(f.wires.length,1);
});
test('interrupted explicit update before task creation is not reported as a completed update on recovery',async t=>{
 const f=await fixture(t),s=await f.seed(await f.create()),write=f.store.write.bind(f.store);let writes=0;
 f.store.write=async value=>{if(value.summaryTasks&&++writes>=2)throw Error('crash after intent');return write(value);};
 const input=await f.input(s,null);await assert.rejects(f.tasks.update(s.id,input),/crash after intent/);f.store.write=write;
 const recovered=await f.tasks.recover(s.id,input.operationId);assert.equal(recovered.status,'failed');assert.equal(f.wires.length,0);assert.equal((await f.transport.status()).reserved,0);assert.equal((await f.store.read(s.id)).rollingSummary.versions.length,0);
});
