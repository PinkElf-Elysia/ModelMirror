// Approved second M3 pair; no model configuration changes, retries or extra calls.
import assert from 'node:assert/strict';
import {readFile,open} from 'node:fs/promises';
import {canonical,sha} from '../plugins/catalog.mjs';
import {memoryRuntime} from '../studio/memory-runtime.mjs';
import {assembleMemoryRequest,requireMemoryEvidence} from '../studio/memory-assembly.mjs';
import {assemble,activeSystem} from '../card-replica/lib/assembly.mjs';
const root=new URL('../.rpg04-work/memory-palace-b5/',import.meta.url),origin='http://127.0.0.1:18497';
const read=async n=>JSON.parse(await readFile(new URL(n,root),'utf8'));
async function save(n,v){const f=await open(new URL(n,root),'wx');try{await f.writeFile(JSON.stringify(v,null,2)+'\n');await f.sync();}finally{await f.close();}}
async function api(path,body){const r=await fetch(origin+'/rpg-app/'+path,{...(body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}),signal:AbortSignal.timeout(260000)});const v=await r.json();assert.ok(r.ok,JSON.stringify({httpStatus:r.status,error:v.error}));return v;}
const a=await read('AUTHORIZATION.json'),first=await read('first-input-freeze.json'),id=first.sessionId,base='earth/api/sessions/'+id;
assert.equal(a.generationLimit,4);assert.equal(a.noRetry,true);await memoryRuntime.verify();assert.equal(memoryRuntime.hash,first.runtimeHash);
const s=await api(base),disk=await read('data/earth/'+id+'.json'),h=await api(base+'/memory-palace'),status=await api('earth/api/status');
requireMemoryEvidence(disk);assert.equal(disk.turns.length,1);assert.equal(h.processedThrough,1);assert.equal(h.busy,false);assert.equal(h.blocked,null);assert.equal(h.maxCalls,2);assert.equal(status.budget.used,2);assert.equal(status.budget.reserved,0);assert.equal(status.budget.remaining,2);
assert.deepEqual(s.modelSelection.current,first.storyModel);assert.deepEqual(h.config.model,first.memoryModel);assert.equal((await api(base+'/rolling-summary')).enabled,false);
for(const path of ['/model-catalog','/memory-palace/catalog']){const model=(await api(base+path)).models.find(m=>m.model===a.storyModel);assert.ok(model?.available);assert.equal(model.selectionRevision,first.memoryModel.selectionRevision);}
const action=process.argv[2];
if(action==='prepare'){
 const query='我先收好手机，专心完成眼前这一小段揭页，拿不准的地方就请周师傅看一眼。等可以停手休息时，再给阿宁回复：“旧信先别拆，我明天下午三点可能赶不及，改到四点方便吗？”然后等她答复。';
 const built=assembleMemoryRequest(disk,query,h.effectivePolicy),original=assemble({characterText:disk.characterText,world:disk.world,input:query,history:disk.history});
 assert.equal(built.messages.length,4);assert.equal(built.messages.filter(m=>m.role==='system').length,1);assert.ok(built.messages[0].content.startsWith(activeSystem+'\n\n【记忆宫殿·历史资料】'));assert.deepEqual(built.messages.slice(1),original.messages.slice(1));assert.equal(built.memoryPolicy.recall.results.filter(r=>r.selected).length,4);
 await save('group1-user-review.json',{at:new Date().toISOString(),decision:'确认',scope:'first story and memory output; proceed with remaining2 calls',noAdditionalQuota:true});
 const f={at:new Date().toISOString(),sessionId:id,runtimeHash:memoryRuntime.hash,storyModel:first.storyModel,memoryModel:first.memoryModel,historyBefore:disk.history,historyHash:sha(canonical(disk.history)),entriesBefore:h.entries,policy:h.effectivePolicy,memoryPolicy:built.memoryPolicy,messages:built.messages,messagesHash:sha(canonical(built.messages)),maxCalls:2,send:{operationId:'m3-b5-group-2',expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,expectedContextPolicyRevision:h.effectivePolicy.revision,input:query}};
 await save('second-input-freeze.json',f);console.log(JSON.stringify({prepared:true,sessionId:id,messageCount:4,selectedEntries:4,messagesHash:f.messagesHash,noHistoryEviction:true,providerCalls:0}));
}else if(action==='send'){
 const f=await read('second-input-freeze.json');assert.equal(f.runtimeHash,memoryRuntime.hash);assert.equal(h.sessionRevision,f.send.expectedSessionRevision);assert.equal(h.memoryRevision,f.send.expectedMemoryRevision);assert.equal(h.effectivePolicy.revision,f.send.expectedContextPolicyRevision);assert.deepEqual(disk.history,f.historyBefore);assert.deepEqual(h.entries,f.entriesBefore);assert.deepEqual(assembleMemoryRequest(disk,f.send.input,h.effectivePolicy).messages,f.messages);
 await save('second-generation-invocation.json',{at:new Date().toISOString(),operationId:f.send.operationId,sessionId:id,messagesHash:f.messagesHash,maximumDispatches:2,noRetry:true,authorization:a.id});
 const result=await api(base+'/send',f.send);await save('second-host-result.json',result);console.log(JSON.stringify({turns:result.turns.length,operation:result.requests[f.send.operationId],backgroundMemory:'inspect original operation; never resend'}));
}else throw Error('UNSUPPORTED_ACTION');
