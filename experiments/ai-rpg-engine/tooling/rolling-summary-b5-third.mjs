// Final authorized story: use approved summary, retain turn 2, never retry or auto-update.
import assert from 'node:assert/strict';
import {readFile,open} from 'node:fs/promises';
import {sha,canonical} from '../plugins/catalog.mjs';
import {summaryRuntime} from '../studio/summary-runtime.mjs';
import {assembleSummaryRequest,requireContextEvidence} from '../studio/summary-assembly.mjs';
import {SUMMARY_WRAPPER,activeSummary} from '../studio/summary-state.mjs';
import {assemble,activeSystem} from '../card-replica/lib/assembly.mjs';
const root=new URL('../.rpg04-work/rolling-summary-b5/',import.meta.url),origin='http://127.0.0.1:18473';
const read=async name=>JSON.parse(await readFile(new URL(name,root),'utf8'));
async function save(name,data){const f=await open(new URL(name,root),'wx');try{await f.writeFile(JSON.stringify(data,null,2)+'\n');await f.sync();}finally{await f.close();}}
async function api(path,body){const r=await fetch(origin+'/rpg-app/'+path,{...(body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}),signal:AbortSignal.timeout(body===undefined?15000:260000)});const v=await r.json();assert.ok(r.ok,JSON.stringify({status:r.status,error:v.error}));return v;}
const action=process.argv[2];assert.ok(['prepare','send'].includes(action));const auth=await read('AUTHORIZATION.json'),review=await read('summary-approved/human-review.json'),first=await read('first-input-freeze.json');assert.equal(auth.limit,4);assert.equal(summaryRuntime.hash,first.runtimeHash);await summaryRuntime.verify();
const id=first.sessionId,s=await api('earth/api/sessions/'+id),h=await api('earth/api/sessions/'+id+'/rolling-summary'),disk=await read('data/earth/'+id+'.json'),status=await api('earth/api/status'),summary=activeSummary(disk.rollingSummary);
assert.equal(s.turns.length,2);requireContextEvidence(disk);assert.equal(sha(summary.effectiveText),review.rawHash);assert.equal(summary.coveredThrough,1);assert.equal(h.enabled,true);assert.equal(h.config.timing,'before');assert.equal(h.effectivePolicy.mode,'rolling-summary');assert.equal(h.effectivePolicy.needsUpdate,false);assert.equal(h.versions.length,1);assert.deepEqual(h.config.model,first.summaryModel);assert.deepEqual(s.modelSelection.current,first.storyModel);assert.equal(status.budget.limit,4);assert.equal(status.budget.used,3);assert.equal(status.budget.reserved,0);
const catalog=await api('earth/api/sessions/'+id+'/model-catalog');assert.ok(catalog.models.some(m=>m.available&&m.selectionId===first.storyModel.selectionId&&m.selectionRevision===first.storyModel.selectionRevision));
if(action==='prepare'){
 const input='我按师傅刚才交代的顺序做好防护，先检查除尘台的设备，再给待处理材料拍照、编号、记录。遇到认不准的纸张，我先单独放好，不擅自拆分。忙完眼前这一步，我想请教他，像我这样容易手上用力过重的新手，平时该怎样练习。';
 const built=assembleSummaryRequest(disk,input,h.effectivePolicy),baseline=assemble({characterText:disk.characterText,world:disk.world,input,history:disk.history});
 assert.equal(built.messages.length,4);assert.deepEqual(built.messages.slice(1,3),disk.history.slice(2,4));assert.equal(built.messages[0].content,activeSystem+SUMMARY_WRAPPER.replace('{summary}',()=>summary.effectiveText));assert.equal(built.messages[3].content,baseline.current);assert.equal(built.contextPolicy.coveredThrough,1);assert.deepEqual(built.contextPolicy.selectedTurns,[2]);assert.deepEqual(built.triggered,[]);assert.equal(built.messages.filter(m=>m.role==='system').length,1);
 await save('third-input-freeze.json',{at:new Date().toISOString(),sessionId:id,runtimeHash:summaryRuntime.hash,storyModel:first.storyModel,summaryModel:first.summaryModel,approvedSummary:summary,messages:built.messages,messagesHash:sha(canonical(built.messages)),contextPolicy:built.contextPolicy,historyBefore:disk.history,turnsBefore:disk.turns,send:{operationId:'m2-b5-story-3',expectedSessionRevision:h.sessionRevision,expectedContextPolicyRevision:h.effectivePolicy.revision,input}});
 console.log(JSON.stringify({prepared:true,messages:4,summaryCoveredThrough:1,rawSelectedTurns:[2],summaryUpdateNeeded:false,paidCalls:0}));
}else{
 const f=await read('third-input-freeze.json');assert.equal(s.revision,f.send.expectedSessionRevision);assert.equal(h.effectivePolicy.revision,f.send.expectedContextPolicyRevision);assert.deepEqual(disk.history,f.historyBefore);assert.deepEqual(disk.turns,f.turnsBefore);assert.deepEqual(summary,f.approvedSummary);assert.deepEqual(assembleSummaryRequest(disk,f.send.input,h.effectivePolicy).messages,f.messages);
 await save('third-generation-invocation.json',{at:new Date().toISOString(),operationId:f.send.operationId,sessionId:id,messagesHash:f.messagesHash,maximumDispatches:1,noRetry:true,authorization:auth.id});
 const result=await api('earth/api/sessions/'+id+'/send',f.send);await save('third-host-result.json',result);console.log(JSON.stringify({turns:result.turns?.length,operation:result.operation?.status,error:result.operation?.error,actualModel:result.turns?.at(-1)?.model?.actualModel,budget:(await api('earth/api/status')).budget}));
}
