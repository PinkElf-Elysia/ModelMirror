import {summaryHttp} from './summary-http.mjs';
import {assembleSummaryRequest,commitSummaryStory,requireContextEvidence} from './summary-assembly.mjs';
import {COMPRESSION_INSTRUCTION,overflow} from './summary-compression.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {SummarySessionStore} from './summary-session-store.mjs';
import {createSummaryTasks} from './summary-tasks.mjs';
import {createSummaryTransport} from './summary-task-provider.mjs';
import {createSummaryBudget,reservationId} from './summary-budget.mjs';
import {createControlledProvider} from './controlled-provider.mjs';
import {activeSummary,requireSummaryState} from './summary-state.mjs';
import {PLUGIN_ID as B} from '../plugins/catalog.mjs';
import {parametersFor} from './model-parameters.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {loadReviewedPlugins,ROLLING_SUMMARY_ID as R,sha,canonical} from '../plugins/catalog.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/rolling-summary-overflow/tests/',import.meta.url));await mkdir(root,{recursive:true});
const token='synthetic-service-token-rolling-summary-no-real-key';
const catalog=['google/gemini-3.8-flash','openai/gpt-5.6-luna'].map((model,i)=>({model,name:model,selectionId:'model-'+i,selectionRevision:'v1',available:true,parameters:parametersFor(model)}));
const latch=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
function response(body,{raw='合成输出',finish='stop',status='complete'}={}){
 const sse='data: '+JSON.stringify({model:body.selectionId==='model-1'?catalog[1].model:catalog[0].model,choices:[{delta:{content:raw},finish_reason:finish}]})+'\n\ndata: [DONE]\n\n';
 const model=catalog.find(m=>m.selectionId===body.selectionId).model;
 return new Response(JSON.stringify({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:body.sessionId,requestId:body.requestId,selectionId:body.selectionId,selectionRevision:body.selectionRevision,requestedModel:model,actualModel:model,parameters:body.parameters,status,error:status==='complete'?null:'synthetic failure',dispatched:true,retries:0,requestHash:sha(canonical(body)),rawHash:sha(raw),sseHash:sha(sse)}}),{status:200,headers:{'content-type':'application/json'}});
}
async function fixture(t,{limit=20,onCall,realAssembly=false}={}){
 const directory=await mkdtemp(join(root,'f-')),wires=[];
 const options={directory:join(directory,'provider'),baseURL:'http://127.0.0.1:1/',serviceToken:token,enabled:true,limit,fetcher:async(url,request)=>{
  if(request.method==='GET')return new Response(JSON.stringify({models:catalog}),{headers:{'content-type':'application/json'}});
  const body=JSON.parse(request.body);wires.push(body);return onCall?onCall(body,wires):response(body);
 }};
 const transport=await createSummaryTransport(options),store=new SummarySessionStore(join(directory,'sessions'),()=>assert.fail('no legacy dispatch'),null,{control:transport});await store.init();
 const plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:id=>store.pluginSession(id),loadCatalog:()=>loadReviewedPlugins({rollingSummary:true})});t.after(async()=>{await writeFile(join(directory,'capture.json'),JSON.stringify({test:t.name,kind:'fake-provider-host-wire',wires},null,2));await plugins.close();});
 const hooks={buildStory:async(s,input)=>({messages:[{role:'system',content:'B2 合成剧情回调，非B3装配证据'},{role:'user',content:input}],current:input}),commitStory:async(s,task,result)=>{
  s.history.push({role:'user',content:task.story.current},{role:'assistant',content:result.raw});s.turns.push({requestId:task.requestId,raw:result.raw,rawHash:sha(result.raw),model:{selectionRevision:s.modelState.revision,selection:task.selection}});s.requests[task.requestId]={status:'complete',selectionRevision:s.modelState.revision,selection:task.selection};s.revision++;
 }};
 if(realAssembly){hooks.buildStory=async(s,input,policy)=>({...assembleSummaryRequest(s,input,policy),input});hooks.commitStory=commitSummaryStory;}
 const tasks=realAssembly?store.connectSummary(plugins,transport):createSummaryTasks({store,plugins,transport,...hooks});
 const f={directory,options,wires,transport,store,plugins,tasks,hooks,async change(action,s,pid=R){const c=await plugins.catalog(),p=c.plugins.find(x=>x.id===pid);return plugins.change(action,{operationId:randomUUID(),pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},async create(timing='before',model=0){let s=await store.create({characterText:'姓名：虚构B2角色',world:'表世界',params:{max_tokens:16384},mode:realAssembly?'real':'offline'});if(!(await plugins.catalog()).plugins.find(x=>x.id===R).installed)await f.change('install');await f.change('enable',s);s=(await store.configureSummary(s.id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedSummaryRevision:0,timing,selectionId:catalog[model].selectionId,catalogRevision:'v1'},plugins)).session;return s;},async input(s,input='继续',operationId=randomUUID()){const status=await store.summaryStatus(s.id,plugins);return {operationId,expectedSessionRevision:status.sessionRevision,expectedContextPolicyRevision:status.effectivePolicy.revision,...(input===null?{}:{input})};},async seed(s,count=2){for(let i=0;i<count;i++){const id='seed-'+i,raw='历史原文'+i,selection=structuredClone(s.modelState.current);s.history.push({role:'user',content:'输入'+i},{role:'assistant',content:raw});s.turns.push({requestId:id,raw,rawHash:sha(raw),model:{selectionRevision:0,selection}});s.requests[id]={status:'complete',selectionRevision:0,selection};s.revision++;}await store.write(s);return s;}};return f;
}
const longSummary=Array.from({length:220},(_,i)=>'第'+(i+1)+'日：旅人记录沿途观察与尚未核实的传言，借款状态和承诺日期需保留；事件未解决时不能写成已经完成。').join('\n');
test('Unicode threshold and truncation',()=>{assert.equal(overflow('😀'.repeat(10000),'stop'),false);assert.equal(overflow('😀'.repeat(10001),'stop'),true);assert.equal(overflow('😀'.repeat(10001),'length'),false);});
test('long-history overflow compresses exactly once before story; original and both receipts retained',async t=>{
 const f=await fixture(t,{limit:3,onCall:(b,w)=>response(b,{raw:w.length===1?longSummary:w.length===2?'借款未偿还；承诺第230日交付。':'继续剧情'})}),s=await f.seed(await f.create());
 const before=structuredClone(s.history),input=await f.input(s),r=await f.tasks.send(s.id,input);assert.equal(r.status,'complete');
 assert.deepEqual(r.tasks.map(t=>t.purpose),['summary','compression','story']);assert.equal(f.wires.length,3);
 assert.equal(f.wires[1].messages[0].content,COMPRESSION_INSTRUCTION);assert.deepEqual(JSON.parse(f.wires[1].messages[1].content),{summary:longSummary});
 const d=await f.store.read(s.id),v=d.rollingSummary.versions[0];assert.equal(v.compression.raw,longSummary);assert.equal(v.compression.rawHash,sha(longSummary));assert.equal(v.compression.receipt.requestId,r.tasks[0].requestId);assert.equal(v.receipt.requestId,r.tasks[1].requestId);
 assert.deepEqual(d.history.slice(0,4),before);assert.equal(v.coveredThrough,1);assert.equal(d.rollingSummary.versions.length,1);assert.equal((await f.transport.status()).used,3);assert.equal((await f.tasks.send(s.id,input)).replayed,true);assert.equal(f.wires.length,3);
});
test('exact 10000 Unicode characters skips compression and releases unused permit',async t=>{
 const f=await fixture(t,{limit:3,onCall:(b,w)=>response(b,{raw:w.length===1?'😀'.repeat(10000):'剧情'})}),s=await f.seed(await f.create());
 const r=await f.tasks.send(s.id,await f.input(s));assert.equal(r.status,'complete');assert.equal(f.wires.length,2);assert.equal(r.tasks[0].overflow,undefined);assert.equal((await f.transport.status()).remaining,1);assert.equal((await f.transport.status()).reserved,0);
});
test('foreground reserves three atomically; update reserves two',async t=>{
 const f=await fixture(t,{limit:2,onCall:(b,w)=>response(b,{raw:w.length===1?longSummary:'压缩后'})}),s=await f.seed(await f.create());
 assert.equal((await f.tasks.send(s.id,await f.input(s))).error,'BUDGET_EXHAUSTED');assert.equal(f.wires.length,0);
 assert.equal((await f.tasks.update(s.id,await f.input(s,null))).status,'complete');assert.equal(f.wires.length,2);assert.equal((await f.transport.status()).remaining,0);
});
for(const [name,raw,finish] of [['oversize','字'.repeat(3001),'stop'],['empty','','stop'],['truncated','未完成','length']])test('invalid compression '+name+' pauses without looping',async t=>{
 const f=await fixture(t,{onCall:(b,w)=>response(b,{raw:w.length===1?longSummary:raw,finish:w.length===1?'stop':finish})}),s=await f.seed(await f.create());
 const r=await f.tasks.send(s.id,await f.input(s));assert.equal(r.status,'failed');assert.equal(r.error,name==='empty'?'SUMMARY_TASK_FAILED':'SUMMARY_COMPRESSION_REJECTED');assert.equal(f.wires.length,2);assert.equal(r.tasks[0].output.raw,longSummary);assert.equal(r.tasks[1].output.raw,raw);
 assert.equal((await f.store.read(s.id)).rollingSummary.versions.length,0);assert.equal((await f.transport.status()).reserved,0);await assert.rejects(f.tasks.send(s.id,await f.input(s)),/SUMMARY_UPDATE_REQUIRES_EXPLICIT_ACTION/);
});
test('explicit retry reuses overflow candidate, not the original summary call',async t=>{
 const f=await fixture(t,{onCall:(b,w)=>response(b,{raw:w.length===1?longSummary:w.length===2?'字'.repeat(3001):'最终摘要'})}),s=await f.seed(await f.create());
 await f.tasks.update(s.id,await f.input(s,null));const next=await f.tasks.update(s.id,await f.input(await f.store.read(s.id),null));
 assert.equal(next.status,'complete');assert.equal(next.tasks.length,1);assert.equal(next.tasks[0].purpose,'compression');assert.equal(f.wires.length,3);assert.deepEqual(f.wires[2].messages,f.wires[1].messages);
});
for(const action of ['cancel','revoke'])test(action+' during compression prevents publication',async t=>{
 const entered=latch(),release=latch(),f=await fixture(t,{onCall:async(b,w)=>{if(w.length===2){entered.resolve();await release.promise;}return response(b,{raw:w.length===1?longSummary:'压缩后'});}}),s=await f.seed(await f.create());
 const run=f.tasks.send(s.id,await f.input(s));await entered.promise;if(action==='cancel')f.store.cancel(s.id);else await f.change('disable',await f.store.read(s.id));release.resolve();
 assert.equal((await run).status,action==='cancel'?'cancelled':'revoked');assert.equal(f.wires.length,2);assert.equal((await f.store.read(s.id)).rollingSummary.versions.length,0);
});
test('unknown compression blocks new IDs after restart; recovery never redispatches',async t=>{
 const f=await fixture(t,{onCall:(b,w)=>{if(w.length===2)throw Error('connection lost');return response(b,{raw:longSummary});}}),s=await f.seed(await f.create()),input=await f.input(s),r=await f.tasks.send(s.id,input);assert.equal(r.status,'unknown');
 const store=new SummarySessionStore(f.store.directory,()=>assert.fail('no legacy'));await store.init();const tasks=createSummaryTasks({store,plugins:f.plugins,transport:f.transport,...f.hooks});
 assert.equal((await tasks.recover(s.id,input.operationId)).status,'unknown');assert.equal(f.wires.length,2);await assert.rejects(tasks.update(s.id,await f.input(s,null)),/SUMMARY_TASK_UNCONFIRMED/);
});
test('complete compression receipt recovers after write failure without story replay',async t=>{
 const f=await fixture(t,{onCall:(b,w)=>response(b,{raw:w.length===1?longSummary:'有效压缩'})}),s=await f.seed(await f.create()),input=await f.input(s),write=f.store.write.bind(f.store);let fail=false;
 f.store.write=async x=>{if(x.rollingSummary.versions.length)fail=true;if(fail)throw Error('disk offline');return write(x);};
 await assert.rejects(f.tasks.send(s.id,input),/disk offline/);f.store.write=write;
 const second=createSummaryTasks({store:f.store,plugins:f.plugins,transport:f.transport,...f.hooks}),r=await second.recover(s.id,input.operationId);
 assert.equal(r.status,'failed');assert.equal((await f.store.read(s.id)).rollingSummary.versions[0].compression.raw,longSummary);assert.equal(f.wires.length,2);
});
test('actual assembler consumes compressed text; next rolling update excludes overflow original',async t=>{
 const f=await fixture(t,{limit:10,realAssembly:true,onCall:(b,w)=>response(b,{raw:w.length===3?longSummary:w.length===4?'借款未偿还；约定第230日交付。':'剧情原文'})});let s=await f.create();
 for(let i=0;i<4;i++){assert.equal((await f.tasks.send(s.id,await f.input(s,'玩家输入'+i))).status,'complete');s=await f.store.read(s.id);}
 requireContextEvidence(s);assert.equal(f.wires.length,7);assert.ok(f.wires[4].messages[0].content.includes('借款未偿还；约定第230日交付。'));assert.ok(!f.wires[4].messages[0].content.includes(longSummary));
 assert.equal(JSON.parse(f.wires[5].messages[1].content).previousSummary,'借款未偿还；约定第230日交付。');assert.deepEqual(f.wires[4].messages.slice(1,3),s.history.slice(2,4));
});
test('background overflow finishes before next send',async t=>{
 const entered=latch(),release=latch(),f=await fixture(t,{onCall:async(b,w)=>{if(w.length===4){entered.resolve();await release.promise;}return response(b,{raw:w.length===3?longSummary:'有效文字'});}});let s=await f.create('after');
 await f.tasks.send(s.id,await f.input(s));s=await f.store.read(s.id);await f.tasks.send(s.id,await f.input(s));await entered.promise;
 s=await f.store.read(s.id);assert.equal(s.turns.length,2);assert.equal(s.rollingSummary.versions.length,0);const waiting=f.tasks.send(s.id,await f.input(s));release.resolve();assert.equal((await waiting).status,'complete');await f.tasks.wait(s.id);assert.equal((await f.store.read(s.id)).rollingSummary.versions[0].compression.raw,longSummary);
});


