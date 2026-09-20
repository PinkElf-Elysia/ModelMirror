// M1 B4 third turn only. Explicit prepare/send, immutable invocation, no retry.
import assert from 'node:assert/strict';
import {readFile,open} from 'node:fs/promises';
import {canonical,sha} from '../plugins/catalog.mjs';
import {historyRuntime} from '../studio/history-runtime.mjs';
import {assembleHistoryRequest} from '../studio/history-assembly.mjs';
import {loadFrozen} from '../card-replica/lib/assembly.mjs';
const root=new URL('../.rpg04-work/history-window-b4/',import.meta.url),origin='http://127.0.0.1:18461';
const read=async p=>JSON.parse(await readFile(new URL(p,root),'utf8'));
async function save(name,data){const file=await open(new URL(name,root),'wx');try{await file.writeFile(JSON.stringify(data,null,2)+'\n');await file.sync();}finally{await file.close();}}
async function api(path,body){const r=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(260000)});const v=await r.json();assert.ok(r.ok,JSON.stringify({status:r.status,error:v.error}));return v;}
const action=process.argv[2];assert.ok(['prepare','send'].includes(action));
const first=await read('first-input-freeze.json'),id=first.sessionId,s=await api('earth/api/sessions/'+id),disk=await read('data/earth/'+id+'.json'),h=await api('earth/api/sessions/'+id+'/history-window');
assert.equal(s.turns.length,2);assert.ok(s.runtime.compatible);assert.equal(historyRuntime.hash,first.runtimeHash);await historyRuntime.verify();assert.deepEqual(s.modelSelection.current,first.model);assert.deepEqual(h.config,{turns:1,includeInitialCharacter:false});assert.equal(h.enabled,true);
const second=await read('second-input-freeze.json'),raw1=await readFile(new URL('data/dispatches/earth/slot-1/response.txt',root),'utf8'),raw=await readFile(new URL('data/dispatches/earth/slot-2/response.txt',root),'utf8');assert.equal(sha(raw1),'cd43dde9acd82b1460853920349cc605369147b48556ddfde1cb6f8ad24f7ea7');assert.equal(sha(raw),'1b1e98ea37d624edf34de3df5b57ead8e1ddd6ced30b55b5a0f91e878b119f5a');assert.deepEqual(disk.history,[first.messages[1],{role:'assistant',content:raw1},second.messages[3],{role:'assistant',content:raw}]);assert.equal(s.turns[1].raw,raw);
const input='我先把靠墙的几册书移到干燥的位置，再去洗手，回来向周芸说明刚才检查到的情况，问她按馆里的流程应该怎样处理。';
const built=assembleHistoryRequest(disk,input,h.effectivePolicy),{prompts}=loadFrozen();
assert.equal(built.messages.length,4);assert.deepEqual(built.messages.slice(0,3),[first.messages[0],...disk.history.slice(2)]);assert.equal(built.messages[3].content,prompts.prefix+'\n'+input+'\n'+prompts.suffix);assert.deepEqual(built.historyPolicy.selectedTurns,[2]);assert.equal(built.historyPolicy.initialSnapshotAdded,false);assert.deepEqual(built.triggered,[]);assert.equal(built.historyPolicy.totalTurns,2);assert.ok(!built.messages.some(m=>m.content===first.messages[1].content||m.content===raw1));assert.ok(!built.messages.some(m=>m.content.includes(disk.characterText)));assert.equal(disk.history.length,4);
const options=await api('earth/api/sessions/'+id+'/model-catalog'),m=options.models.find(x=>x.selectionId===first.model.selectionId);assert.ok(m?.available);assert.equal(m.selectionRevision,first.model.selectionRevision);
const budget=(await api('api/status')).cards.find(c=>c.id==='earth');assert.equal(budget.used,2);assert.equal(budget.limit,3);
if(action==='prepare'){
 const f={at:new Date().toISOString(),sessionId:id,model:s.modelSelection.current,runtimeHash:historyRuntime.hash,priorRawHash:sha(raw),priorHistoryHash:sha(canonical(disk.history)),historyPolicy:built.historyPolicy,messages:built.messages,messagesHash:sha(canonical(built.messages)),send:{requestId:'m1-b4-rpg-output-3',revision:s.revision,expectedSelectionRevision:s.modelSelection.revision,expectedHistoryPolicyRevision:h.historyPolicyRevision,input}};
 await save('third-input-freeze.json',f);console.log(JSON.stringify({prepared:true,sessionId:id,messages:4,selectedTurns:[2],trimmed:true,messagesHash:f.messagesHash,input}));
}else{
 const f=await read('third-input-freeze.json');assert.equal(s.revision,f.send.revision);assert.equal(h.historyPolicyRevision,f.send.expectedHistoryPolicyRevision);assert.deepEqual(f.messages,built.messages);assert.equal(f.messagesHash,sha(canonical(built.messages)));
 await save('third-generation-invocation.json',{at:new Date().toISOString(),sessionId:id,requestId:f.send.requestId,messagesHash:f.messagesHash,noRetry:true,authorization:'rpg-history-window-b4-20260919'});
 const result=await api('earth/api/sessions/'+id+'/send',f.send);await save('third-host-result.json',result);console.log(JSON.stringify({turns:result.turns?.length,actualModel:result.turns?.at(-1)?.model?.actualModel,rawLength:result.turns?.at(-1)?.raw?.length,request:result.requests?.[f.send.requestId]}));
}
