// M1 B4 second turn only. Explicit prepare/send, immutable invocation, no retry.
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
assert.equal(s.turns.length,1);assert.ok(s.runtime.compatible);assert.equal(historyRuntime.hash,first.runtimeHash);await historyRuntime.verify();assert.deepEqual(s.modelSelection.current,first.model);assert.deepEqual(h.config,{turns:1,includeInitialCharacter:false});assert.equal(h.enabled,true);
const raw=await readFile(new URL('data/dispatches/earth/slot-1/response.txt',root),'utf8');assert.equal(sha(raw),'cd43dde9acd82b1460853920349cc605369147b48556ddfde1cb6f8ad24f7ea7');assert.deepEqual(disk.history,[first.messages[1],{role:'assistant',content:raw}]);assert.equal(s.turns[0].raw,raw);
const input='我应声答应周芸，先把推车上最后三本文献逐一核对索书号、放回对应书架，再弯腰检查底层靠墙的板子，确认受潮的位置。';
const built=assembleHistoryRequest(disk,input,h.effectivePolicy),{prompts}=loadFrozen();
assert.equal(built.messages.length,4);assert.deepEqual(built.messages.slice(0,3),[first.messages[0],...disk.history]);assert.equal(built.messages[3].content,prompts.prefix+'\n'+input+'\n'+prompts.suffix);assert.deepEqual(built.historyPolicy.selectedTurns,[1]);assert.equal(built.historyPolicy.initialSnapshotAdded,false);assert.deepEqual(built.triggered,[]);
const options=await api('earth/api/sessions/'+id+'/model-catalog'),m=options.models.find(x=>x.selectionId===first.model.selectionId);assert.ok(m?.available);assert.equal(m.selectionRevision,first.model.selectionRevision);
const budget=(await api('api/status')).cards.find(c=>c.id==='earth');assert.equal(budget.used,1);assert.equal(budget.limit,3);
if(action==='prepare'){
 const f={at:new Date().toISOString(),sessionId:id,model:s.modelSelection.current,runtimeHash:historyRuntime.hash,priorRawHash:sha(raw),priorHistoryHash:sha(canonical(disk.history)),historyPolicy:built.historyPolicy,messages:built.messages,messagesHash:sha(canonical(built.messages)),send:{requestId:'m1-b4-rpg-output-2',revision:s.revision,expectedSelectionRevision:s.modelSelection.revision,expectedHistoryPolicyRevision:h.historyPolicyRevision,input}};
 await save('second-input-freeze.json',f);console.log(JSON.stringify({prepared:true,sessionId:id,messages:4,selectedTurns:[1],trimmed:false,messagesHash:f.messagesHash,input}));
}else{
 const f=await read('second-input-freeze.json');assert.equal(s.revision,f.send.revision);assert.equal(h.historyPolicyRevision,f.send.expectedHistoryPolicyRevision);assert.deepEqual(f.messages,built.messages);assert.equal(f.messagesHash,sha(canonical(built.messages)));
 await save('second-generation-invocation.json',{at:new Date().toISOString(),sessionId:id,requestId:f.send.requestId,messagesHash:f.messagesHash,noRetry:true,authorization:'rpg-history-window-b4-20260919'});
 const result=await api('earth/api/sessions/'+id+'/send',f.send);await save('second-host-result.json',result);console.log(JSON.stringify({turns:result.turns?.length,actualModel:result.turns?.at(-1)?.model?.actualModel,rawLength:result.turns?.at(-1)?.raw?.length,request:result.requests?.[f.send.requestId]}));
}
