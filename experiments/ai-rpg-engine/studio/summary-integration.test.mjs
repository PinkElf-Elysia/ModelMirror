import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {SummarySessionStore} from './summary-session-store.mjs';
import {createSummaryTransport} from './summary-task-provider.mjs';
import {parametersFor} from './model-parameters.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {loadReviewedPlugins,ROLLING_SUMMARY_ID as R,sha,canonical} from '../plugins/catalog.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/rolling-summary-b3/tests/',import.meta.url));await mkdir(root,{recursive:true});
const token='synthetic-service-token-rolling-summary-no-real-key';
const catalog=['google/gemini-3.8-flash','openai/gpt-5.6-luna','deepseek/deepseek-v4-flash0731'].map((model,i)=>({model,name:model,selectionId:'model-'+i,selectionRevision:'v1',available:true,parameters:parametersFor(model)}));
const latch=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
function response(body,{raw='合成输出',finish='stop',status='complete'}={}){
 const sse='data: '+JSON.stringify({model:catalog.find(m=>m.selectionId===body.selectionId).model,choices:[{delta:{content:raw},finish_reason:finish}]})+'\n\ndata: [DONE]\n\n';
 const model=catalog.find(m=>m.selectionId===body.selectionId).model;
 return new Response(JSON.stringify({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:body.sessionId,requestId:body.requestId,selectionId:body.selectionId,selectionRevision:body.selectionRevision,requestedModel:model,actualModel:model,parameters:body.parameters,status,error:status==='complete'?null:'synthetic failure',dispatched:true,retries:0,requestHash:sha(canonical(body)),rawHash:sha(raw),sseHash:sha(sse)}}),{status:200,headers:{'content-type':'application/json'}});
}
import {assemble} from '../card-replica/lib/assembly.mjs';
import {requireContextEvidence} from './summary-assembly.mjs';
import {SUMMARY_INSTRUCTION,SUMMARY_WRAPPER,activeSummary} from './summary-state.mjs';
import {HISTORY_WINDOW_ID as H,MODEL_SELECTOR_ID as M,PLUGIN_ID as B} from '../plugins/catalog.mjs';

async function fixture(t,{limit=40,onCall}={}){
 const directory=await mkdtemp(join(root,'f-')),wires=[];
 const transport=await createSummaryTransport({directory:join(directory,'provider'),baseURL:'http://127.0.0.1:1/',serviceToken:token,enabled:true,limit,fetcher:async(url,request)=>{
  if(request.method==='GET')return new Response(JSON.stringify({models:catalog}),{headers:{'content-type':'application/json'}});
  const body=JSON.parse(request.body);wires.push(body);
  return onCall?onCall(body,wires):response(body,{raw:body.messages[0].content===SUMMARY_INSTRUCTION?'合成摘要-'+wires.length:'剧情原文-'+wires.length+'\n<LongShortMemory>原文\t记忆</LongShortMemory>'});
 }});
 const store=new SummarySessionStore(join(directory,'sessions'),()=>assert.fail('legacy dispatch forbidden'),null,{control:transport});await store.init();
 const plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:id=>store.pluginSession(id),loadCatalog:()=>loadReviewedPlugins({rollingSummary:true})});
 const tasks=store.connectSummary(plugins,transport);
 t.after(async()=>{await writeFile(join(directory,'capture.json'),JSON.stringify({test:t.name,kind:'fake-provider-host-wire',wires},null,2));await plugins.close();});
 const f={directory,wires,transport,store,plugins,tasks,
  async change(action,s,pid=R){const c=await plugins.catalog(),p=c.plugins.find(x=>x.id===pid);return plugins.change(action,{operationId:randomUUID(),pluginId:pid,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},
  async grant(s,pid=R){if(!(await plugins.catalog()).plugins.find(p=>p.id===pid).installed)await f.change('install',null,pid);await f.change('enable',await store.read(s.id),pid);},
  async create(){return store.create({characterText:'姓名：虚构甲\n身份：开局学徒',world:'表世界',params:{max_tokens:16384},mode:'real'});},
  async configure(s,timing='before',model=1){s=await store.read(s.id);return (await store.configureSummary(s.id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedSummaryRevision:s.rollingSummary.revision,timing,selectionId:catalog[model].selectionId,catalogRevision:'v1'},plugins)).session;},
  async input(s,text='继续',operationId=randomUUID()){const x=await store.summaryStatus(s.id,plugins);return {operationId,expectedSessionRevision:x.sessionRevision,expectedContextPolicyRevision:x.effectivePolicy.revision,input:text};},
  async send(s,text='继续'){const op=await store.send(s.id,await f.input(s,text));assert.equal(op.status,'complete',JSON.stringify({status:op.status,error:op.error,tasks:op.tasks.map(t=>({purpose:t.purpose,status:t.status}))}));return store.read(s.id);},
  async edit(s,text){s=await store.read(s.id);return (await store.reviseSummary(s.id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedSummaryRevision:s.rollingSummary.revision,text},plugins)).session;},
  async branch(s,turn,name='新路线'){s=await store.read(s.id);return store.createBranch(s.id,{operationId:randomUUID(),expectedSessionRevision:s.revision,turn,name},plugins);},
  async m1(s,turns=1,includeInitialCharacter=true){s=await store.read(s.id);return (await store.saveHistoryWindow(s.id,{operationId:randomUUID(),expectedSessionRevision:s.revision,expectedConfigRevision:s.historyWindow.revision,turns,includeInitialCharacter},plugins)).session;}
 };return f;
}
const storyWires=f=>f.wires.filter(w=>w.messages[0].content!==SUMMARY_INSTRUCTION);
const summaryWires=f=>f.wires.filter(w=>w.messages[0].content===SUMMARY_INSTRUCTION);

