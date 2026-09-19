// B5 Gemini parent continuation: explicit prepare, then one-shot send. No retries.
import assert from 'node:assert/strict';
import {readFile,writeFile,open} from 'node:fs/promises';
import {assemble,loadFrozen} from '../card-replica/lib/assembly.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {modelRuntime} from '../studio/model-runtime.mjs';
const root=new URL('../.rpg04-work/model-selector-b5/',import.meta.url),origin='http://127.0.0.1:18449';
const load=async name=>JSON.parse(await readFile(new URL(name,root),'utf8'));
const save=async(name,data)=>{const file=await open(new URL(name,root),'wx');try{await file.writeFile(JSON.stringify(data,null,2));await file.sync();}finally{await file.close();}};
async function api(path,body){const r=await fetch(origin+'/rpg-app/earth/api/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(260000)});const data=await r.json();assert.ok(r.ok,JSON.stringify({status:r.status,error:data.error}));return data;}
const first=await load('input-freeze-luna.json'),id=first.sessionId;
const action=process.argv[2];assert.ok(['prepare','send'].includes(action));
let s=await api('sessions/'+id);assert.equal(s.turns.length,1);assert.equal(s.revision,1);assert.ok(s.runtime.compatible);assert.equal(modelRuntime.hash,first.runtimeHash);
const disk=await load('data/earth/'+id+'.json'),luna=await readFile(new URL('data/dispatches/earth/slot-1/response.txt',root),'utf8');
assert.equal(disk.history.length,2);assert.deepEqual(disk.history,[first.messages[1],{role:'assistant',content:luna}]);assert.equal(s.turns[0].raw,luna);assert.equal(s.turns[0].model.actualModel,'openai/gpt-5.6-luna');assert.equal(disk.characterText, (await load('input-freeze.json')).input.characterText);
const input='我对门外的人说：“车先按原计划安排。我先吃早餐，麻烦把今天的行程和访客资料拿来，我想确认一下会议的时间。”';
const a=assemble({characterText:disk.characterText,world:disk.world,input,history:disk.history});const {prompts}=loadFrozen();assert.equal(a.messages.length,4);assert.deepEqual(a.messages.slice(0,3),[first.messages[0],...disk.history]);assert.equal(a.messages[3].content,prompts.prefix+'\n'+input+'\n'+prompts.suffix);assert.deepEqual(a.triggered,[]);
if(action==='prepare'){
 const options=await api('sessions/'+id+'/model-catalog');const m=options.models.find(x=>x.model==='google/gemini-3.8-flash');assert.ok(m?.available);
 s=(await api('sessions/'+id+'/model-selection',{operationId:'b5-select-gemini',expectedSessionRevision:s.revision,selectionRevision:s.modelSelection.revision,selectionId:m.selectionId,catalogRevision:m.selectionRevision})).session;
 assert.deepEqual(s.modelSelection.current.parameters,{temperature:.7,top_p:.8,max_tokens:16384});assert.equal(s.turns[0].raw,luna);assert.equal(s.turns[0].model.actualModel,'openai/gpt-5.6-luna');
 const f={at:new Date().toISOString(),sessionId:id,model:s.modelSelection.current,selectionRevision:s.modelSelection.revision,runtimeHash:modelRuntime.hash,messages:a.messages,messagesHash:sha(canonical(a.messages)),priorRawHash:sha(luna),historyHash:sha(canonical(disk.history)),worldbook:'disabled',send:{requestId:'b5-gemini-output-2',revision:s.revision,expectedSelectionRevision:s.modelSelection.revision,input}};
 await save('input-freeze-gemini.json',f);console.log(JSON.stringify({prepared:true,sessionId:id,model:f.model.model,parameters:f.model.parameters,messages:4,prefixSuffixCopiesThisTurn:1,messagesHash:f.messagesHash,priorRawHash:f.priorRawHash}));
}else{
 const f=await load('input-freeze-gemini.json');assert.deepEqual(f.model,s.modelSelection.current);assert.equal(f.selectionRevision,s.modelSelection.revision);assert.equal(f.runtimeHash,modelRuntime.hash);assert.deepEqual(f.messages,a.messages);assert.equal(f.messagesHash,sha(canonical(a.messages)));
 await save('gemini-generation-invocation.json',{at:new Date().toISOString(),sessionId:id,requestId:f.send.requestId,messagesHash:f.messagesHash,parameters:f.model.parameters,retry:false});
 const result=await api('sessions/'+id+'/send',f.send);await save('gemini-host-result.json',result);
 console.log(JSON.stringify({sessionId:id,turns:result.turns?.length,actualModel:result.turns?.at(-1)?.model?.actualModel,rawUtf16Length:result.turns?.at(-1)?.raw?.length,error:result.error}));
}