test('truncated primary output above threshold never triggers compression',async t=>{
 const f=await fixture(t,{onCall:b=>response(b,{raw:longSummary,finish:'length'})}),s=await f.seed(await f.create());
 const r=await f.tasks.update(s.id,await f.input(s,null));assert.equal(r.status,'failed');assert.equal(f.wires.length,1);assert.equal((await f.store.read(s.id)).rollingSummary.versions.length,0);
});

test('50 complete synthetic turns reach source intact; 3000 Unicode compressed characters accepted',async t=>{
 const f=await fixture(t,{onCall:(b,w)=>response(b,{raw:w.length===1?longSummary:'😀'.repeat(3000)})}),s=await f.seed(await f.create(),50),before=structuredClone(s.history);
 const r=await f.tasks.update(s.id,await f.input(s,null));assert.equal(r.status,'complete');
 assert.deepEqual(JSON.parse(f.wires[0].messages[1].content).completedTurns,before.slice(0,98));assert.equal(f.wires.length,2);
 const d=await f.store.read(s.id);assert.equal(activeSummary(d.rollingSummary).coveredThrough,49);assert.equal(Array.from(activeSummary(d.rollingSummary).raw).length,3000);assert.deepEqual(d.history,before);
});

test('failed compression preserves the prior effective summary and all source history',async t=>{
 const f=await fixture(t,{realAssembly:true,onCall:(b,w)=>response(b,{raw:w.length===5?longSummary:w.length===6?'字'.repeat(3001):'已确认摘要或剧情'})});let s=await f.create();
 for(let i=0;i<3;i++){assert.equal((await f.tasks.send(s.id,await f.input(s))).status,'complete');s=await f.store.read(s.id);}
 const old=structuredClone(s.rollingSummary),history=structuredClone(s.history);
 assert.equal((await f.tasks.send(s.id,await f.input(s))).status,'failed');const d=await f.store.read(s.id);assert.deepEqual(d.rollingSummary,old);assert.deepEqual(d.history,history);assert.equal(f.wires.length,6);
});