test('B3 zero-plugin actual transport is byte-identical to existing first/continuation assembly',async t=>{
 const f=await fixture(t);let s=await f.create();
 for(let n=0;n<4;n++){
  const input='剧情输入 '+n,expected=assemble({characterText:s.characterText,input,history:s.history,world:s.world}),before=structuredClone(s.history);
  s=await f.send(s,input);assert.deepEqual(f.wires.at(-1).messages,expected.messages);assert.deepEqual(s.history.slice(0,-2),before);assert.equal(s.history.at(-2).content,expected.current);
  assert.equal(s.turns.at(-1).contextPolicy.mode,'full-history');assert.equal(requireContextEvidence(s),true);
 }
 assert.equal(summaryWires(f).length,0);assert.equal((await f.transport.status()).used,4);
});

test('B3 first and rolling summaries use actual raw sources; current wrappers once; manual revision becomes next summary source',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=await f.configure(s);
 for(let n=0;n<3;n++)s=await f.send(s,'第'+n+'次');
 const beforeEdit=structuredClone(s),summary=summaryWires(f)[0],third=storyWires(f)[2];
 assert.deepEqual(JSON.parse(summary.messages[1].content),{previousSummary:null,completedTurns:s.history.slice(0,2)});
 const base=assemble({characterText:s.characterText,input:'第2次',history:s.history.slice(0,4),world:s.world});
 assert.deepEqual(third.messages,[{role:'system',content:base.messages[0].content+SUMMARY_WRAPPER.replace('{summary}',()=>activeSummary(s.rollingSummary).effectiveText)},...s.history.slice(2,4),base.messages.at(-1)]);
 assert.deepEqual(third.parameters,{temperature:0.7,top_p:0.8,max_tokens:16384});assert.deepEqual(summary.parameters,{max_tokens:16384});
 s=await f.edit(s,'玩家修订：约定尚未兑现。');s=await f.send(s,'下一轮');
 const second=JSON.parse(summaryWires(f)[1].messages[1].content);assert.equal(second.previousSummary,'玩家修订：约定尚未兑现。');assert.deepEqual(second.completedTurns,s.history.slice(2,4));
 assert.deepEqual(s.history.slice(0,6),beforeEdit.history);assert.deepEqual(s.turns[2].contextPolicy,beforeEdit.turns[2].contextPolicy);
 assert.equal(s.rollingSummary.versions.length,3);assert.equal(s.rollingSummary.versions[0].raw,'合成摘要-3');assert.equal((await f.transport.status()).used,6);
});

