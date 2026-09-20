// Explicit prepare/send only. One generation per invocation, no retries.
import assert from 'node:assert/strict';
import {readFile,open} from 'node:fs/promises';
import {canonical,sha} from '../plugins/catalog.mjs';
import {historyRuntime} from '../studio/history-runtime.mjs';
import {assembleHistoryRequest} from '../studio/history-assembly.mjs';
import {assemble} from '../card-replica/lib/assembly.mjs';
const root=new URL('../.rpg04-work/history-window-b4/',import.meta.url),origin='http://127.0.0.1:18461';
const read=async name=>JSON.parse(await readFile(new URL(name,root),'utf8'));
async function save(name,data){const f=await open(new URL(name,root),'wx');try{await f.writeFile(JSON.stringify(data,null,2)+'\n');await f.sync();}finally{await f.close();}}
async function api(path,body){const r=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(260000)});const v=await r.json();assert.ok(r.ok,JSON.stringify({status:r.status,error:v.error}));return v;}
const action=process.argv[2];assert.ok(['prepare','send'].includes(action));
const expected='0e76f0d2997a66e8fbedb57f97a7a2727bfd2344c08154f978897fafbb8e62c9';assert.equal(historyRuntime.hash,expected);await historyRuntime.verify();
if(action==='prepare'){
 const input={characterText:'姓名：许澄（虚构验收角色）\n开局24岁，在图书馆工作。',world:'表世界',mode:'real',params:{max_tokens:16384}};
 let s;try{const prior=await read('session-created.json');s=await api('earth/api/sessions/'+prior.id);assert.equal(s.turns.length,0);}catch(e){if(e.code!=='ENOENT')throw e;s=await api('earth/api/sessions',input);await save('session-created.json',{id:s.id});}
 for(const pluginId of ['rpg.model-selector','rpg.history-window']){
  let cat=await api('api/plugins'),p=cat.plugins.find(x=>x.id===pluginId);assert.ok(p);
  let base={pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:cat.revision};
  if(!p.installed){await api('api/plugins/'+pluginId+'/install',{...base,operationId:'m1-install-'+pluginId.replaceAll('.','-')});cat=await api('api/plugins');p=cat.plugins.find(x=>x.id===pluginId);base={...base,expectedRegistryRevision:cat.revision};}
  await api('earth/api/sessions/'+s.id+'/plugins/'+pluginId+'/enable',{...base,operationId:'m1-enable-'+pluginId.replaceAll('.','-'),sessionId:s.id,expectedSessionRevision:s.revision,permissions:p.permissions});
 }
 const options=await api('earth/api/sessions/'+s.id+'/model-catalog'),m=options.models.find(x=>x.model==='google/gemini-3.8-flash');assert.ok(m?.available);
 s=(await api('earth/api/sessions/'+s.id+'/model-selection',{operationId:'m1-select-gemini',expectedSessionRevision:s.revision,selectionRevision:s.modelSelection.revision,selectionId:m.selectionId,catalogRevision:m.selectionRevision})).session;
 let policy=await api('earth/api/sessions/'+s.id+'/history-window');
 s=(await api('earth/api/sessions/'+s.id+'/history-window',{operationId:'m1-window-one-no-snapshot',expectedSessionRevision:s.revision,expectedConfigRevision:policy.configRevision,turns:1,includeInitialCharacter:false})).session;
 policy=await api('earth/api/sessions/'+s.id+'/history-window');
 const disk=await read('data/earth/'+s.id+'.json'),query='请开始。',built=assembleHistoryRequest(disk,query,policy.effectivePolicy);
 assert.equal(disk.turns.length,0);assert.deepEqual(built.messages,assemble({characterText:input.characterText,world:input.world,input:query,history:[]}).messages);assert.equal(built.messages.length,2);assert.equal(built.historyPolicy.initialSnapshotAdded,false);assert.deepEqual(built.triggered,[]);
 const f={at:new Date().toISOString(),sessionId:s.id,characterText:input.characterText,characterHash:sha(input.characterText),model:s.modelSelection.current,historyPolicy:built.historyPolicy,effectivePolicy:policy.effectivePolicy,runtimeHash:historyRuntime.hash,messages:built.messages,messagesHash:sha(canonical(built.messages)),send:{requestId:'m1-b4-rpg-output-1',revision:s.revision,expectedSelectionRevision:s.modelSelection.revision,expectedHistoryPolicyRevision:policy.historyPolicyRevision,input:query}};
 await save('first-input-freeze.json',f);console.log(JSON.stringify({prepared:true,sessionId:s.id,model:f.model.model,parameters:f.model.parameters,messages:2,messagesHash:f.messagesHash,policy:built.historyPolicy.config,paidGeneration:0}));
}else{
 const f=await read('first-input-freeze.json'),s=await api('earth/api/sessions/'+f.sessionId),disk=await read('data/earth/'+f.sessionId+'.json'),h=await api('earth/api/sessions/'+f.sessionId+'/history-window');
 assert.equal(s.turns.length,0);assert.equal(s.revision,f.send.revision);assert.equal(h.historyPolicyRevision,f.send.expectedHistoryPolicyRevision);assert.deepEqual(s.modelSelection.current,f.model);assert.deepEqual(assembleHistoryRequest(disk,f.send.input,h.effectivePolicy).messages,f.messages);
 const budget=await api('api/status');assert.equal(budget.cards.find(c=>c.id==='earth').used,0);
 await save('first-generation-invocation.json',{at:new Date().toISOString(),requestId:f.send.requestId,sessionId:f.sessionId,messagesHash:f.messagesHash,noRetry:true,authorization:'rpg-history-window-b4-20260919'});
 const result=await api('earth/api/sessions/'+f.sessionId+'/send',f.send);await save('first-host-result.json',result);
 console.log(JSON.stringify({turns:result.turns?.length,actualModel:result.turns?.at(-1)?.model?.actualModel,rawLength:result.turns?.at(-1)?.raw?.length,request:result.requests?.[f.send.requestId]}));
}