test('crash after saved overflow before compression requires explicit action and reuses saved candidate',async t=>{
 const f=await fixture(t,{onCall:(b,w)=>response(b,{raw:w.length===1?longSummary:'压缩已恢复'})}),s=await f.seed(await f.create()),input=await f.input(s,null),write=f.store.write.bind(f.store);let fail=false;
 f.store.write=async x=>{if(Object.values(x.summaryTasks?.operations||{}).some(o=>o.tasks.some(t=>t.purpose==='compression')))fail=true;if(fail)throw Error('disk offline');return write(x);};
 await assert.rejects(f.tasks.update(s.id,input),/disk offline/);f.store.write=write;assert.equal(f.wires.length,1);
 const second=createSummaryTasks({store:f.store,plugins:f.plugins,transport:f.transport,...f.hooks});
 assert.equal((await second.recover(s.id,input.operationId)).status,'failed');assert.equal(f.wires.length,1);
 await assert.rejects(second.send(s.id,await f.input(s)),/SUMMARY_UPDATE_REQUIRES_EXPLICIT_ACTION/);
 const r=await second.update(s.id,await f.input(s,null));assert.equal(r.status,'complete');assert.deepEqual(r.tasks.map(t=>t.purpose),['compression']);assert.equal(f.wires.length,2);assert.equal((await f.transport.status()).reserved,0);
});