test('B3 M2 ignores both M1 controls; disable restores window then full history without changing stored raw',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s,H);s=await f.m1(s);await f.grant(s);s=await f.configure(s);
 for(let i=0;i<3;i++)s=await f.send(s);
 const wire=storyWires(f).at(-1);assert.deepEqual(wire.messages.slice(1,3),s.history.slice(2,4));assert.ok(!wire.messages[1].content.includes('开局角色资料（原始快照）'));
 const before=structuredClone(s.history);await f.change('disable',s);s=await f.send(s);
 assert.equal(s.turns.at(-1).contextPolicy.mode,'history-window');assert.match(storyWires(f).at(-1).messages[1].content,/开局角色资料/);assert.deepEqual(s.history.slice(0,6),before);
 await f.change('disable',s,H);const full=structuredClone(s.history);s=await f.send(s);assert.deepEqual(storyWires(f).at(-1).messages.slice(1,-1),full);assert.equal(summaryWires(f).length,1);
});

test('B3 branch inherits only point-bound summary/settings, no future text/tasks/grants/budget; continue both routes',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=await f.configure(s);await f.grant(s,H);s=await f.m1(s,2,false);await f.grant(s,B);
 for(let i=0;i<4;i++)s=await f.send(s,'父剧情'+i);
 const point=structuredClone(s.turns[2].contextPolicy.summarySnapshot),history=structuredClone(s.history.slice(0,6));
 s=await f.edit(s,'父路线后来的秘密');s=await f.configure(s,'after',0);s=await f.m1(s,9,true);
 const budget=await f.transport.status();let {session:child}=await f.branch(s,3);
 assert.deepEqual(child.rollingSummary,point);assert.deepEqual(child.history,history);assert.deepEqual(child.historyWindow.config,{turns:2,includeInitialCharacter:false});assert.deepEqual(child.requests,{});assert.equal(child.summaryTasks,undefined);assert.equal((await f.transport.status()).used,budget.used);
 assert.equal((await f.store.summaryStatus(child.id,f.plugins)).effectivePolicy.mode,'full-history');assert.equal((await f.plugins.historyAuthorization(child.id)).enabled,false);
 child=await f.send(child,'分支继续');assert.deepEqual(storyWires(f).at(-1).messages.slice(1,-1),history);
 await f.grant(child);child=await f.send(child,'分支再次继续');assert.ok(!JSON.stringify(f.wires.at(-1)).includes('父路线后来的秘密'));
 const src=JSON.parse(summaryWires(f).at(-1).messages[1].content);assert.equal(src.previousSummary,activeSummary(point).effectiveText);assert.deepEqual(src.completedTurns,history.slice(2,6));
 assert.deepEqual((await f.store.read(s.id)).history,s.history);assert.ok(child.turns.at(-1).requestId!==s.turns.at(-1).requestId);
 s=await f.configure(s,'before',0);const old=structuredClone(s.history);s=await f.send(s,'父路线继续');assert.deepEqual(s.history.slice(0,-2),old);assert.ok(storyWires(f).at(-1).messages[0].content.includes('父路线后来的秘密')===false); // a new summary legitimately replaces the edited old summary
 assert.equal(JSON.parse(summaryWires(f).at(-1).messages[1].content).previousSummary,'父路线后来的秘密');
 await f.grant(child,B);const nested=(await f.branch(child,3,'再次分支')).session;assert.deepEqual(nested.rollingSummary,point);assert.deepEqual(nested.history,history);
});

test('B3 early branch excludes all later summaries; repeated operation creates exactly one route and tamper is rejected',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=await f.configure(s);await f.grant(s,B);for(let i=0;i<3;i++)s=await f.send(s);
 const input={operationId:randomUUID(),expectedSessionRevision:s.revision,turn:1,name:'早期路线'};
 const one=await f.store.createBranch(s.id,input),two=await f.store.createBranch(s.id,input);assert.equal(two.replayed,true);assert.equal(one.session.id,two.session.id);assert.equal(one.session.rollingSummary.versions.length,0);assert.equal(one.session.history.length,2);
 await assert.rejects(f.store.createBranch(s.id,{...input,name:'不同名称'}),/BRANCH_OPERATION_COLLISION/);
 const envelope=await f.store.archive.read(one.session.id);envelope.snapshot.configuration.rollingSummary=structuredClone(s.rollingSummary);envelope.snapshotHash=sha(canonical(envelope.snapshot));envelope.session.provenance.snapshotHash=envelope.snapshotHash;
 await writeFile(f.store.archive.path(one.session.id),JSON.stringify(envelope));await assert.rejects(f.store.read(one.session.id),/BRANCH_SUMMARY_ORIGIN_MISMATCH/);
});

