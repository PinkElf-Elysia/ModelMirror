// One explicitly authorized continuation. Preparation never generates; send has no retry.
import assert from 'node:assert/strict';
import {readFile,open} from 'node:fs/promises';
import {sha,canonical} from '../plugins/catalog.mjs';
import {summaryRuntime} from '../studio/summary-runtime.mjs';
import {assembleSummaryRequest,requireContextEvidence} from '../studio/summary-assembly.mjs';
import {assemble} from '../card-replica/lib/assembly.mjs';
const root=new URL('../.rpg04-work/rolling-summary-b5/',import.meta.url),origin='http://127.0.0.1:18473';
const read=async name=>JSON.parse(await readFile(new URL(name,root),'utf8'));
async function save(name,data){const f=await open(new URL(name,root),'wx');try{await f.writeFile(JSON.stringify(data,null,2)+'\n');await f.sync();}finally{await f.close();}}
async function api(path,body){const r=await fetch(origin+'/rpg-app/'+path,{...(body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}),signal:AbortSignal.timeout(body===undefined?15000:260000)});const v=await r.json();assert.ok(r.ok,JSON.stringify({status:r.status,error:v.error}));return v;}
const action=process.argv[2];assert.ok(['prepare','send'].includes(action));
const auth=await read('AUTHORIZATION.json'),review=await read('first-approved/human-review.json'),first=await read('first-input-freeze.json');
assert.equal(auth.limit,4);assert.equal(review.rawHash,'6b66607e1d57f1af2384e5caf15618e9029e9ec3aa9dab0faf10768a2a22b7b0');assert.equal(summaryRuntime.hash,first.runtimeHash);await summaryRuntime.verify();
const id=first.sessionId,s=await api('earth/api/sessions/'+id),h=await api('earth/api/sessions/'+id+'/rolling-summary'),disk=await read('data/earth/'+id+'.json'),status=await api('earth/api/status');
assert.equal(s.turns.length,1);requireContextEvidence(disk);assert.equal(sha(disk.history[1].content),review.rawHash);assert.equal(h.enabled,true);assert.equal(h.config.timing,'before');assert.equal(h.effectivePolicy.mode,'rolling-summary');assert.equal(h.effectivePolicy.needsUpdate,false);assert.equal(h.versions.length,0);assert.deepEqual(h.config.model,first.summaryModel);assert.deepEqual(s.modelSelection.current,first.storyModel);assert.equal(status.budget.limit,4);assert.equal(status.budget.used,1);assert.equal(status.budget.reserved,0);
const catalog=await api('earth/api/sessions/'+id+'/model-catalog');assert.ok(catalog.models.some(m=>m.available&&m.selectionId===first.storyModel.selectionId&&m.selectionRevision===first.storyModel.selectionRevision));
if(action==='prepare'){
 const input='我先把手里的工具放稳，去防潮柜前核对移交标签和登记记录，再请师傅确认这批材料的处理顺序。没有确认前，我不拆动封存的残卷。';
 const built=assembleSummaryRequest(disk,input,h.effectivePolicy),baseline=assemble({characterText:disk.characterText,world:disk.world,input,history:disk.history});
 assert.deepEqual(built.messages,baseline.messages);assert.equal(built.messages.length,4);assert.deepEqual(built.messages.slice(1,3),disk.history);assert.equal(built.messages[0].content,first.messages[0].content);assert.equal(built.contextPolicy.coveredThrough,0);assert.deepEqual(built.contextPolicy.selectedTurns,[1]);assert.deepEqual(built.triggered,[]);
 await save('second-input-freeze.json',{at:new Date().toISOString(),sessionId:id,runtimeHash:summaryRuntime.hash,storyModel:first.storyModel,summaryModel:first.summaryModel,messages:built.messages,messagesHash:sha(canonical(built.messages)),contextPolicy:built.contextPolicy,historyBefore:disk.history,historyBeforeHash:sha(canonical(disk.history)),send:{operationId:'m2-b5-story-2',expectedSessionRevision:h.sessionRevision,expectedContextPolicyRevision:h.effectivePolicy.revision,input}});
 console.log(JSON.stringify({prepared:true,model:first.storyModel.model,messages:4,selectedTurns:[1],summaryNeeded:false,paidCalls:0}));
}else{
 const f=await read('second-input-freeze.json');assert.equal(s.revision,f.send.expectedSessionRevision);assert.equal(h.effectivePolicy.revision,f.send.expectedContextPolicyRevision);assert.deepEqual(disk.history,f.historyBefore);assert.deepEqual(assembleSummaryRequest(disk,f.send.input,h.effectivePolicy).messages,f.messages);
 await save('second-generation-invocation.json',{at:new Date().toISOString(),operationId:f.send.operationId,sessionId:id,messagesHash:f.messagesHash,maximumDispatches:1,noRetry:true,authorization:auth.id});
 const result=await api('earth/api/sessions/'+id+'/send',f.send);await save('second-host-result.json',result);
 console.log(JSON.stringify({turns:result.turns?.length,operation:result.operation?.status,error:result.operation?.error,actualModel:result.turns?.at(-1)?.model?.actualModel,budget:(await api('earth/api/status')).budget}));
}