test('two overflow updates retain point-bound evidence in branch without future data or tasks',async t=>{
 const f=await fixture(t,{realAssembly:true,onCall:(b,w)=>response(b,{raw:[3,6].includes(w.length)?longSummary+'\n第'+w.length+'次候选':w.length===4?'第一份有效压缩':w.length===7?'父路线后来的压缩':'剧情原文'})});let s=await f.create();
 await f.change('install',null,B);await f.change('enable',s,B);
 for(let i=0;i<4;i++){assert.equal((await f.tasks.send(s.id,await f.input(s))).status,'complete');s=await f.store.read(s.id);}
 assert.equal(f.wires.length,8);assert.equal(s.rollingSummary.versions.length,2);assert.equal(activeSummary(s.rollingSummary).raw,'父路线后来的压缩');
 const point=structuredClone(s.turns[2].contextPolicy.summarySnapshot),budget=await f.transport.status();
 const {session:child}=await f.store.createBranch(s.id,{operationId:randomUUID(),expectedSessionRevision:s.revision,turn:3,name:'压缩分支'},f.plugins);
 assert.deepEqual(child.rollingSummary,point);assert.equal(child.summaryTasks,undefined);assert.equal((await f.plugins.summaryAuthorization(child.id)).enabled,false);assert.deepEqual(child.history,s.history.slice(0,6));
 assert.equal(activeSummary(child.rollingSummary).raw,'第一份有效压缩');assert.ok(activeSummary(child.rollingSummary).compression.raw.includes('第3次候选'));assert.ok(!JSON.stringify(child).includes('父路线后来的压缩'));assert.equal((await f.transport.status()).used,budget.used);requireSummaryState(child);
});