test('B3 story model switch invalidates old admission but preserves summary model/config and shared quota',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=await f.configure(s,'before',0);await f.grant(s,M);s=await f.send(s);s=await f.send(s);
 const stale=await f.input(s),summary=structuredClone(s.rollingSummary),budget=await f.transport.status();
 const op={operationId:randomUUID(),expectedSessionRevision:s.revision,selectionRevision:s.modelState.revision,selectionId:'model-1',catalogRevision:'v1'};
 s=(await f.store.select(s.id,op)).session;assert.deepEqual(s.rollingSummary,summary);assert.equal((await f.transport.status()).used,budget.used);
 await assert.rejects(f.store.send(s.id,stale),/SUMMARY_TASK_REVISION_CONFLICT/);s=await f.send(s);
 assert.equal(storyWires(f).at(-1).selectionId,'model-1');assert.deepEqual(storyWires(f).at(-1).parameters,{max_tokens:16384});assert.equal(summaryWires(f).at(-1).selectionId,'model-0');assert.equal(s.rollingSummary.config.model.selectionId,'model-0');
});

test('B3 background and foreground share coverage; branch keeps request-bound snapshot before later background update',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=await f.configure(s,'after');await f.grant(s,B);
 s=await f.send(s);s=await f.send(s);await f.tasks.wait(s.id);s=await f.store.read(s.id);
 assert.equal(activeSummary(s.rollingSummary).coveredThrough,1);assert.equal(s.turns[1].contextPolicy.summarySnapshot.activeVersionId,null);
 const child=(await f.branch(s,2)).session;assert.equal(child.rollingSummary.activeVersionId,null);
 s=await f.send(s);await f.tasks.wait(s.id);s=await f.store.read(s.id);
 assert.equal(s.turns[2].contextPolicy.coveredThrough,1);assert.deepEqual(storyWires(f)[2].messages.slice(1,3),s.history.slice(2,4));assert.equal(activeSummary(s.rollingSummary).coveredThrough,2);
});

test('B3 missing coverage cannot omit history; corrupted receipt or persistent original is rejected',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=await f.configure(s);s=await f.send(s);s=await f.send(s);
 const status=await f.store.summaryStatus(s.id,f.plugins);await assert.rejects(f.store.prepareHistoryRequest(s.id,{input:'继续',expectedContextPolicyRevision:status.effectivePolicy.revision}),/SUMMARY_POLICY_NOT_READY/);assert.equal(f.wires.length,2);
 const c=structuredClone(s);c.turns[0].contextPolicy.sourceHash=sha('changed');await assert.rejects(f.store.requireRuntime(c),/SUMMARY_TURN_BINDING_INVALID/);
 const h=structuredClone(s);h.history[0].content+='changed';await assert.rejects(f.store.requireRuntime(h),/SUMMARY_TURN_BINDING_INVALID/);
 const rr=structuredClone(s);rr.requests[s.turns[0].requestId].assemblyHash=sha('different');await assert.rejects(f.store.requireRuntime(rr),/SUMMARY_TURN_BINDING_INVALID/);
});

test('B3 pending story shares branch/model/settings lock; late revoked result never joins history',async t=>{
 const started=latch(),release=latch();let block=false;
 const f=await fixture(t,{onCall:async body=>{if(block){started.resolve();await release.promise;}return response(body,{raw:'回合原文'});}});
 let s=await f.create();await f.grant(s);s=await f.configure(s);await f.grant(s,B);await f.grant(s,M);s=await f.send(s);block=true;
 const sending=f.store.send(s.id,await f.input(s));await started.promise;
 await assert.rejects(f.branch(s,1),/SESSION_BUSY/);await assert.rejects(f.configure(s),/SESSION_BUSY/);
 await assert.rejects(f.store.select(s.id,{operationId:randomUUID(),expectedSessionRevision:s.revision,selectionRevision:0,selectionId:'model-1',catalogRevision:'v1'}),/SESSION_BUSY/);
 await f.change('disable',s);release.resolve();assert.equal((await sending).status,'revoked');assert.deepEqual((await f.store.read(s.id)).history,s.history);assert.equal((await f.transport.status()).used,2);
});


