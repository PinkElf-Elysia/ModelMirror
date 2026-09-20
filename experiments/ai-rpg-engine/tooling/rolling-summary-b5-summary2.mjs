// Explicit second rolling summary only. Never send a story or retry from this script.
import assert from 'node:assert/strict';
import {readFile,open} from 'node:fs/promises';
import {sha,canonical} from '../plugins/catalog.mjs';
import {summaryRuntime} from '../studio/summary-runtime.mjs';
import {requireContextEvidence} from '../studio/summary-assembly.mjs';
import {SUMMARY_INSTRUCTION,SUMMARY_INSTRUCTION_HASH,summaryUpdateSource} from '../studio/summary-state.mjs';
const root=new URL('../.rpg04-work/rolling-summary-b5/',import.meta.url),origin='http://127.0.0.1:18473';
const read=async name=>JSON.parse(await readFile(new URL(name,root),'utf8'));
async function save(name,data){const f=await open(new URL(name,root),'wx');try{await f.writeFile(JSON.stringify(data,null,2)+'\n');await f.sync();}finally{await f.close();}}
async function api(path,body){const r=await fetch(origin+'/rpg-app/'+path,{...(body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}),signal:AbortSignal.timeout(body===undefined?15000:260000)});const v=await r.json();assert.ok(r.ok,JSON.stringify({status:r.status,error:v.error}));return v;}
const action=process.argv[2];assert.ok(['prepare','update'].includes(action));
const auth=await read('AUTHORIZATION-EXTENSION-1.json'),review=await read('summary-approved/human-review.json'),first=await read('first-input-freeze.json');assert.equal(auth.cumulativeLimit,5);assert.equal(auth.additionalLimit,1);assert.equal(auth.purpose,'summary');assert.equal(auth.noStory,true);assert.equal(summaryRuntime.hash,first.runtimeHash);await summaryRuntime.verify();
const id=first.sessionId,s=await api('earth/api/sessions/'+id),h=await api('earth/api/sessions/'+id+'/rolling-summary'),disk=await read('data/earth/'+id+'.json'),status=await api('earth/api/status');
assert.equal(s.turns.length,3);requireContextEvidence(disk);assert.equal(sha(h.versions[0].effectiveText),review.rawHash);assert.equal(h.enabled,true);assert.equal(h.config.timing,'before');assert.equal(h.effectivePolicy.mode,'rolling-summary');assert.equal(h.effectivePolicy.needsUpdate,true);assert.equal(h.versions.length,1);assert.deepEqual(h.config.model,first.summaryModel);assert.deepEqual(s.modelSelection.current,first.storyModel);assert.equal(status.budget.limit,5);assert.equal(status.budget.used,4);assert.equal(status.budget.reserved,0);
const catalog=await api('earth/api/sessions/'+id+'/rolling-summary/catalog');assert.ok(catalog.models.some(m=>m.available&&m.selectionId===h.config.model.selectionId&&m.selectionRevision===h.config.model.selectionRevision));
const source=summaryUpdateSource(disk);assert.equal(source.previousSummary,h.versions[0].effectiveText);assert.equal(source.targetThrough,2);assert.deepEqual(source.newTurns,[2]);assert.deepEqual(source.source,disk.history.slice(2,4));assert.deepEqual(source.recent,disk.history.slice(4,6));
const messages=[{role:'system',content:SUMMARY_INSTRUCTION},{role:'user',content:JSON.stringify({previousSummary:source.previousSummary,completedTurns:source.source})}];
if(action==='prepare'){
 await save('summary2-input-freeze.json',{at:new Date().toISOString(),sessionId:id,runtimeHash:summaryRuntime.hash,summaryInstructionHash:SUMMARY_INSTRUCTION_HASH,model:h.config.model,source,messages,messagesHash:sha(canonical(messages)),historyBefore:disk.history,turnsBefore:disk.turns,request:{operationId:'m2-b5-summary-2',expectedSessionRevision:h.sessionRevision,expectedContextPolicyRevision:h.effectivePolicy.revision}});
 console.log(JSON.stringify({prepared:true,model:h.config.model.model,parameters:h.config.model.parameters,sourceTurns:[2],excludedTurn:3,previousSummaryHash:sha(source.previousSummary),paidCalls:0}));
}else{
 const f=await read('summary2-input-freeze.json');assert.equal(s.revision,f.request.expectedSessionRevision);assert.equal(h.effectivePolicy.revision,f.request.expectedContextPolicyRevision);assert.deepEqual(disk.history,f.historyBefore);assert.deepEqual(disk.turns,f.turnsBefore);assert.deepEqual(messages,f.messages);assert.deepEqual(h.config.model,f.model);
 await save('summary2-generation-invocation.json',{at:new Date().toISOString(),operationId:f.request.operationId,sessionId:id,messagesHash:f.messagesHash,purpose:'summary',maximumDispatches:1,noStory:true,noRetry:true,authorization:auth.id});
 const result=await api('earth/api/sessions/'+id+'/rolling-summary/update',f.request);await save('summary2-host-result.json',result);
 const after=await api('earth/api/sessions/'+id+'/rolling-summary');console.log(JSON.stringify({operation:result.operation?.status,error:result.operation?.error,summaryVersions:after.versions.length,coveredThrough:after.effectivePolicy.coveredThrough,turns:result.session?.turns?.length,budget:(await api('earth/api/status')).budget}));
}