test('HTTP projection exposes recovery cost and both raw outputs without provider request data',async t=>{
 const f=await fixture(t,{onCall:(b,w)=>response(b,{raw:w.length===1?longSummary:w.length===2?'字'.repeat(3001):'有效压缩'})}),s=await f.seed(await f.create());
 assert.deepEqual((await f.store.summaryStatus(s.id,f.plugins)).updatePlan,{kind:'summary',maxCalls:2});
 const op=await f.tasks.update(s.id,await f.input(s,null));let projected;
 const read=async()=>summaryHttp({route:'api/sessions/'+s.id+'/rolling-summary',method:'GET',query:new URLSearchParams(),store:f.store,plugins:f.plugins,send:(_code,value)=>{projected=value;}});
 await read();assert.deepEqual(projected.updatePlan,{kind:'compression',maxCalls:1});assert.equal(projected.compressionAttempts[0].raw,longSummary);assert.equal(projected.compressionAttempts[0].compressedRaw,'字'.repeat(3001));
 assert.equal(projected.taskState.operations[op.input.operationId].purpose,'compression');assert.equal(projected.taskState.operations[op.input.operationId].overflow,true);
 assert.ok(!JSON.stringify(projected.taskState).includes('messages'));assert.ok(!JSON.stringify(projected.compressionAttempts).includes('serviceToken'));
 await f.tasks.update(s.id,await f.input(s,null));await read();assert.equal(projected.updatePlan,null);assert.equal(projected.versions[0].compression.raw,longSummary);
});