test('B3 interrupted actual story publication recovers exactly once after restart without replay',async t=>{
 const f=await fixture(t);let s=await f.create();const write=f.store.write.bind(f.store);
 f.store.write=async value=>{if(value.turns.length)throw Error('synthetic disk unavailable');return write(value);};
 const input=await f.input(s);await assert.rejects(f.store.send(s.id,input),/synthetic disk unavailable/);f.store.write=write;
 assert.equal((await f.store.read(s.id)).turns.length,0);assert.equal(f.wires.length,1);
 const restarted=new SummarySessionStore(f.store.directory,()=>assert.fail('no legacy calls'),null,{control:f.transport});await restarted.init();const tasks=restarted.connectSummary(f.plugins,f.transport);
 assert.equal((await tasks.recover(s.id,input.operationId)).status,'complete');s=await restarted.read(s.id);assert.equal(s.turns.length,1);assert.equal(requireContextEvidence(s),true);
 await tasks.recover(s.id,input.operationId);assert.equal((await restarted.send(s.id,input)).replayed,true);assert.equal((await restarted.read(s.id)).turns.length,1);assert.equal(f.wires.length,1);
});

test('B3 successful summary survives failed actual story and explicit next send uses it without another summary',async t=>{
 let calls=0;const f=await fixture(t,{onCall:body=>response(body,{raw:'原文-'+(++calls),status:calls===4?'failed':'complete'})});let s=await f.create();await f.grant(s);s=await f.configure(s);s=await f.send(s);s=await f.send(s);
 const raw=structuredClone(s.history);const failed=await f.store.send(s.id,await f.input(s));assert.equal(failed.status,'failed');s=await f.store.read(s.id);assert.equal(s.rollingSummary.versions.length,1);assert.deepEqual(s.history,raw);
 s=await f.send(s);assert.equal(s.turns.length,3);assert.equal(summaryWires(f).length,1);assert.equal(f.wires.length,5);assert.equal(storyWires(f).at(-1).messages[0].content.includes('原文-3'),true);
});

test('B3 branches share exhausted quota and atomic publication rejects revocation without half a route',async t=>{
 const f=await fixture(t,{limit:1});let s=await f.create();await f.grant(s,B);s=await f.send(s);
 const publish=f.store.archive.publish.bind(f.store.archive);
 f.store.archive.publish=async(envelope,commit)=>{await f.change('disable',s,B);return publish(envelope,commit);};
 await assert.rejects(f.branch(s,1),/PLUGIN_NOT_AUTHORIZED|PLUGIN_RESULT_STALE|PLUGIN_AUTHORIZATION/);assert.equal((await f.store.archive.list()).length,0);
 f.store.archive.publish=publish;await f.grant(s,B);const child=(await f.branch(s,1)).session;
 const op=await f.store.send(child.id,await f.input(child));assert.equal(op.error,'BUDGET_EXHAUSTED');assert.equal(f.wires.length,1);assert.deepEqual((await f.store.read(child.id)).history,s.history);
});

test('B3 actual unknown story blocks branch/new sends; recovery does not dispatch and retains full input',async t=>{
 let drop=false;const f=await fixture(t,{onCall:body=>{if(drop)throw Error('synthetic dropped response');return response(body);}});let s=await f.create();await f.grant(s,B);s=await f.send(s);drop=true;
 const input=await f.input(s,'需要保留的草稿'),op=await f.store.send(s.id,input);assert.equal(op.status,'unknown');assert.equal(op.input.input,input.input);
 await assert.rejects(f.branch(s,1),/SUMMARY_OPERATION_UNCONFIRMED/);await assert.rejects(f.store.send(s.id,await f.input(s)),/SUMMARY_TASK_UNCONFIRMED/);
 assert.equal((await f.tasks.recover(s.id,input.operationId)).status,'unknown');assert.equal(f.wires.length,2);assert.deepEqual((await f.store.read(s.id)).history,s.history);
});


test('B3 offline sessions cannot enter the connected controlled transport or reserve budget',async t=>{
 const f=await fixture(t);let s=await f.store.create({characterText:'姓名：离线样例',world:'表世界',params:{max_tokens:16384},mode:'offline'});
 await assert.rejects(f.store.send(s.id,await f.input(s)),/SUMMARY_OFFLINE_TRANSPORT_REQUIRED/);
 await f.grant(s);s=await f.configure(s);const input=await f.input(s);delete input.input;
 await assert.rejects(f.tasks.update(s.id,input),/SUMMARY_OFFLINE_TRANSPORT_REQUIRED/);
 assert.equal(f.wires.length,0);const budget=await f.transport.status();assert.equal(budget.used,0);assert.equal(budget.reserved,0);assert.equal((await f.store.read(s.id)).summaryTasks,undefined);
});
